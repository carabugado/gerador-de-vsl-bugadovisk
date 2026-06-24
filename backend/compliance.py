"""
Layer de compliance (CLAUDE.md, seção 4 — INEGOCIÁVEL).

Valida CADA b-roll escolhido ANTES de chegar ao editor. Nenhum asset que viole
as regras deve ser inserido na timeline. Bloqueios:
  - universais (prescrição/diagnóstico, antes/depois, nudez/sexual, medicamento
    farmacêutico, cirurgia, claim de cura)
  - por vertical (WL/ED/NR/PT/VS/JT/FG) — carregados de compliance_rules.json

O casamento é feito contra o NOME do arquivo do b-roll + a direção visual
pretendida (o que se vai MOSTRAR), não contra a narração — narração pode citar
"cirurgia" sem que o b-roll seja problemático.

Cada bloqueio é logado em compliance_log.jsonl com contexto (motivo, asset,
vertical, segmento) — erros nunca silenciosos.
"""
import os
import re
import json
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from matcher import _clean_name

_RULES_PATH = Path(__file__).with_name("compliance_rules.json")
_LOG_PATH = Path(__file__).with_name("compliance_log.jsonl")

# Status que significam "tem b-roll de verdade pra validar"
_ACTIVE = {"ok", "review", "generated"}


def _load_rules() -> dict:
    try:
        return json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Compliance] não carregou compliance_rules.json: {e} — compliance DESLIGADO")
        return {}


def detect_vertical(context: dict, rules: dict) -> str:
    """Mapeia o nicho/produto do contexto para um código de vertical (ou '')."""
    if not context:
        return ""
    # vertical explícito no contexto vence
    explicit = (context.get("vertical") or "").strip().upper()
    vmap = rules.get("vertical_keywords", {})
    if explicit in vmap:
        return explicit

    prod = context.get("product") or {}
    hay = " ".join([
        context.get("niche", "") or "",
        context.get("avatar", "") or "",
        prod.get("what", "") or "",
        prod.get("mechanism", "") or "",
        prod.get("name", "") or "",
    ]).lower()

    best, best_hits = "", 0
    for vert, terms in vmap.items():
        hits = sum(1 for t in terms if t.strip().lower() in hay)
        if hits > best_hits:
            best, best_hits = vert, hits
    return best


def vertical_from_path(path: str, rules: dict) -> str:
    """Fallback: deduz a vertical pelo CAMINHO da pasta de B-rolls (ex.: '.../JOELHO/'
    → JT) quando não há doc/contexto. Usa as mesmas keywords da detecção por contexto."""
    if not path:
        return ""
    hay = str(path).lower()
    best, best_hits = "", 0
    for vert, terms in rules.get("vertical_keywords", {}).items():
        hits = sum(1 for t in terms if t.strip().lower() in hay)
        if hits > best_hits:
            best, best_hits = vert, hits
    return best


def _matches_rule(text: str, rule: dict) -> bool:
    """Regra casa se: TODOS os termos de 'all' aparecem, ou QUALQUER de 'any'."""
    allt = [t.lower() for t in rule.get("all", [])]
    anyt = [t.lower() for t in rule.get("any", [])]
    if allt and all(t in text for t in allt):
        return True
    if anyt and any(t in text for t in anyt):
        return True
    return False


def _check_asset(asset_text: str, vertical: str, rules: dict) -> Optional[str]:
    """Devolve o motivo do bloqueio, ou None se o asset passa."""
    for rule in rules.get("universal_block", []):
        if _matches_rule(asset_text, rule):
            return rule.get("reason", "viola regra universal")
    for rule in rules.get("by_vertical", {}).get(vertical, []):
        if _matches_rule(asset_text, rule):
            return rule.get("reason", f"viola regra da vertical {vertical}")
    return None


def _log_block(entry: dict) -> None:
    try:
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[Compliance] falha ao logar bloqueio: {e}")


def apply_compliance(segments: List[Dict], matches: List[Dict],
                     context: dict = None) -> Dict:
    """Valida cada b-roll ativo in-place. Bloqueia (status 'blocked_compliance')
    e loga os que violam. Retorna {'blocked': n, 'vertical': code}."""
    rules = _load_rules()
    if not rules:
        return {"blocked": 0, "vertical": ""}

    vertical = detect_vertical(context or {}, rules)
    blocked = 0
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")

    for seg, m in zip(segments, matches):
        if m.get("status") not in _ACTIVE or not m.get("broll_path"):
            continue

        # Clipe da pasta +18 (ED): local, curado pelo editor pra uma VSL adulta — o
        # conteúdo sexual/nudez é INTENCIONAL e o clip nunca sai da máquina (sem risco
        # de ban em anúncio/nuvem). Não bloqueia (senão barra justo o que a VSL ED
        # precisa). Biblioteca/Pexels/gerados seguem sob compliance normal.
        if m.get("broll_source") == "ed":
            continue

        # texto do ASSET: nome do arquivo + LEGENDA do clipe (o que ele REALMENTE
        # mostra, do BLIP) + direção visual pretendida. A legenda é o melhor sinal de
        # conteúdo — pega clipe de nome-hash que mostra cirurgia/medicamento/etc.
        caption = ""
        try:
            import asset_tagger
            tg = asset_tagger.load_tags(m.get("broll_path") or "") or {}
            caption = str(tg.get("caption") or "")
        except Exception:
            caption = ""
        asset_text = " ".join([
            _clean_name(m.get("broll_filename", "") or ""),
            caption.lower(),
            (seg.get("visual_query", "") or "").lower(),
            (seg.get("scene_type", "") or "").lower(),
        ])

        reason = _check_asset(asset_text, vertical, rules)
        if reason:
            _log_block({
                "ts": ts,
                "vertical": vertical,
                "reason": reason,
                "asset": m.get("broll_filename", ""),
                "segment_text": (seg.get("text", "") or "")[:160],
                "visual_query": seg.get("visual_query", ""),
            })
            m["broll_path"] = None
            m["broll_filename"] = None
            m["status"] = "blocked_compliance"
            m["select_reason"] = f"⛔ Compliance: {reason}"
            blocked += 1

    return {"blocked": blocked, "vertical": vertical}
