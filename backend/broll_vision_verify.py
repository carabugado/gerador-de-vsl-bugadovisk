"""
FASE 3 — Verificação com Claude Vision (segundo filtro, OPCIONAL).

Para os top candidatos da busca por embedding, manda 1 frame de cada pro Claude
Vision e pergunta se REALMENTE combina com o que o script pede. Re-ranqueia pelo
match_score (0-10). Elimina falsos positivos do CLIP.

É lento (~2s/clip) e custa API → desligado por padrão. Liga com VISION_VERIFY=1
ou pela flag vision_verify no /process. Roda só nos top-K (não em tudo).
"""
import os
import json
import base64
import concurrent.futures
from typing import List, Dict, Optional

import llm
from broll_index import _extract_frame_ffmpeg

# Visão local (Ollama 11B) é LENTA (~5s/frame, serializa num modelo só). Verificar
# top-2 por segmento já decide o gate (melhor candidato serve ou gera?) sem explodir
# o tempo. Aumente VISION_VERIFY_TOPK se quiser rerank mais amplo (mais lento).
VERIFY_TOP_K = int(os.environ.get("VISION_VERIFY_TOPK", "2"))
_MAX_WORKERS = int(os.environ.get("VISION_VERIFY_WORKERS", "2"))


# Prompt de visão enxuto (Llama 3.2 Vision 11B precisa de instrução direta)
_VERIFY_SYSTEM = (
    "Olhe a imagem. Leia o que o script precisa. Dê uma nota de 0 a 10 se a imagem "
    "serve como B-roll.\n"
    "10 = perfeito, mostra exatamente o que o script descreve\n"
    "7 = bom, tema certo mas detalhes diferentes\n"
    "4 = fraco, mesmo assunto mas ação/objeto errado\n"
    "1 = não serve, imagem não tem relação com o script\n"
    "0 = contrário, emoção oposta ao que o script precisa\n\n"
    "Retorne APENAS JSON:\n"
    '{"match_score": 0-10, "what_i_see": "o que aparece na imagem em 10 palavras", '
    '"object_match": true/false, "action_match": true/false, "emotion_match": true/false}'
)


def available() -> bool:
    return len(llm.vision_chain()) > 0


def _frame_b64(path: str) -> Optional[str]:
    img = _extract_frame_ffmpeg(path, 1.0)
    if img is None:
        return None
    try:
        import cv2
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None
    except Exception:
        return None


def verify(frame_b64: str, script_excerpt: str, desired: str) -> Optional[dict]:
    """Pergunta à IA de visão (Ollama → Claude) se o frame casa. Dict ou None."""
    user = (f'O script diz: "{script_excerpt}"\n'
            f'O B-roll ideal seria: "{desired}"\nEsta imagem combina?')
    try:
        raw = llm.vision_complete(_VERIFY_SYSTEM, user, frame_b64,
                                  max_tokens=300, temperature=0.2, force_json=True)
        data = llm.safe_json(raw)
        if not isinstance(data, dict):
            return None
        # clamp do score (validateVisionOutput)
        try:
            data["match_score"] = max(0, min(10, float(data.get("match_score", 5))))
        except (TypeError, ValueError):
            data["match_score"] = 5
        return data
    except Exception as e:
        print(f"[Vision] verify falhou: {str(e)[:80]}")
        return None


def rerank(script_excerpt: str, desired: str, candidates: List[Dict]) -> List[Dict]:
    """Re-ranqueia os candidatos pelo match_score do Vision (top-K, em paralelo).
    Mistura: score_final = 0.5*embedding_norm + 0.5*(match_score/10). Falha → mantém ordem."""
    top = candidates[:VERIFY_TOP_K]
    if not top or not available():
        return candidates

    def _eval(c):
        b64 = _frame_b64(c["path"])
        if not b64:
            return c, None
        return c, verify(b64, script_excerpt, desired)

    evaluated = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        for c, res in ex.map(_eval, top):
            evaluated[c["path"]] = res

    # normaliza o score de embedding do bloco pra 0-1 (relativo ao melhor)
    max_emb = max((c["score"] for c in top), default=1.0) or 1.0
    for c in top:
        res = evaluated.get(c["path"])
        if res and isinstance(res.get("match_score"), (int, float)):
            ms = max(0.0, min(10.0, float(res["match_score"]))) / 10.0
            c["vision_score"] = res["match_score"]
            c["vision_note"] = res.get("what_i_see", "")
            c["score"] = round(0.5 * (c["score"] / max_emb) + 0.5 * ms, 4)
        # sem resultado → mantém o score de embedding original

    top.sort(key=lambda x: x["score"], reverse=True)
    return top + candidates[VERIFY_TOP_K:]


def rerank_all(segments: List[Dict], ranked: List[Dict], queries: List[str]) -> None:
    """Aplica o rerank do Vision in-place nos candidatos de cada segmento ativo."""
    for r in ranked:
        if r.get("skip") or not r.get("candidates"):
            continue
        i = r["index"]
        seg = segments[i]
        q = (queries[i] if i < len(queries) else "") or seg.get("visual_query", "")
        r["candidates"] = rerank(seg.get("text", ""), q, r["candidates"])
