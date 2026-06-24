"""
MELHORIA 3 — Scoring multicritério da busca de B-roll.

Recebe o PERFIL semântico do trecho (broll_classifier) e ranqueia TODOS os assets
tagados (asset_tagger) por uma tabela de pontos. Substitui o keyword matching
quando há tags + classificação; cai no CLIP atual quando não há (server decide).

Tabela:
  +10  emoção do perfil ∈ asset.emotions
  +8   visual_type do perfil ∈ asset.visual_type
  +5   energy_level igual
  +6   block_type ∈ asset.suitable_blocks
  ELIM block_type ∈ asset.unsuitable_blocks
  +5   vertical do produto ∈ asset.verticals
  +3   por keyword do asset que aparece nos search_terms
  ELIM overlap entre asset.keywords e perfil.avoid
  +4   histórico bom (accepted/used > 0.7)
  -8   histórico ruim (rejected/used > 0.5)
  -12  já usado nesta sessão
  +2   nunca usado (dar chance)

Score <= 0 → eliminado. Top 3 por trecho. Desempate: menos usado.
Se o melhor < GENERATE_THRESHOLD → aciona geração IA (visual_description vira prompt).
"""
from typing import List, Dict, Optional, Tuple

GENERATE_THRESHOLD = 10   # melhor abaixo disso → gerar com IA
OK_SCORE = 16             # >= → "ok" (insere direto); entre 10 e 16 → "review"
MIN_BROLL_DURATION = 3.0


def _norm_set(items) -> set:
    return {str(x).strip().lower() for x in (items or []) if str(x).strip()}


def score_asset(asset: Dict, profile: Dict, vertical: str,
                used_paths: set) -> Tuple[Optional[float], str]:
    """Pontua UM asset para UM perfil. Retorna (score, motivo). score=None → eliminado."""
    tags = asset.get("tags") or {}
    block = str(profile.get("block_type", "")).strip().lower()

    # Eliminações duras
    if block and block in _norm_set(tags.get("unsuitable_blocks")):
        return None, "bloco inadequado"
    avoid = _norm_set(profile.get("avoid"))
    kws = _norm_set(tags.get("keywords"))
    if avoid and (avoid & kws):
        return None, "bate com 'avoid'"
    if tags.get("compliance_safe") is False:
        return None, "compliance"

    score = 0.0
    why = []

    emo = str(profile.get("emotion", "")).strip().lower()
    if emo and emo in _norm_set(tags.get("emotions")):
        score += 10; why.append("emoção+10")

    vtype = str(profile.get("visual_type", "")).strip().lower()
    if vtype and vtype in _norm_set(tags.get("visual_type")):
        score += 8; why.append("tipo+8")

    if profile.get("energy_level") and \
       str(profile["energy_level"]).lower() == str(tags.get("energy_level", "")).lower():
        score += 5; why.append("energia+5")

    if block and block in _norm_set(tags.get("suitable_blocks")):
        score += 6; why.append("bloco+6")

    if vertical and vertical.upper() in {v.upper() for v in (tags.get("verticals") or [])}:
        score += 5; why.append("vertical+5")

    # keyword: cada keyword do asset que aparece nos search_terms do perfil
    search_blob = " ".join(profile.get("search_terms", [])).lower()
    kw_hits = sum(1 for k in (tags.get("keywords") or []) if k.strip().lower() in search_blob)
    if kw_hits:
        score += 3 * kw_hits; why.append(f"kw+{3*kw_hits}")

    used = tags.get("times_used", 0) or 0
    acc = tags.get("times_accepted", 0) or 0
    rej = tags.get("times_rejected", 0) or 0
    if used > 0 and acc / used > 0.7:
        score += 4; why.append("hist+4")
    if used > 0 and rej / used > 0.5:
        score -= 8; why.append("hist-8")

    if asset["path"] in used_paths:
        score -= 12; why.append("repetido-12")
    if used == 0:
        score += 2; why.append("novo+2")

    if score <= 0:
        return None, "score<=0"
    return score, ", ".join(why)


def rank_segment(profile: Dict, assets: List[Dict], vertical: str,
                 used_paths: set, top_k: int = 3) -> List[Dict]:
    """Top-K assets para UM perfil. Desempate: menos usado."""
    scored = []
    for a in assets:
        sc, why = score_asset(a, profile, vertical, used_paths)
        if sc is None:
            continue
        scored.append((sc, (a.get("tags") or {}).get("times_used", 0), a, why))
    # score desc, depois menos usado
    scored.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for sc, _used, a, why in scored[:top_k]:
        out.append({
            "path": a["path"],
            "filename": a["filename"],
            "clean_name": (a.get("tags") or {}).get("filename", a["filename"]),
            "duration": a.get("duration", 0),
            "score": round(float(sc), 2),
            "source": a.get("_source", "project"),
            "why": why,
        })
    return out


def select(segments: List[Dict], profiles: List[Dict], assets: List[Dict],
           vertical: str = "") -> Tuple[List[Dict], List[Dict]]:
    """Produz (ranked, matches) no MESMO formato de matcher.rank_segments + broll_select.

    - ranked[i] = {index, skip, candidates:[...top3...]}
    - matches[i] = make_result(...) — status ok|review|no_broll|skip
    Quando o melhor candidato fica abaixo do limiar, marca p/ geração IA usando a
    visual_description do classificador como prompt (seg["ugc_prompt"]).
    """
    from matcher import make_result

    used: set = set()
    ranked: List[Dict] = []
    matches: List[Dict] = []

    for i, seg in enumerate(segments):
        profile = profiles[i] or {}
        duration_needed = seg["end"] - seg["start"]

        # sub-slots de enumeração (rajada de "3 ingredientes") são curtos de propósito —
        # isentos do piso de 3s (o ritmo cuida do timing). Mesma regra do broll_search.
        if duration_needed < MIN_BROLL_DURATION and not seg.get("_enum_group"):
            ranked.append({"index": i, "skip": True, "candidates": []})
            matches.append(make_result(seg, None, "skip"))
            continue

        # filtra por duração suficiente (80% da janela)
        usable_assets = [a for a in assets
                         if a.get("duration", 0) >= duration_needed * 0.8]
        cands = rank_segment(profile, usable_assets, vertical, used, top_k=3)
        ranked.append({"index": i, "skip": False, "candidates": cands})

        best = cands[0]["score"] if cands else 0
        if not cands or best < GENERATE_THRESHOLD:
            # fallback p/ geração IA — visual_description vira o prompt
            desc = profile.get("visual_description", "") or seg.get("visual_query", "")
            if desc:
                seg["ugc_prompt"] = desc
            reason = (f"gerar IA: {desc[:60]}" if desc
                      else "nenhum asset com score suficiente")
            matches.append(make_result(seg, None, "no_broll", reason))
            continue

        chosen = cands[0]
        used.add(chosen["path"])
        status = "ok" if chosen["score"] >= OK_SCORE else "review"
        matches.append(make_result(
            seg, chosen, status,
            f"scoring {chosen['score']:.0f} ({chosen.get('why','')})"))

    return ranked, matches
