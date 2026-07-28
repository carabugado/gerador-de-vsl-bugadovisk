"""Plano de rebrand de voz: o que regravar, como e com qual voz.

REGRAS DE OURO (o dono do projeto cobrou muito):

  • Narrador  → troca só a PALAVRA. Preserva a locução real; ninguém percebe.
  • Depoimento → regenera a FRASE INTEIRA na voz do locutor. Soa natural e
    não precisa esticar áudio pra caber.
  • Clone da PRÓPRIA fala de cada locutor (expandindo o trecho não-narrador
    ao redor). Pra voz feminina, concatene vários trechos limpos só dela
    (~13–20 s) e clone disso — fica bem melhor.
  • FALLBACK POR GÊNERO: se o clone falhar, mulher cai em OUTRA voz feminina
    (ou na stock Wise_Woman) e homem cai na stock Deep_Voice_Man.
    JAMAIS voz feminina no narrador.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..pipeline.text_detect import Mention, Token, find_sentence, normalize
from .minimax import STOCK_FEMALE, STOCK_MALE

MODE_WORD = "word"
MODE_SENTENCE = "sentence"

REF_MIN_SECONDS = 13.0
REF_MAX_SECONDS = 20.0


def plan_mode(role: str) -> str:
    """narrador → palavra; qualquer outro locutor → frase inteira."""
    return MODE_WORD if role == "narrator" else MODE_SENTENCE


def replace_mention(text: str, mention_text: str, new_name: str) -> str:
    """Troca o trecho da menção pelo nome novo, preservando o resto da frase."""
    words = normalize(text).split()
    target = normalize(mention_text).split()
    if not words:
        return new_name
    if not target:
        return " ".join(words)
    n = len(target)
    for i in range(len(words) - n + 1):
        if words[i : i + n] == target:
            return " ".join(words[:i] + [new_name] + words[i + n :])
    return " ".join(words)


@dataclass
class VoiceChoice:
    voice_id: str
    kind: str  # cloned | fallback_female | fallback_male | stock_female | stock_male
    reason: str = ""

    @property
    def is_fallback(self) -> bool:
        return self.kind != "cloned"


def pick_voice(
    role: str,
    gender: str,
    *,
    cloned_voice: str | None = None,
    female_pool: list[str] | None = None,
) -> VoiceChoice:
    """Voz a usar. O clone da própria fala ganha sempre que existir."""
    if cloned_voice:
        return VoiceChoice(cloned_voice, "cloned", "clone da própria fala do locutor")
    if role == "narrator":
        # trava dura: narrador NUNCA recebe voz feminina
        return VoiceChoice(STOCK_MALE, "stock_male", "clone falhou; narrador cai em voz masculina de catálogo")
    if gender == "female":
        for candidate in female_pool or []:
            if candidate:
                return VoiceChoice(candidate, "fallback_female", "clone falhou; usa outra voz feminina já clonada")
        return VoiceChoice(STOCK_FEMALE, "stock_female", "clone falhou; voz feminina de catálogo")
    return VoiceChoice(STOCK_MALE, "stock_male", "clone falhou; voz masculina de catálogo")


@dataclass
class RebrandItem:
    start: float
    end: float
    mode: str
    role: str
    speaker_id: str
    gender: str
    original_text: str
    text_to_speak: str
    voice: VoiceChoice | None = None
    reference_spans: list[tuple[float, float]] = field(default_factory=list)
    audio_path: str = ""
    notes: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "mode": self.mode,
            "role": self.role,
            "speaker_id": self.speaker_id,
            "gender": self.gender,
            "original_text": self.original_text,
            "text_to_speak": self.text_to_speak,
            "voice": None if self.voice is None else {"voice_id": self.voice.voice_id, "kind": self.voice.kind, "reason": self.voice.reason},
            "reference_spans": [[round(a, 3), round(b, 3)] for a, b in self.reference_spans],
            "audio_path": self.audio_path,
            "notes": self.notes,
        }


def tag_for(tags, start: float, end: float):
    """Rótulo de locutor que cobre (ou mais perto de) o trecho."""
    best = None
    best_overlap = 0.0
    for tag in tags or []:
        overlap = min(tag.end, end) - max(tag.start, start)
        if overlap > best_overlap:
            best, best_overlap = tag, overlap
    if best is not None:
        return best
    return min(tags, key=lambda t: abs(t.start - start)) if tags else None


def collect_reference_spans(
    tags,
    speaker_id: str,
    *,
    min_seconds: float = REF_MIN_SECONDS,
    max_seconds: float = REF_MAX_SECONDS,
    exclude: tuple[float, float] | None = None,
) -> list[tuple[float, float]]:
    """Trechos limpos só desse locutor pra montar a referência do clone.

    Junta vários pedaços até ~13–20 s: clone de 3 s sai ruim, clone de 15 s
    sai igual à pessoa.
    """
    spans: list[tuple[float, float]] = []
    total = 0.0
    candidates = [t for t in (tags or []) if t.speaker_id == speaker_id]
    candidates.sort(key=lambda t: (t.end - t.start), reverse=True)
    for tag in candidates:
        if exclude and not (tag.end <= exclude[0] or tag.start >= exclude[1]):
            continue
        span_len = tag.end - tag.start
        if span_len < 0.8:
            continue
        take = min(span_len, max_seconds - total)
        if take <= 0:
            break
        spans.append((tag.start, tag.start + take))
        total += take
        if total >= min_seconds:
            break
    spans.sort()
    return spans


def build_plan(
    mentions: list[Mention],
    tokens: list[Token],
    tags,
    *,
    new_product_name: str,
    cloned_voices: dict | None = None,
    max_gap: float = 0.55,
) -> list[RebrandItem]:
    """Monta o plano de rebrand, uma entrada por menção falada."""
    cloned = dict(cloned_voices or {})
    female_pool = [vid for sid, vid in cloned.items() if sid.startswith("female") and vid]
    items: list[RebrandItem] = []
    for mention in mentions:
        tag = tag_for(tags, mention.start, mention.end)
        role = tag.role if tag else "narrator"
        gender = tag.gender if tag else "male"
        speaker_id = tag.speaker_id if tag else "narrator"
        mode = plan_mode(role)
        if mode == MODE_WORD:
            start, end = mention.start, mention.end
            original = mention.text
            to_speak = new_product_name
            notes = "narrador: troca só a palavra, o resto da locução original fica"
        else:
            start, end, sentence = find_sentence(tokens, mention, max_gap=max_gap)
            original = sentence
            to_speak = replace_mention(sentence, mention.text, new_product_name)
            notes = "depoimento: regenera a frase inteira na voz do locutor"
        voice = pick_voice(role, gender, cloned_voice=cloned.get(speaker_id), female_pool=female_pool)
        items.append(
            RebrandItem(
                start=start,
                end=end,
                mode=mode,
                role=role,
                speaker_id=speaker_id,
                gender=gender,
                original_text=original,
                text_to_speak=to_speak,
                voice=voice,
                reference_spans=collect_reference_spans(tags, speaker_id, exclude=(start, end)) if role != "narrator" else [],
                notes=notes,
            )
        )
    items.sort(key=lambda i: i.start)
    return items


def audio_tracks(items: list[RebrandItem]) -> dict[str, list[RebrandItem]]:
    """Agrupa por locutor — vira uma faixa A2+ por locutor no Premiere."""
    tracks: dict[str, list[RebrandItem]] = {}
    for item in items:
        tracks.setdefault(item.speaker_id or "narrator", []).append(item)
    for values in tracks.values():
        values.sort(key=lambda i: i.start)
    return tracks
