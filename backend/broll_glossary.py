"""
Glossário conceito→visual concreto p/ B-roll de VSL.

Quando a NARRAÇÃO (ou a descrição do classificador) menciona um conceito — joelho,
médico, cabelo, liberdade, confiança, barriga... — injeta na query de busca FRASES
VISUAIS CONCRETAS em inglês ("a person holding their knee in pain"), no estilo das
legendas BLIP dos clipes. Isso força o match LITERAL (joelho→joelho, nunca barriga) e
conserta VSL em português: os GATILHOS são PT+EN, mas as frases injetadas são sempre EN
(o espaço de busca — legendas/CLIP — é inglês).

Dados em broll_glossary_data.json (gerado por workflow; editável à mão). Desliga com
GLOSSARY_DISABLED=1; ajusta o nº de frases com GLOSSARY_MAX_PHRASES.
"""
import os
import re
import json
from typing import List, Dict, Tuple

_DATA_PATH = os.path.join(os.path.dirname(__file__), "broll_glossary_data.json")
MAX_PHRASES = int(os.environ.get("GLOSSARY_MAX_PHRASES", "3"))
_GLOSSARY_ON = os.environ.get("GLOSSARY_DISABLED", "0") != "1"


def _load() -> list:
    try:
        with open(_DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Glossário] não carregou {os.path.basename(_DATA_PATH)}: {e}")
        return []


def _compile(entries: list) -> list:
    """Pré-compila (regex, tamanho) por trigger (word-boundary que respeita acentos/dígitos)."""
    out = []
    for e in entries:
        pats = []
        for t in e.get("triggers", []):
            t = str(t).strip().lower()
            if len(t) < 3:                       # gatilho curto demais → ignora (falso match)
                continue
            # plural opcional (s/es) à direita pega "joelho→joelhos", "confiante→
            # confiantes" sem reabrir falso-match por substring (a borda à esquerda
            # ainda bloqueia "molho"→"olho").
            pats.append((re.compile(r"(?<![a-zà-ÿ0-9])" + re.escape(t) + r"(?:es|s)?(?![a-zà-ÿ0-9])"), len(t)))
        if pats and e.get("visuals"):
            out.append({
                "concept": e["concept"],
                "pats": pats,
                "visuals": list(e["visuals"]),
                "avoid": list(e.get("avoid", [])),
                "priority": e.get("priority", "normal"),
            })
    return out


_ENTRIES = _load()
_COMPILED = _compile(_ENTRIES)


def match_concepts(text: str) -> List[Dict]:
    """Conceitos do glossário detectados no texto. Ordena: prioridade alta primeiro,
    depois pelo gatilho que REALMENTE casou mais específico (mais longo) — pra os literais
    fortes ('dor no joelho') virem na frente de gatilhos vagos ('vida')."""
    t = (text or "").lower()
    if not t:
        return []
    hits = []
    for e in _COMPILED:
        best = 0
        for pat, tlen in e["pats"]:
            if pat.search(t) and tlen > best:
                best = tlen
        if best:
            hits.append({**e, "match_len": best})
    hits.sort(key=lambda e: (e["priority"] != "high", -e["match_len"]))
    return hits


def expand(text: str, max_phrases: int = None) -> Tuple[List[str], List[str]]:
    """(frases_visuais, avoid) pros conceitos detectados — capado p/ não diluir o embedding."""
    cap = MAX_PHRASES if max_phrases is None else max_phrases
    phrases: List[str] = []
    avoid: List[str] = []
    seen = set()
    for e in match_concepts(text):
        for v in e["visuals"]:
            if v not in seen:
                phrases.append(v)
                seen.add(v)
            if len(phrases) >= cap:
                break
        for a in e["avoid"]:
            if a not in avoid:
                avoid.append(a)
        if len(phrases) >= cap:
            break
    return phrases, avoid


def enrich_query(query: str, narration: str = "") -> str:
    """Acrescenta à query as frases visuais concretas dos conceitos detectados (na query
    E na narração). Sem conceito (ou desligado) → devolve a query original, intacta."""
    if not _GLOSSARY_ON:
        return query
    phrases, _ = expand((query or "") + " . " + (narration or ""))
    if not phrases:
        return query
    extra = ", ".join(phrases)
    return f"{query}. {extra}" if query else extra


def stats() -> dict:
    return {"entries": len(_COMPILED),
            "triggers": sum(len(e["pats"]) for e in _COMPILED)}
