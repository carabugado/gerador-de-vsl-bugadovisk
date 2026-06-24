"""
Matching entre segmentos da VSL e B-rolls da pasta.

Dois sinais semânticos (CLIP), combinados numa escala de "relevância" 0-1:
  1. visual_query  x  FRAMES do b-roll        (texto↔imagem)
  2. visual_query  x  NOME do arquivo limpo   (texto↔texto)  ← forte: nomes são descritivos

O nome do arquivo costuma descrever melhor o conteúdo do que 2 frames, então ele
tem peso maior. As similaridades cru de cada sinal vivem em escalas diferentes
(texto↔imagem ~0.2-0.3; texto↔texto ~0.5-0.85), por isso normalizamos cada uma
para 0-1 antes de combinar.
"""
import re
import numpy as np
from typing import List, Dict, Optional
from broll_index import embed_text

# Limiares na escala de RELEVÂNCIA combinada (0-1).
OK_THRESHOLD       = 0.25   # >= → "ok" (insere direto)
REVIEW_THRESHOLD   = 0.12   # entre os dois → revisar; abaixo → gerar/revisar
LIBRARY_BONUS      = 0.03   # biblioteca local entra um pouco mais fácil
MIN_BROLL_DURATION = 3.0

# Calibragem das escalas cru → relevância 0-1
_IMG_FULL = 0.30   # cosseno texto↔imagem que já vale "match cheio"
_TXT_BASE = 0.55   # baseline texto↔texto (abaixo disso ~irrelevante)
_TXT_FULL = 0.85   # texto↔texto de match forte

_NOISE = re.compile(
    r'\b(mp4|mov|avi|mkv|m4v|hd|4k|1080p|720p|alpha|sbv|looping|animated|'
    r'video|clip|footage|stock|sbv\d*|20\d\d)\b'
)


def _clean_name(filename: str) -> str:
    name = re.sub(r'\.[a-z0-9]+$', '', filename.lower())   # tira extensão
    name = re.sub(r'[-_.]+', ' ', name)
    name = _NOISE.sub('', name)
    name = re.sub(r'\b[0-9a-f]{8,}\b', '', name)            # tira hashes/ids
    return re.sub(r'\s+', ' ', name).strip()


def _keyword_overlap(query: str, clean_name: str) -> float:
    words = {w for w in re.findall(r'\b\w{4,}\b', query.lower())}
    if not words:
        return 0.0
    name_words = set(clean_name.split())
    return len(words & name_words) / len(words)


def _embed_batched(texts: List[str], batch: int = 256) -> np.ndarray:
    out = []
    for i in range(0, len(texts), batch):
        out.append(embed_text(texts[i:i + batch]))
    return np.vstack(out) if out else np.zeros((0, 512))


def _niche_terms(context: dict) -> str:
    """Texto curto do nicho/tema da VSL pra enviesar a busca para o assunto certo."""
    if not context:
        return ""
    parts = [context.get("niche", "")]
    prod = context.get("product") or {}
    parts.append(prod.get("what", ""))
    parts += (context.get("visual_dos") or [])[:3]
    return ". ".join(p for p in parts if p).strip()


def rank_segments(segments: List[Dict], brolls: List[Dict], context: dict = None,
                  top_k: int = 8) -> List[Dict]:
    """Para cada segmento, devolve os TOP-K candidatos de b-roll (CLIP semântico).

    Se houver CONTEXTO da VSL, o nicho/tema é injetado na busca (e nos nomes) para o
    pool já vir do assunto certo — evita trazer clipe de outro nicho (barriga/skincare
    numa VSL de articulação). Quem decide a escolha final é o LLM (broll_select).
    """
    if not brolls:
        return [{"index": i, "skip": False, "candidates": []} for i in range(len(segments))]

    frame_matrix = np.array([b["embedding"] for b in brolls])
    frame_matrix = frame_matrix / (np.linalg.norm(frame_matrix, axis=1, keepdims=True) + 1e-8)

    clean_names = [_clean_name(b["filename"]) for b in brolls]
    name_matrix = _embed_batched(clean_names)

    # Query do segmento, enviesada pelo nicho da VSL
    niche = _niche_terms(context)
    queries = [seg.get("visual_query") or seg["text"] for seg in segments]
    bias_queries = [(q + ". Tema: " + niche) if niche else q for q in queries]
    query_embs = embed_text(bias_queries)

    # Vetor do nicho puro — penaliza candidatos de outro assunto
    niche_emb = embed_text([niche])[0] if niche else None
    name_niche = (name_matrix @ niche_emb) if niche_emb is not None else None

    durations = np.array([b["duration"] for b in brolls])

    out = []
    for i, seg in enumerate(segments):
        duration_needed = seg["end"] - seg["start"]
        if duration_needed < MIN_BROLL_DURATION:
            out.append({"index": i, "skip": True, "candidates": []})
            continue

        q = query_embs[i]
        img_rel = np.clip((frame_matrix @ q) / _IMG_FULL, 0.0, 1.0)
        txt_rel = np.clip((name_matrix @ q - _TXT_BASE) / (_TXT_FULL - _TXT_BASE), 0.0, 1.0)
        kw = np.array([_keyword_overlap(queries[i], n) for n in clean_names])
        combined = 0.55 * txt_rel + 0.35 * img_rel + 0.10 * kw

        # Bônus de NICHO: candidato cujo nome combina com o tema da VSL sobe;
        # de outro assunto fica pra trás (evita barriga/skincare em VSL de articulação).
        if name_niche is not None:
            niche_rel = np.clip((name_niche - _TXT_BASE) / (_TXT_FULL - _TXT_BASE), 0.0, 1.0)
            combined = combined + 0.30 * niche_rel

        # só candidatos com duração suficiente
        combined = np.where(durations >= duration_needed * 0.8, combined, -1.0)

        order = np.argsort(combined)[::-1][:top_k]
        cands = []
        for idx in order:
            if combined[idx] < 0:
                break
            b = brolls[idx]
            cands.append({
                "path": b["path"],
                "filename": b["filename"],
                "clean_name": clean_names[idx],
                "duration": b["duration"],
                "score": round(float(combined[idx]), 4),
                "source": b.get("_source", "project"),
            })
        out.append({"index": i, "skip": False, "candidates": cands})

    return out


def make_result(seg: Dict, cand: Optional[Dict], status: str, reason: str = "") -> Dict:
    return {
        # start do B-ROLL: ancorado na palavra-chave (broll_start) quando houver;
        # senão o início do trecho. A janela da narração (seg) não muda.
        "start":          seg.get("broll_start", seg["start"]),
        "end":            seg["end"],
        "text":           seg["text"],
        "broll_path":     cand["path"]      if cand else None,
        "broll_filename": cand["filename"]  if cand else None,
        "broll_duration": cand["duration"]  if cand else None,
        "confidence":     cand["score"]     if cand else 0.0,
        "status":         status,
        "broll_source":   cand["source"]    if cand else "",
        "select_reason":  reason,
    }
