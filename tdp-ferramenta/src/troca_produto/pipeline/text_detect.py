"""Detecção das menções faladas ao produto antigo.

REGRA DE OURO: nunca liste as menções com regex fixo. O ASR entorta o nome
("sugarbind" → "sugar bean", "sugarbent"; "glicovita" → "glykavit"). Aqui o
match é fuzzy + fonético, em janelas de 1 a 3 palavras, com limiar ~72.
Depois disso, filtramos os falsos positivos de contexto ("blood sugar X").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

DEFAULT_THRESHOLD = 72.0
DEFAULT_MAX_WORDS = 3

#: Contextos que costumam gerar falso positivo em VSL de glicemia.
DEFAULT_STOP_PHRASES = (
    "blood sugar",
    "sugar level",
    "sugar levels",
    "sugar spike",
    "sugar spikes",
    "blood pressure",
)

_WORD_RE = re.compile(r"[a-z0-9']+")


# ── normalização ────────────────────────────────────────────────────────────
def normalize(text: str) -> str:
    """minúsculas, sem acento, sem pontuação, espaços colapsados."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.lower().replace("’", "'")
    return " ".join(_WORD_RE.findall(lowered))


def _collapse(text: str) -> str:
    """Normaliza e junta tudo: 'Sugar Bind' → 'sugarbind'."""
    return normalize(text).replace(" ", "").replace("'", "")


_VOWELS = set("aeiou")
_DIGRAPHS = (
    ("ph", "f"),
    ("gh", "g"),
    ("ck", "k"),
    ("sch", "sk"),
    ("sh", "x"),
    ("ch", "x"),
    ("th", "t"),
    ("wh", "w"),
    ("qu", "k"),
    ("kn", "n"),
    ("wr", "r"),
    ("ll", "l"),
    ("ss", "s"),
    ("tt", "t"),
)


def phonetic_key(text: str) -> str:
    """Chave fonética 'metaphone-lite'.

    Feita pra colapsar exatamente os erros que o Whisper comete em nome de
    marca inventada: 'glykavit' e 'glicovita' viram a mesma chave.
    """
    word = _collapse(text)
    if not word:
        return ""
    for src, dst in _DIGRAPHS:
        word = word.replace(src, dst)
    out: list[str] = []
    for i, ch in enumerate(word):
        nxt = word[i + 1] if i + 1 < len(word) else ""
        if ch in _VOWELS or ch == "y":
            # vogal só conta quando abre a palavra (ancora o som inicial)
            if i == 0:
                out.append("a")
            continue
        if ch == "c":
            out.append("s" if nxt in ("e", "i", "y") else "k")
        elif ch == "q":
            out.append("k")
        elif ch == "x":
            out.append("ks")
        elif ch == "z":
            out.append("s")
        elif ch == "v":
            out.append("f")
        elif ch == "d" and nxt == "":
            out.append("t")
        elif ch == "h":
            continue
        else:
            out.append(ch)
    key = "".join(out)
    # colapsa repetições ("nn" → "n")
    collapsed: list[str] = []
    for ch in key:
        if not collapsed or collapsed[-1] != ch:
            collapsed.append(ch)
    return "".join(collapsed)


def _fuzzy_score(a: str, b: str) -> float:
    """Similaridade 0–100 entre dois trechos, misturando grafia e som.

    É a função que o resto da ferramenta usa pra decidir se um pedaço de fala
    é (ou não) uma menção ao produto. Limiar de trabalho: ~72.
    """
    A, B = _collapse(a), _collapse(b)
    if not A or not B:
        return 0.0
    if A == B:
        return 100.0
    seq = SequenceMatcher(None, A, B).ratio()
    pa, pb = phonetic_key(A), phonetic_key(B)
    phon = SequenceMatcher(None, pa, pb).ratio() if pa and pb else 0.0
    score = max(seq, (seq + phon) / 2.0) * 100.0
    # janela muito mais curta (ou longa) que o alvo não é a mesma coisa:
    # evita casar só "sugar" com "sugarbind".
    len_ratio = min(len(A), len(B)) / max(len(A), len(B))
    if len_ratio < 0.6:
        score *= 0.85
    return round(score, 2)


# ── tokens ──────────────────────────────────────────────────────────────────
@dataclass
class Token:
    word: str
    start: float
    end: float


@dataclass
class Mention:
    start: float
    end: float
    text: str
    target: str
    score: float
    words: int = 1
    kind: str = "spoken"
    speaker: str = "unknown"
    context: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        data = {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "target": self.target,
            "score": self.score,
            "words": self.words,
            "kind": self.kind,
            "speaker": self.speaker,
            "context": self.context,
        }
        if self.extra:
            data["extra"] = self.extra
        return data


def to_tokens(words) -> list[Token]:
    """Aceita saída do Whisper (dicts com word/start/end) ou Tokens prontos."""
    tokens: list[Token] = []
    for item in words or []:
        if isinstance(item, Token):
            tokens.append(item)
            continue
        if isinstance(item, dict):
            raw = item.get("word", item.get("text", ""))
            start = float(item.get("start", 0.0) or 0.0)
            end = float(item.get("end", start) or start)
        else:  # objeto qualquer com atributos
            raw = getattr(item, "word", "")
            start = float(getattr(item, "start", 0.0) or 0.0)
            end = float(getattr(item, "end", start) or start)
        cleaned = normalize(raw)
        if not cleaned:
            continue
        tokens.append(Token(word=cleaned, start=start, end=end))
    return tokens


