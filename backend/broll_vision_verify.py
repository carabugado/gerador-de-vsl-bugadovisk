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


# Quantos frames por clip mandar pra visão. 1 = rápido/barato; 3 = "ver o clip de
# verdade" (início/meio/fim — capta movimento/conteúdo). Modo Qualidade Alta usa 3.
VISION_FRAMES = int(os.environ.get("VISION_FRAMES", "1"))


def _one_b64(img) -> Optional[str]:
    try:
        import cv2
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None
    except Exception:
        return None


def _frames_b64(path: str, duration: float = 0.0, n: int = 1) -> List[str]:
    """N frames (base64) espaçados no clip — capta o conteúdo todo, não 1 instante."""
    dur = float(duration or 0)
    if n <= 1 or dur < 2.0:
        times = [dur * 0.5 if dur > 1.0 else 1.0]
    elif n <= 3:
        times = [dur * f for f in (0.1, 0.5, 0.8)][:n]   # margem do fim (clip curto)
    else:
        times = [dur * (i + 1) / (n + 1) for i in range(n)]
    out = []
    for t in times:
        img = _extract_frame_ffmpeg(path, max(0.0, t))
        if img is not None:
            b = _one_b64(img)
            if b:
                out.append(b)
    return out


def _frame_b64(path: str) -> Optional[str]:        # compat
    fs = _frames_b64(path, 0.0, 1)
    return fs[0] if fs else None


def verify(frames, script_excerpt: str, desired: str) -> Optional[dict]:
    """Pergunta à IA de visão (Claude/Gemini/Ollama) se o clip casa. `frames`: 1 ou
    vários (início/meio/fim do mesmo clip). Dict ou None."""
    imgs = [f for f in (frames if isinstance(frames, list) else [frames]) if f]
    if not imgs:
        return None
    multi = len(imgs) > 1
    user = (f'O script diz: "{script_excerpt}"\nO B-roll ideal seria: "{desired}"\n'
            + (f"As {len(imgs)} imagens são quadros (início/meio/fim) do MESMO clipe. "
               "Avalie o CLIPE como um todo. " if multi else "")
            + "Esse clipe combina?")
    try:
        raw = llm.vision_complete(_VERIFY_SYSTEM, user, imgs,
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


def rerank(script_excerpt: str, desired: str, candidates: List[Dict],
           n_frames: int = None) -> List[Dict]:
    """Re-ranqueia os candidatos pelo match_score do Vision (top-K, em paralelo).
    n_frames: quadros por clip (Modo Qualidade Alta = 3 → "vê o clip de verdade").
    Mistura: score_final = 0.5*embedding_norm + 0.5*(match_score/10). Falha → mantém ordem."""
    top = candidates[:VERIFY_TOP_K]
    if not top or not available():
        return candidates
    nf = n_frames or VISION_FRAMES

    def _eval(c):
        frames = _frames_b64(c["path"], c.get("duration", 0), nf)
        if not frames:
            return c, None
        return c, verify(frames, script_excerpt, desired)

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
