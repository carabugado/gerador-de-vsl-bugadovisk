"""
Memória de ESTILO (#L1) — aprende com projetos antigos.

Guarda os pares (texto da narração → B-roll que o editor escolheu) de projetos já
finalizados, com embeddings (texto CLIP + frame do clip CLIP). Depois a seleção pode
enviesar pras escolhas parecidas com as suas (#L2, fatia seguinte).

Arquivo: ~/.vsl_style_memory.json (ou env VSL_STYLE_MEMORY). Não toca no fluxo atual.
"""
import os
import re
import json
from pathlib import Path
from typing import List, Dict

import numpy as np

# Filtro de QUALIDADE: aprender só B-roll de CONTEÚDO, não efeito/transição/título.
_MIN_BROLL_DUR = float(os.environ.get("LEARN_MIN_DUR", "2.5"))   # < isso = efeito/corte
_EFFECT_PAT = re.compile(
    r"(film ?burn|reveal|transition|transi[cç][aã]o|lower.?third|green.?screen|"
    r"chroma|overlay|glitch|light.?leak|flare|particle|grain|vignette|brilho|"
    r"\btitle\b|lettering|legenda|caption|\blogo\b|\bintro\b|\boutro\b|matte|"
    r"\.png|\.psd|\.ai|\.mogrt|"
    r"\baudio\b|\bvoz\b|voice|narra[cç]|locu[cç]|\.mp3|\.wav|\.aac|\.m4a)", re.I)


def _is_content_broll(clip: Dict) -> bool:
    """True se o clipe parece B-roll de conteúdo (e não efeito/overlay/título)."""
    path = clip.get("path") or ""
    if not path:                                  # sem arquivo real → fora
        return False
    dur = float(clip.get("seq_end", 0) or 0) - float(clip.get("seq_start", 0) or 0)
    if dur < _MIN_BROLL_DUR:                       # curtíssimo = efeito/transição
        return False
    name = clip.get("name") or os.path.basename(path)
    if _EFFECT_PAT.search(name) or _EFFECT_PAT.search(os.path.basename(path)):
        return False
    return True

_MEM_PATH = Path(os.environ.get(
    "VSL_STYLE_MEMORY", str(Path.home() / ".vsl_style_memory.json")))


def _load() -> Dict:
    """Banco persistente: {"examples":[...], "projects":[...]}. Migra o formato
    antigo (lista de exemplos) automaticamente."""
    try:
        data = json.loads(_MEM_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = None
    if isinstance(data, list):                       # formato antigo → migra
        return {"examples": data, "projects": []}
    if isinstance(data, dict):
        data.setdefault("examples", [])
        data.setdefault("projects", [])
        return data
    return {"examples": [], "projects": []}


def _save(db: Dict) -> None:
    try:
        tmp = str(_MEM_PATH) + ".tmp"
        Path(tmp).write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _MEM_PATH)            # escrita atômica
    except Exception as e:
        print(f"[StyleMemory] falha ao salvar: {e}")


def examples() -> List[Dict]:
    return _load().get("examples", [])


def count() -> int:
    return len(examples())


def stats() -> Dict:
    db = _load()
    return {"examples": len(db.get("examples", [])),
            "projects": len(db.get("projects", []))}


def project_learned(project_id: str) -> bool:
    if not project_id:
        return False
    return any(p.get("id") == project_id for p in _load().get("projects", []))


def reset() -> Dict:
    """Zera a memória de estilo (exemplos + projetos). Retorna o que havia antes."""
    before = stats()
    _save({"examples": [], "projects": []})
    return before


def pairs_from_timeline(broll_clips: List[Dict], narration: List[Dict]) -> List[Dict]:
    """Alinha por TEMPO: cada B-roll [seq_start,seq_end] casa com o texto da narração
    que cobre aquela janela. Devolve [{text, broll_path, in_point}]. Função pura."""
    pairs = []
    for c in broll_clips:
        path = c.get("path")
        if not path:
            continue
        if not _is_content_broll(c):           # pula efeito/transição/título/curto
            continue
        s = float(c.get("seq_start", 0) or 0)
        e = float(c.get("seq_end", 0) or 0)
        if e <= s:
            continue
        texts = [seg.get("text", "") for seg in narration
                 if float(seg.get("end", 0) or 0) > s
                 and float(seg.get("start", 0) or 0) < e
                 and seg.get("text")]
        text = " ".join(t.strip() for t in texts if t).strip()
        if not text:
            continue
        pairs.append({"text": text, "broll_path": path,
                      "in_point": float(c.get("in_point", 0) or 0)})
    return pairs


