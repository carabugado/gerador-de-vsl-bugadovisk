"""
Tradução de legendas .srt mantendo os TEMPOS EXATOS.

Traduz inglês → português (ou outro alvo) preservando:
  - o número de blocos (bloco i da saída = bloco i da entrada);
  - os timecodes verbatim (copiados sem reparsear segundos → zero arredondamento).

Como a ordem das palavras muda entre idiomas, o LLM enxerga todos os blocos do
trecho de uma vez e redistribui as palavras entre os blocos, de modo que cada
bloco, lido sozinho, soe natural e acompanhe a fala — igual a uma tradução
simultânea encaixada embaixo do original.
"""
import re
import json
from pathlib import Path
from typing import List, Dict, Callable, Optional

import llm

# Captura: índice, timecode início --> fim (resto da linha ignorado), corpo do bloco.
# Aceita vírgula ou ponto nos milissegundos (.srt usa vírgula, .vtt usa ponto).
_BLOCK_RE = re.compile(
    r"(?P<idx>\d+)\s*\n"
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3})[^\n]*\n"
    r"(?P<body>.*?)(?=\n\s*\n|\s*\Z)",
    re.DOTALL,
)


def parse_srt(text: str) -> List[Dict]:
    """Parseia o texto de um .srt/.vtt preservando os timecodes verbatim."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    blocks = []
    for m in _BLOCK_RE.finditer(text):
        body = m.group("body").strip("\n").strip()
        blocks.append({
            "start": m.group("start"),
            "end":   m.group("end"),
            "text":  body,
        })
    return blocks


def _ts_to_ms(ts: str) -> int:
    """'HH:MM:SS,mmm' (ou com ponto) → milissegundos."""
    ts = ts.strip().replace(".", ",")
    hh, mm, rest = ts.split(":")
    ss, ms = rest.split(",")
    return ((int(hh) * 60 + int(mm)) * 60 + int(ss)) * 1000 + int(ms)


def _ms_to_ts(ms: int) -> str:
    """milissegundos → 'HH:MM:SS,mmm' (clampa em 0)."""
    if ms < 0:
        ms = 0
    hh, ms = divmod(ms, 3600000)
    mm, ms = divmod(ms, 60000)
    ss, ms = divmod(ms, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def shift_blocks(blocks: List[Dict], offset_sec: float) -> List[Dict]:
    """Desloca todos os timecodes por offset_sec (pode ser negativo). Use pra ancorar
    a legenda na posição do playhead quando a fala não começa no 0 da sequência."""
    if not offset_sec:
        return blocks
    off = int(round(offset_sec * 1000))
    for b in blocks:
        b["start"] = _ms_to_ts(_ts_to_ms(b["start"]) + off)
        b["end"] = _ms_to_ts(_ts_to_ms(b["end"]) + off)
    return blocks


def build_srt(blocks: List[Dict]) -> str:
    """Reconstrói o .srt (renumerado 1..N) com os mesmos timecodes."""
    out = []
    for i, b in enumerate(blocks, 1):
        out.append(str(i))
        out.append(f"{b['start']} --> {b['end']}")
        out.append((b.get("text") or "").strip())
        out.append("")
    return "\n".join(out).strip() + "\n"


_LANG_NAMES = {
    "pt": "português do Brasil",
    "en": "inglês",
    "es": "espanhol",
    "fr": "francês",
}

_SYSTEM = (
    "Você é um tradutor de legendas para TRADUÇÃO SIMULTÂNEA: cada legenda traduzida "
    "vai aparecer EXATAMENTE embaixo da legenda original, no mesmo instante. Por isso "
    "cada bloco precisa cobrir o MESMO trecho falado que o bloco original.\n"
    "REGRAS OBRIGATÓRIAS:\n"
    "- Cada bloco vem marcado com <<<N>>>. Devolva uma linha por bloco: <<<N>>> + a tradução.\n"
    "- Traduza CADA bloco NO LUGAR: o bloco N de saída traduz as palavras do bloco N de entrada.\n"
    "- NÃO junte o sentido de vários blocos num só. NÃO empurre palavras de um bloco para outro.\n"
    "- NÃO invente, NÃO acrescente e NÃO complete nada que não esteja na fala daquele bloco.\n"
    "- Use os blocos vizinhos só como CONTEXTO para traduzir certo — nunca para mover texto.\n"
    "- Pode ficar um pedaço de frase por bloco (o original também é cortado assim) — tudo bem.\n"
    "- Quando uma frase é cortada entre blocos, corte a tradução no MESMO ponto; NÃO complete a "
    "frase num bloco só nem encha o bloco seguinte com algo inventado.\n"
    "- Tom natural e falado, fiel ao sentido. Sem aspas, sem comentários. Só os blocos <<<N>>>.\n"
    "\n"
    "EXEMPLO (repare: o corte fica no mesmo lugar e NADA é inventado):\n"
    "Entrada:\n"
    "<<<1>>> Well, gentlemen, thank\n"
    "<<<2>>> you so much for your time.\n"
    "<<<3>>> And, Rafaela\n"
    "Saída CORRETA:\n"
    "<<<1>>> Bem, senhores, muito\n"
    "<<<2>>> obrigado pelo seu tempo.\n"
    "<<<3>>> E, Rafaela,\n"
    "Saída ERRADA (NÃO faça): <<<1>>> com a frase inteira e <<<2>>> com algo que não estava na fala."
)

# Ordem de IA para tradução: Gemini primeiro (qualidade), Ollama local de reserva.
# Se o Gemini tiver chave válida ele é sempre usado; só cai no Ollama se falhar
# (sem chave / cota estourada / offline). complete() escala sozinho.
_TRANSLATE_BACKENDS = ["gemini", "ollama"]

# Marcador <<<N>>> seguido do texto até o próximo marcador (ou fim). DOTALL p/ multilinha.
_MARK_RE = re.compile(r"<<<\s*(\d+)\s*>>>[ \t]*(.*?)(?=<<<\s*\d+\s*>>>|\Z)", re.DOTALL)


def _coerce_list(data) -> Optional[list]:
    """Aceita array puro ou objeto que embrulha o array (blocks/translations/etc)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("blocks", "translations", "result", "results", "items",
                  "legendas", "traducao", "traducoes", "output"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return None


def _parse_translations(raw: str, n: int) -> Optional[List[str]]:
    """Alinha a resposta do modelo aos N blocos. Robusto a modelos locais:
    1) marcadores <<<N>>> (primário); 2) array JSON; 3) objeto JSON com N valores."""
    # 1) Marcadores <<<N>>> — tolera reordenação e lixo ao redor.
    found = {}
    for m in _MARK_RE.finditer(raw or ""):
        found[int(m.group(1))] = m.group(2).strip().strip('"').strip()
    if found:
        out = [found.get(i + 1) for i in range(n)]
        if all(x and x.strip() for x in out):
            return out

    # 2/3) JSON: array de N itens, ou objeto cujos N valores estão na ordem.
    data = llm.safe_json(raw)
    lst = _coerce_list(data)
    if lst is None and isinstance(data, dict) and len(data) == n:
        lst = list(data.values())
    if isinstance(lst, list) and len(lst) == n and all(str(x).strip() for x in lst):
        return [str(x).strip() for x in lst]
    return None


def _translate_chunk(texts: List[str], target: str) -> List[str]:
    """Traduz um trecho de blocos preservando a contagem. Levanta erro se não
    conseguir alinhar (o chamador faz fallback bloco a bloco)."""
    lang = _LANG_NAMES.get(target, target)
    numbered = "\n".join(f"<<<{i + 1}>>> {t}" for i, t in enumerate(texts))
    user = (
        f"Traduza para {lang} os {len(texts)} blocos abaixo, CADA UM NO LUGAR "
        f"(o bloco N traduz só as palavras do bloco N — não junte, não mova, não invente). "
        f"Mantenha os marcadores <<<N>>> e devolva exatamente {len(texts)} blocos, "
        f"um por linha.\n\n{numbered}"
    )
    # force_json=False: pedir JSON forçado faz modelos locais devolverem objeto
    # com chaves erradas; o formato <<<N>>> é mais confiável para 7B.
    # backends: Gemini PRIMEIRO (traduz muito melhor) e Ollama local só de reserva
    # (quando o Gemini está sem chave/cota/offline). complete() filtra por
    # disponibilidade e escala pro próximo se o primeiro falhar.
    raw = llm.complete(_SYSTEM, user, max_tokens=4000, temperature=0.3,
                       force_json=False, backends=_TRANSLATE_BACKENDS)
    out = _parse_translations(raw, len(texts))
    if out is None:
        raise ValueError(f"não consegui alinhar a tradução aos {len(texts)} blocos")
    return out


def translate_srt_text(text: str, target: str = "pt", chunk_size: int = 40,
                       offset_seconds: float = 0.0,
                       progress: Optional[Callable[[str], None]] = None) -> Dict:
    """Traduz o conteúdo de um .srt. offset_seconds desloca os tempos (ancora no
    playhead). Retorna {ok, srt, blocks} ou {ok:False,error}."""
    blocks = parse_srt(text)
    if not blocks:
        return {"ok": False, "error": "SRT vazio ou sem timecodes (-->)."}
    if not llm.available():
        return {"ok": False, "error": "Nenhuma IA disponível: ligue o Ollama local "
                                      "ou configure uma chave (Gemini)."}

    total = len(blocks)
    non_empty = sum(1 for b in blocks if b["text"].strip())
    failed = 0            # blocos que caíram no original por erro de IA (cota/offline)
    out_texts: List[str] = []
    for i in range(0, total, chunk_size):
        chunk = blocks[i:i + chunk_size]
        src = [b["text"] for b in chunk]
        if progress:
            progress(f"Traduzindo blocos {i + 1}–{min(i + chunk_size, total)} de {total}...")
        try:
            out_texts.extend(_translate_chunk(src, target))
        except Exception:
            # Fallback: bloco a bloco, garantindo a contagem custe o que custar.
            for s in src:
                if not s.strip():
                    out_texts.append("")
                    continue
                try:
                    one = _translate_chunk([s], target)
                    out_texts.append(one[0] if one else s)
                except Exception:
                    out_texts.append(s)   # último recurso: mantém o original
                    failed += 1

    # Falha honesta: se a IA não traduziu NADA (ex.: cota estourada / offline no meio),
    # não devolve um arquivo "traduzido" que na verdade é o original em inglês.
    if non_empty and failed >= non_empty:
        return {"ok": False, "error": "A IA não respondeu (cota esgotada ou offline): "
                                      "nenhuma legenda foi traduzida. Tente de novo mais "
                                      "tarde ou use o Ollama local."}

    for b, t in zip(blocks, out_texts):
        b["text"] = t
    if offset_seconds:
        shift_blocks(blocks, offset_seconds)
    res = {"ok": True, "srt": build_srt(blocks), "blocks": total}
    if failed:
        res["warning"] = (f"{failed} de {non_empty} blocos não foram traduzidos "
                          f"(mantidos no original) — a IA falhou nesses.")
    return res


def out_path_for(src_path: str, target: str = "pt") -> str:
    """ingles.srt → ingles.pt.srt (ao lado do original)."""
    p = Path(src_path)
    return str(p.with_name(f"{p.stem}.{target}{p.suffix or '.srt'}"))


def translate_srt_file(src_path: str, target: str = "pt",
                       out_path: Optional[str] = None,
                       offset_seconds: float = 0.0,
                       progress: Optional[Callable[[str], None]] = None) -> Dict:
    """Lê um .srt do disco, traduz e grava o resultado. Retorna o resultado com out_path."""
    p = Path(src_path)
    if not p.exists():
        return {"ok": False, "error": f"Arquivo não encontrado: {src_path}"}
    text = p.read_text(encoding="utf-8", errors="ignore")
    res = translate_srt_text(text, target=target, offset_seconds=offset_seconds, progress=progress)
    if not res.get("ok"):
        return res
    out = out_path or out_path_for(src_path, target)
    Path(out).write_text(res["srt"], encoding="utf-8")
    res["out_path"] = out
    return res
