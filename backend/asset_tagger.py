"""
MELHORIA 2 — Tags semânticas nos assets (sidecar {nome}.tags.json).

Para cada vídeo da pasta, gera/atualiza um arquivo de tags com emoções, energia,
tipo visual, sujeitos, blocos adequados/inadequados, verticais, keywords,
compliance_safe e histórico de uso (times_used/accepted/rejected).

Auto-tagging via Claude:
  - nome descritivo ("woman-frustrated-mirror.mp4") → manda o NOME pra API (texto)
  - nome genérico ("clip_0042.mp4") → extrai 1 frame (ffmpeg) e manda a IMAGEM
    pra API (visão). Sem visão disponível → marca "needs_manual_tagging".

Uso como script:   python asset_tagger.py "/caminho/da/pasta" [--force]
Uso pela API:      ver endpoint /tag_assets no server.py
"""
import os
import re
import sys
import json
import base64
from typing import Optional, List, Dict, Callable

import llm
from broll_index import _extract_frame_ffmpeg, SUPPORTED
import compliance

# System prompt EXATO do auto-tagging (contrato com o produto).
AUTOTAG_SYSTEM = (
    "Analise este asset de vídeo B-roll baseado no nome do arquivo (e imagem se "
    "disponível). Retorne APENAS JSON válido sem markdown:\n"
    "{\n"
    '"emotions": ["lista de emoções que este clip transmite"],\n'
    '"energy_level": "low|medium|high",\n'
    '"visual_type": ["emotional|illustrative|authority|result|lifestyle|data_graphic"],\n'
    '"subjects": ["objetos/pessoas/cenários visíveis"],\n'
    '"suitable_blocks": ["tipos de bloco de VSL onde este clip funciona bem"],\n'
    '"unsuitable_blocks": ["tipos de bloco onde este clip NÃO deve ser usado"],\n'
    '"verticals": ["verticais de suplemento onde é adequado: WL|ED|NR|PT|VS|JT|FG"],\n'
    '"keywords": ["termos de busca descritivos em inglês"]\n'
    "}"
)

# Nome é "genérico" se não carrega significado (precisa olhar a imagem).
_GENERIC_RE = re.compile(
    r'^(clip|video|vid|img|image|mvi|dji|gopro|gx|dsc|mov|file|untitled|seq|shot|'
    r'broll|footage|render|export|comp)[\s_\-]*\d+', re.I
)


def tag_path(video_path: str) -> str:
    """Sidecar de tags: {stem}.tags.json ao lado do vídeo."""
    stem = os.path.splitext(video_path)[0]
    return stem + ".tags.json"