def tokens_from_segments(segments) -> list[Token]:
    """Sem timestamps por palavra: interpola linearmente dentro do segmento."""
    tokens: list[Token] = []
    for seg in segments or []:
        text = seg.get("text", "") if isinstance(seg, dict) else getattr(seg, "text", "")
        start = float((seg.get("start") if isinstance(seg, dict) else getattr(seg, "start", 0.0)) or 0.0)
        end = float((seg.get("end") if isinstance(seg, dict) else getattr(seg, "end", start)) or start)
        words = normalize(text).split()
        if not words:
            continue
        span = max(end - start, 0.001)
        step = span / len(words)
        for i, word in enumerate(words):
            tokens.append(Token(word=word, start=start + i * step, end=start + (i + 1) * step))
    return tokens


def iter_windows(tokens: list[Token], max_words: int = DEFAULT_MAX_WORDS):
    """Todas as janelas de 1..max_words palavras: (i, j, texto)."""
    total = len(tokens)
    for i in range(total):
        for n in range(1, max_words + 1):
            j = i + n
            if j > total:
                break
            yield i, j, " ".join(t.word for t in tokens[i:j])


# ── falsos positivos ────────────────────────────────────────────────────────
def is_false_positive(window_text: str, context_text: str, stop_phrases=DEFAULT_STOP_PHRASES) -> bool:
    """True se a janela só casou porque está dentro de uma expressão genérica.

    'blood sugar levels' não é uma menção ao SugarBind, mesmo que o fuzzy goste.
    """
    window = normalize(window_text)
    context = normalize(context_text or window)
    if not window:
        return True
    window_words = set(window.split())
    for phrase in stop_phrases:
        phrase_norm = normalize(phrase)
        if not phrase_norm or phrase_norm not in context:
            continue
        # só descarta se a janela é parte da expressão genérica
        phrase_words = set(phrase_norm.split())
        if window_words and window_words <= phrase_words:
            return True
    return False


def _context_text(tokens: list[Token], i: int, j: int, pad: int = 2) -> str:
    left = max(0, i - pad)
    right = min(len(tokens), j + pad)
    return " ".join(t.word for t in tokens[left:right])


# ── busca ───────────────────────────────────────────────────────────────────
def find_mentions(
    words,
    targets,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    max_words: int = DEFAULT_MAX_WORDS,
    stop_phrases=DEFAULT_STOP_PHRASES,
    kind: str = "spoken",
) -> list[Mention]:
    """Acha as menções aos `targets` na fala, sem regex.

    Retorna menções ordenadas no tempo e sem sobreposição (a de maior score
    ganha quando duas janelas disputam o mesmo trecho).
    """
    tokens = to_tokens(words)
    wanted = [t for t in (targets or []) if (t or "").strip()]
    if not tokens or not wanted:
        return []

    candidates: list[tuple[float, int, int, str, str]] = []
    for i, j, window in iter_windows(tokens, max_words):
        best_score = 0.0
        best_target = ""
        for target in wanted:
            score = _fuzzy_score(window, target)
            if score > best_score:
                best_score, best_target = score, target
        if best_score < threshold:
            continue
        context = _context_text(tokens, i, j)
        if is_false_positive(window, context, stop_phrases):
            continue
        candidates.append((best_score, i, j, window, best_target))

    # maior score primeiro; empate → janela mais longa
    candidates.sort(key=lambda c: (-c[0], -(c[2] - c[1]), c[1]))
    taken: list[tuple[int, int]] = []
    chosen: list[Mention] = []
    for score, i, j, window, target in candidates:
        if any(i < end and start < j for start, end in taken):
            continue
        taken.append((i, j))
        chosen.append(
            Mention(
                start=tokens[i].start,
                end=tokens[j - 1].end,
                text=window,
                target=target,
                score=score,
                words=j - i,
                kind=kind,
                context=_context_text(tokens, i, j, pad=6),
            )
        )
    chosen.sort(key=lambda m: m.start)
    return chosen


def sentence_span(tokens: list[Token], i: int, j: int, *, max_gap: float = 0.55) -> tuple[float, float]:
    """Expande [i, j) até a frase inteira (usado em depoimento).

    Sem pontuação confiável no ASR, a fronteira é a pausa: um buraco maior que
    `max_gap` entre palavras marca fim de frase.
    """
    start_idx = i
    while start_idx > 0:
        gap = tokens[start_idx].start - tokens[start_idx - 1].end
        if gap > max_gap:
            break
        start_idx -= 1
    end_idx = j
    while end_idx < len(tokens):
        gap = tokens[end_idx].start - tokens[end_idx - 1].end
        if gap > max_gap:
            break
        end_idx += 1
    return tokens[start_idx].start, tokens[end_idx - 1].end


def find_sentence(tokens: list[Token], mention: Mention, *, max_gap: float = 0.55) -> tuple[float, float, str]:
    """Frase completa que contém a menção (start, end, texto)."""
    i = next((k for k, t in enumerate(tokens) if t.start >= mention.start - 1e-6), 0)
    j = next((k + 1 for k in range(len(tokens) - 1, -1, -1) if tokens[k].end <= mention.end + 1e-6), i + 1)
    j = max(j, i + 1)
    start, end = sentence_span(tokens, i, j, max_gap=max_gap)
    text = " ".join(t.word for t in tokens if start - 1e-6 <= t.start and t.end <= end + 1e-6)
    return start, end, text


def hotwords_prompt(terms) -> str:
    """Prompt de viés pro Whisper: grafa o nome antigo do jeito certo."""
    clean = [t.strip() for t in (terms or []) if (t or "").strip()]
    if not clean:
        return ""
    return "Nomes próprios que aparecem neste áudio: " + ", ".join(clean) + "."