def add_examples(pairs: List[Dict], project_id: str = "", project_name: str = "") -> int:
    """Embeda e guarda os pares novos no banco CUMULATIVO (dedup por texto+broll).
    Registra o projeto (pra não reprocessar e mostrar progresso). Reusa CLIP do índice;
    se não der pra extrair frame, guarda só o lado de texto (visual_emb=None).
    Retorna quantos exemplos foram adicionados."""
    db = _load()
    items = db["examples"]

    added = 0
    if pairs:
        seen = {(it.get("text"), it.get("broll_path")) for it in items}
        new = [p for p in pairs if p.get("text") and p.get("broll_path")
               and (p["text"], p["broll_path"]) not in seen]
        if new:
            from broll_index import embed_text, _extract_frame_ffmpeg, _embed_images_batched
            tembs = embed_text([p["text"] for p in new])      # (N,512) normalizado
            for i, p in enumerate(new):
                visual = None
                try:
                    ts = float(p.get("in_point", 0) or 0) + 0.5
                    img = _extract_frame_ffmpeg(p["broll_path"], ts)
                    if img is not None:
                        visual = [float(x) for x in _embed_images_batched([img])[0]]
                except Exception:
                    visual = None
                items.append({
                    "text": p["text"],
                    "text_emb": [float(x) for x in tembs[i]],
                    "broll_path": p["broll_path"],
                    "filename": os.path.basename(p["broll_path"]),
                    "visual_emb": visual,
                    "project": project_id or project_name or "",
                })
                added += 1

    # Registra/atualiza o projeto no banco (upsert por id)
    if project_id or project_name:
        pid = project_id or project_name
        rec = next((p for p in db["projects"] if p.get("id") == pid), None)
        if rec is None:
            db["projects"].append({"id": pid, "name": project_name or pid, "examples": added})
        else:
            rec["examples"] = (rec.get("examples", 0) or 0) + added
            if project_name:
                rec["name"] = project_name

    _save(db)
    return added


def query(text: str, top_k: int = 1, min_sim: float = 0.0) -> List[Dict]:
    """Exemplos passados mais parecidos com `text` (cosseno sobre o text_emb).
    Retorna [{similarity, text, filename, broll_path, visual_emb}]. Usado pela #L2."""
    items = examples()
    if not items or not (text or "").strip():
        return []
    from broll_index import embed_text
    q = np.asarray(embed_text([text])[0], dtype=np.float32)
    scored = []
    for it in items:
        te = it.get("text_emb")
        if not te:
            continue
        sim = float(np.dot(q, np.asarray(te, dtype=np.float32)))
        if sim >= min_sim:
            scored.append((sim, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{
        "similarity": round(sim, 4),
        "text": it.get("text", ""),
        "filename": it.get("filename"),
        "broll_path": it.get("broll_path"),
        "visual_emb": it.get("visual_emb"),
    } for sim, it in scored[:top_k]]


def _clean_filename(name: str) -> str:
    """Nome de arquivo → frase legível p/ few-shot (tira extensão, traços e ids)."""
    import re
    n = re.sub(r"\.[a-z0-9]+$", "", (name or "").lower())
    n = re.sub(r"[-_.]+", " ", n)
    n = re.sub(r"\b[0-9a-f]{8,}\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def few_shot(text: str, k: int = 2, min_sim: float = 0.5) -> List[Dict]:
    """Exemplos do estilo do editor p/ ancorar o classificador (#L3):
    [{text (trecho passado), scene (B-roll escolhido, legível)}]."""
    out = []
    for e in query(text, top_k=k, min_sim=min_sim):
        scene = _clean_filename(e.get("filename") or "")
        if scene:
            out.append({"text": e.get("text", ""), "scene": scene})
    return out
