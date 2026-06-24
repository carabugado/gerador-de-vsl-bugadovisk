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
import captioner

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


# IMPORTANTE: pedimos um OBJETO {"keywords":[...]} — o force_json do Ollama/Groq força
# JSON-objeto (não array), então pedir array gerava lixo (':null,', []). Objeto bate com
# o modo JSON e o parsing extrai a lista de dentro.
_KW_ENRICH_SYSTEM = (
    "You are tagging adult content video clips for an erectile dysfunction (ED) supplement VSL. "
    'Given a video clip description, output a JSON object of the form {"keywords": ["...", "..."]} '
    "with 12-18 English keywords/phrases that describe the visual scene explicitly and accurately. "
    "Include explicit sexual terms when relevant (erection, hard penis, soft penis, sex, "
    "intercourse, nude, naked, arousal, foreplay, orgasm, etc). Be direct and specific — these "
    "tags are used to match search queries about sexual performance, desire, and intimacy. "
    "Output ONLY the JSON object, no markdown, no explanation.\n"
    'Example input: "a man and a woman in bed"\n'
    'Example output: {"keywords": ["couple in bed", "sexual intercourse", "intimate moment", '
    '"nude bodies", "passion", "desire", "making love", "bedroom", "skin", "arousal", '
    '"togetherness", "sexual connection", "foreplay", "pleasure"]}'
)


def _clean_keywords(raw_list) -> List[str]:
    """Filtra a lista: só strings com letras, sem fragmentos de JSON ('{', ':', 'null'...)."""
    out: List[str] = []
    seen = set()
    for k in (raw_list or []):
        s = str(k).strip().strip('",').strip()
        if len(s) < 2 or not re.search(r"[a-zA-Z]", s):
            continue
        if any(c in s for c in "{}[]:") or s.lower() in ("null", "none", "keywords"):
            continue
        key = s.lower()
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out[:20]


def _keywords_ok(lst) -> bool:
    """True se a lista de keywords é utilizável (não vazia, não-lixo)."""
    return bool(_clean_keywords(lst)) if isinstance(lst, list) else False


def _enrich_keywords_from_caption(caption: str) -> List[str]:
    """Expande um caption BLIP curto em keywords descritivas via LLM local."""
    if not caption or len(caption) < 8:
        return []
    try:
        backends = llm.chain()
        if not backends:
            return []
        raw = llm.complete(_KW_ENRICH_SYSTEM, f"Description: {caption}",
                           max_tokens=250, temperature=0.3,
                           force_json=True, backends=backends)
        data = llm.safe_json(raw)
        kws = []
        if isinstance(data, dict):
            if isinstance(data.get("keywords"), list):
                kws = data["keywords"]
            else:  # qualquer primeira lista de valores serve
                for v in data.values():
                    if isinstance(v, list):
                        kws = v
                        break
        elif isinstance(data, list):
            kws = data
        return _clean_keywords(kws)
    except Exception as e:
        print(f"[Tagger] enrich falhou: {str(e)[:60]}")
        return []


def _compliance_safe(filename: str, tags: dict) -> bool:
    """True se o asset NÃO bate em nenhuma regra universal de compliance."""
    rules = compliance._load_rules()
    if not rules:
        return True
    text = " ".join([filename.lower(), " ".join(tags.get("keywords", [])),
                     " ".join(tags.get("subjects", []))]).lower()
    return compliance._check_asset(text, "", rules) is None


def _write_tags(video_path: str, tags: dict) -> None:
    try:
        with open(tag_path(video_path), "w", encoding="utf-8") as f:
            json.dump(tags, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Tagger] não salvou tags de {os.path.basename(video_path)}: {e}")