def load_tags(video_path: str) -> Optional[dict]:
    """Carrega as tags do asset (procura {stem}.tags.json e {path}.tags.json)."""
    for cand in (tag_path(video_path), video_path + ".tags.json"):
        if os.path.exists(cand):
            try:
                with open(cand, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
    return None


def _is_generic(filename: str) -> bool:
    stem = os.path.splitext(filename)[0]
    if _GENERIC_RE.match(stem):
        return True
    letters = re.sub(r'[^a-zA-Z]', '', stem)
    return len(letters) < 4   # quase só números/hash → genérico


def _frame_b64(video_path: str) -> Optional[str]:
    """Extrai 1 frame e devolve JPEG em base64 (ou None)."""
    img = _extract_frame_ffmpeg(video_path, 1.0)
    if img is None:
        return None
    try:
        import cv2
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return None
        return base64.b64encode(buf.tobytes()).decode("ascii")
    except Exception:
        return None


def _vision_tag(image_b64: str, user_text: str) -> Optional[str]:
    """VISÃO via Ollama (llama3.2-vision) → Claude/Gemini de fallback."""
    try:
        return llm.vision_complete(AUTOTAG_SYSTEM, user_text, image_b64,
                                   max_tokens=600, temperature=0.2, force_json=True)
    except Exception as e:
        print(f"[Tagger] visão falhou: {str(e)[:80]}")
        return None


def _parse(raw: str) -> Optional[dict]:
    data = llm.safe_json(raw)
    return data if isinstance(data, dict) else None


def _empty_semantic() -> dict:
    return {"emotions": [], "energy_level": "medium", "visual_type": [],
            "subjects": [], "suitable_blocks": [], "unsuitable_blocks": [],
            "verticals": [], "keywords": []}


def _semantic_tags(video_path: str) -> (Optional[dict], str):
    """Devolve (tags_semânticas, método). método ∈ name|vision|needs_manual_tagging."""
    filename = os.path.basename(video_path)
    generic = _is_generic(filename)

    def _by_name() -> Optional[dict]:
        usable = llm.chain_for("auto_tag")
        if not usable:
            return None
        try:
            raw = llm.complete(AUTOTAG_SYSTEM, f"Filename: {filename}",
                               max_tokens=600, temperature=0.2,
                               force_json=True, backends=usable)
            return _parse(raw)
        except Exception as e:
            print(f"[Tagger] texto falhou {filename}: {str(e)[:60]}")
            return None

    # 1) nome descritivo → texto
    if not generic:
        tags = _by_name()
        if tags:
            return tags, "name"

    # 2) nome genérico (ou texto falhou) → VISÃO (Ollama vision → Claude/Gemini)
    if llm.vision_chain():
        b64 = _frame_b64(video_path)
        if b64:
            tags = _parse(_vision_tag(b64, f"Filename: {filename}"))
            if tags:
                return tags, "vision"

    # 3) último recurso: nome via texto (se ainda não tentou)
    if generic:
        tags = _by_name()
        if tags:
            return tags, "name"

    return None, "needs_manual_tagging"


def _compliance_safe(filename: str, tags: dict) -> bool:
    """True se o asset NÃO bate em nenhuma regra universal de compliance."""
    rules = compliance._load_rules()
    if not rules:
        return True
    text = " ".join([filename.lower(), " ".join(tags.get("keywords", [])),
                     " ".join(tags.get("subjects", []))]).lower()
    return compliance._check_asset(text, "", rules) is None


def auto_tag_one(video_path: str, force: bool = False) -> dict:
    """Gera (ou mantém) o sidecar de tags de UM vídeo. Preserva histórico de uso."""
    existing = load_tags(video_path)
    if existing and not force:
        return existing

    sem, method = _semantic_tags(video_path)
    filename = os.path.basename(video_path)

    base = _empty_semantic()
    if sem:
        base.update({k: sem.get(k, base[k]) for k in base})
    tags = {
        "filename": filename,
        "emotions": base["emotions"],
        "energy_level": base["energy_level"],
        "visual_type": base["visual_type"] if isinstance(base["visual_type"], list)
                       else [base["visual_type"]],
        "subjects": base["subjects"],
        "suitable_blocks": base["suitable_blocks"],
        "unsuitable_blocks": base["unsuitable_blocks"],
        "verticals": base["verticals"],
        "keywords": base["keywords"],
        "compliance_safe": _compliance_safe(filename, base),
        "tagging_method": method,
        # histórico preservado entre re-taggings
        "times_used": (existing or {}).get("times_used", 0),
        "times_accepted": (existing or {}).get("times_accepted", 0),
        "times_rejected": (existing or {}).get("times_rejected", 0),
    }
    if method == "needs_manual_tagging":
        tags["needs_manual_tagging"] = True

    try:
        with open(tag_path(video_path), "w", encoding="utf-8") as f:
            json.dump(tags, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Tagger] não salvou tags de {filename}: {e}")
    return tags


def tag_folder(folder: str, force: bool = False,
               progress_cb: Optional[Callable] = None) -> Dict:
    """Tagueia todos os vídeos da pasta (recursivo). Retorna contadores."""
    videos = []
    for root, _, files in os.walk(folder):
        for fn in files:
            if fn.startswith("._") or fn.startswith("."):
                continue
            if os.path.splitext(fn)[1].lower() in SUPPORTED:
                videos.append(os.path.join(root, fn))

    counts = {"total": len(videos), "tagged": 0, "vision": 0,
              "needs_manual": 0, "skipped": 0}
    for i, vp in enumerate(videos):
        if load_tags(vp) and not force:
            counts["skipped"] += 1
        else:
            tags = auto_tag_one(vp, force=force)
            counts["tagged"] += 1
            if tags.get("tagging_method") == "vision":
                counts["vision"] += 1
            if tags.get("needs_manual_tagging"):
                counts["needs_manual"] += 1
        if progress_cb:
            progress_cb(i + 1, len(videos), os.path.basename(vp))
    return counts


def update_stats(video_path: str, accepted: bool = False,
                 rejected: bool = False, used: bool = True) -> None:
    """MELHORIA 3 (feedback): atualiza histórico no .tags.json após decisão do editor."""
    tags = load_tags(video_path)
    if tags is None:
        return
    if used:
        tags["times_used"] = tags.get("times_used", 0) + 1
    if accepted:
        tags["times_accepted"] = tags.get("times_accepted", 0) + 1
    if rejected:
        tags["times_rejected"] = tags.get("times_rejected", 0) + 1
    try:
        with open(tag_path(video_path), "w", encoding="utf-8") as f:
            json.dump(tags, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("uso: python asset_tagger.py <pasta> [--force]")
        sys.exit(1)
    force_flag = "--force" in sys.argv
    res = tag_folder(sys.argv[1], force=force_flag,
                     progress_cb=lambda c, t, n: print(f"  [{c}/{t}] {n}"))
    print(json.dumps(res, indent=2))