def auto_tag_one(video_path: str, force: bool = False, enrich: bool = False) -> dict:
    """Gera (ou mantém) o sidecar de tags de UM vídeo. Preserva histórico de uso.

    enrich=True: re-gera caption_keywords via LLM mesmo em clips já tagueados
                 (útil p/ pasta ED+ com clips de nome numérico).
    """
    existing = load_tags(video_path)
    if existing and not force:
        updated = False
        # upgrade BARATO (sem LLM/cota): adiciona a legenda local se faltar
        if not existing.get("caption") and captioner.available():
            cap = captioner.caption_path(video_path)
            if cap:
                existing["caption"] = cap
                updated = True
        # enriquecimento: gera caption_keywords se caption existe e ainda não há
        # keywords utilizáveis (vazio OU lixo de uma tentativa antiga com parsing ruim).
        if enrich and existing.get("caption") and not _keywords_ok(existing.get("caption_keywords")):
            kws = _enrich_keywords_from_caption(existing["caption"])
            if kws:                              # só sobrescreve quando deu certo
                existing["caption_keywords"] = kws
                updated = True
            elif "caption_keywords" not in existing:
                existing["caption_keywords"] = []  # marca tentativa p/ não travar no 1º run
        if updated:
            _write_tags(video_path, existing)
        return existing

    sem, method = _semantic_tags(video_path)
    filename = os.path.basename(video_path)
    # legenda local (BLIP) — grátis/offline; reusa a existente p/ não re-legendar à toa
    caption = (existing or {}).get("caption", "") or \
        (captioner.caption_path(video_path) if captioner.available() else "")

    base = _empty_semantic()
    if sem:
        base.update({k: sem.get(k, base[k]) for k in base})

    # Se keywords são fracas (< 4) e há caption, enriquece via LLM
    kws = base.get("keywords") or []
    caption_keywords: List[str] = []
    if caption and len(kws) < 4:
        caption_keywords = _enrich_keywords_from_caption(caption)

    tags = {
        "filename": filename,
        "caption": caption,
        "caption_keywords": caption_keywords,
        "emotions": base["emotions"],
        "energy_level": base["energy_level"],
        "visual_type": base["visual_type"] if isinstance(base["visual_type"], list)
                       else [base["visual_type"]],
        "subjects": base["subjects"],
        "suitable_blocks": base["suitable_blocks"],
        "unsuitable_blocks": base["unsuitable_blocks"],
        "verticals": base["verticals"],
        "keywords": kws,
        "compliance_safe": _compliance_safe(filename, base),
        "tagging_method": method,
        # histórico preservado entre re-taggings
        "times_used": (existing or {}).get("times_used", 0),
        "times_accepted": (existing or {}).get("times_accepted", 0),
        "times_rejected": (existing or {}).get("times_rejected", 0),
    }
    if method == "needs_manual_tagging":
        tags["needs_manual_tagging"] = True

    _write_tags(video_path, tags)
    return tags


def tag_folder(folder: str, force: bool = False,
               progress_cb: Optional[Callable] = None,
               enrich: bool = False) -> Dict:
    """Tagueia todos os vídeos da pasta (recursivo). Retorna contadores."""
    videos = []
    for root, _, files in os.walk(folder):
        for fn in files:
            if fn.startswith("._") or fn.startswith("."):
                continue
            if os.path.splitext(fn)[1].lower() in SUPPORTED:
                videos.append(os.path.join(root, fn))

    counts = {"total": len(videos), "tagged": 0, "vision": 0,
              "needs_manual": 0, "skipped": 0, "captioned": 0}
    for i, vp in enumerate(videos):
        had = load_tags(vp)
        try:
            tags = auto_tag_one(vp, force=force, enrich=enrich)
        except Exception as e:
            print(f"[Tagger] erro em {os.path.basename(vp)}: {str(e)[:80]} — pulando")
            counts["skipped"] += 1
            if progress_cb:
                progress_cb(i + 1, len(videos), os.path.basename(vp))
            continue
        if had and not force:
            counts["skipped"] += 1
        else:
            counts["tagged"] += 1
            if tags.get("tagging_method") == "vision":
                counts["vision"] += 1
            if tags.get("needs_manual_tagging"):
                counts["needs_manual"] += 1
        if tags.get("caption"):
            counts["captioned"] += 1
        if progress_cb:
            progress_cb(i + 1, len(videos), os.path.basename(vp))
    return counts


def caption_folder(folder: str, force: bool = False,
                   progress_cb: Optional[Callable] = None) -> Dict:
    """Legenda LOCAL (BLIP) de TODOS os vídeos da pasta — grátis, offline, SEM LLM/cota.
    Cria/atualiza só o campo `caption` no sidecar (preserva tags existentes). Pula quem
    já tem caption (a menos de force). É o passo que torna o clip de nome-hash buscável."""
    if not captioner.available():
        return {"total": 0, "captioned": 0, "skipped": 0, "failed": 0,
                "error": "captioner indisponível (transformers/torch ausentes ou desligado)"}
    videos = []
    for root, _, files in os.walk(folder):
        for fn in files:
            if fn.startswith("._") or fn.startswith("."):
                continue
            if os.path.splitext(fn)[1].lower() in SUPPORTED:
                videos.append(os.path.join(root, fn))

    counts = {"total": len(videos), "captioned": 0, "skipped": 0, "failed": 0}
    for i, vp in enumerate(videos):
        existing = load_tags(vp) or {}
        if existing.get("caption") and not force:
            counts["skipped"] += 1
        else:
            cap = captioner.caption_path(vp)
            if cap:
                if not existing:
                    existing = _empty_semantic()
                    existing["filename"] = os.path.basename(vp)
                    existing["tagging_method"] = "caption_only"
                    existing["times_used"] = 0
                    existing["times_accepted"] = 0
                    existing["times_rejected"] = 0
                existing["caption"] = cap
                _write_tags(vp, existing)
                counts["captioned"] += 1
            else:
                counts["failed"] += 1
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


def _write_progress_file(path: str, payload: dict) -> None:
    """Escrita atômica (tmp + rename) do estado de progresso pro servidor ler."""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except Exception:
        pass


if __name__ == "__main__":
    # uso: python asset_tagger.py <pasta> [--force] [--enrich] [--progress-file PATH]
    _args = sys.argv[1:]
    if not _args or _args[0].startswith("-"):
        print("uso: python asset_tagger.py <pasta> [--force] [--enrich] [--progress-file PATH]")
        sys.exit(1)
    _folder = _args[0]
    _force = "--force" in _args
    _enrich = "--enrich" in _args
    _pfile = None
    if "--progress-file" in _args:
        _i = _args.index("--progress-file")
        if _i + 1 < len(_args):
            _pfile = _args[_i + 1]

    _state = {"pid": os.getpid(), "folder": _folder, "enrich": _enrich,
              "force": _force, "step": "tagging", "current": 0, "total": 0,
              "detail": "Iniciando...", "_relaunch_at": 0}
    if _pfile:
        # preserva o _relaunch_at que o servidor possa ter gravado (retomada)
        try:
            with open(_pfile, encoding="utf-8") as _f:
                _state["_relaunch_at"] = json.load(_f).get("_relaunch_at", 0)
        except Exception:
            pass
        _write_progress_file(_pfile, _state)

    def _cb(c, t, n):
        print(f"  [{c}/{t}] {n}")
        if _pfile:
            _state.update({"current": c, "total": t, "detail": n, "step": "tagging"})
            _write_progress_file(_pfile, _state)

    try:
        res = tag_folder(_folder, force=_force, progress_cb=_cb, enrich=_enrich)
        _detail = (f"{res.get('tagged', 0)} tagueados · {res.get('captioned', 0)} legendas · "
                   f"{res.get('skipped', 0)} já prontos")
        if _pfile:
            _state.update({"step": "done", "detail": _detail,
                           "current": res.get("total", 0), "total": res.get("total", 0)})
            _write_progress_file(_pfile, _state)
        print(json.dumps(res, indent=2))
    except Exception as e:
        if _pfile:
            _state.update({"step": "done", "detail": f"Concluído com erro: {str(e)[:80]}"})
            _write_progress_file(_pfile, _state)
        raise
