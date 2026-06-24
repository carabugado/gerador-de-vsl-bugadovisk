"""
Controle de timing e ritmo da inserção de B-roll (CLAUDE.md, seção 5).

Roda DEPOIS da seleção (broll_select.select) e ANTES de mandar pro host.jsx.
Recebe segments + matches (1:1 por índice) e ajusta/bloqueia cada b-roll para
respeitar as regras de ritmo de uma VSL:

  - Duração: 2-5s por b-roll (trimma o excesso; descarta se não couber 2s)
  - Gap mínimo: 3s de áudio puro entre dois b-rolls (empurra o início; descarta
    se ao empurrar não sobrar 2s dentro do segmento)
  - Nunca durante: CTA, revelação de preço, garantia/reembolso, nome de depoente
  - Máximo 3 b-rolls consecutivos sem uma pausa maior (>= PAUSE_RESET)
  - Hook respira HOOK_BREATH antes do primeiro b-roll
  - Transição por bloco: hook=cut, mecanismo/solução/prova=dissolve, cta=nenhum

Cada b-roll bloqueado/descartado vira status "blocked" com motivo legível (o
painel mostra; nada de lixo entra na timeline).
"""
import os
import re
from typing import List, Dict, Optional

# Regras de ritmo — tunáveis por env. Por PADRÃO o ritmo NÃO inventa pausas: o editor
# pediu "coloca o máximo de b-rolls que encaixa, não fica decidindo pausa por conta
# própria". Então MIN_GAP/HOOK_BREATH=0 e MAX_CONSEC altíssimo deixam tudo o que casou
# entrar, colado. A camada só (1) trimma a duração e (2) protege os momentos de dinheiro
# (preço/garantia/CTA). Pra voltar a "respirar" a VSL, suba MIN_GAP / baixe MAX_CONSEC via env.
MIN_DUR     = float(os.environ.get("RHYTHM_MIN_DUR", "2.0"))    # duração mínima de um b-roll
MAX_DUR     = float(os.environ.get("RHYTHM_MAX_DUR", "5.0"))    # duração máxima — trimma o excesso
MIN_GAP     = float(os.environ.get("RHYTHM_MIN_GAP", "0.0"))    # 0 = sem gap forçado entre b-rolls
HOOK_BREATH = float(os.environ.get("RHYTHM_HOOK_BREATH", "0.0"))  # 0 = 1º b-roll não espera
MAX_CONSEC  = int(os.environ.get("RHYTHM_MAX_CONSEC", "999"))   # 999 = sem limite de consecutivos
PAUSE_RESET = float(os.environ.get("RHYTHM_PAUSE_RESET", "6.0"))  # gap que zera os consecutivos

# Proteção dos momentos de dinheiro (preço/garantia/CTA não recebem b-roll). Ligada por
# padrão — é prática de VSL, não "pausa". Desligue tudo com RHYTHM_PROTECT_MONEY=0.
PROTECT_MONEY = os.environ.get("RHYTHM_PROTECT_MONEY", "1") != "0"

# Arcos/seções que NUNCA recebem b-roll
_BLOCK_ARCS = {"cta"}

# Momentos protegidos detectados pelo TEXTO da narração
_PRICE_RE = re.compile(
    r'(\$\s?\d|\bR\$\s?\d|\d+\s?(dollars|reais|bucks)\b|\bprice\b|\bpre[çc]o\b|'
    r'\bonly\s+\$?\d|\bapenas\s+R?\$?\d)', re.I
)
_GUARANTEE_RE = re.compile(
    r'\b(guarantee|guaranteed|money[-\s]?back|refund|garantia|reembolso|'
    r'30[-\s]?day|60[-\s]?day|90[-\s]?day)\b', re.I
)

# Status que significam "tem b-roll de verdade pra inserir"
_ACTIVE = {"ok", "review", "generated"}


def _transition_for(arc: str) -> str:
    if arc in ("mechanism", "solution", "proof", "offer"):
        return "dissolve"
    if arc == "hook":
        return "cut"
    return "cut"


def _block(match: Dict, reason: str) -> None:
    """Tira o b-roll do match (não insere) e marca o porquê."""
    match["broll_path"]     = None
    match["broll_filename"] = None
    match["status"]         = "blocked"
    match["select_reason"]  = reason
    match["transition"]     = "none"


def apply_rhythm(segments: List[Dict], matches: List[Dict],
                 max_dur: float = None, min_dur: float = None) -> Dict[str, int]:
    """Ajusta `matches` in-place. Retorna contadores para stats.
    max_dur: sobrepõe a duração máxima do b-roll (Modo Qualidade Alta usa clipes curtos).
    min_dur: sobrepõe a duração mínima (vertical ED usa ~1s pra frase curta sexual
             também receber clipe, em vez de virar buraco)."""
    counts = {"trimmed": 0, "pushed": 0, "blocked": 0}
    max_d = float(max_dur) if max_dur else MAX_DUR
    min_d = float(min_dur) if min_dur else MIN_DUR

    last_end: Optional[float] = None   # fim do último b-roll inserido
    prev_end: Optional[float] = None   # fim do b-roll anterior (p/ medir a pausa)
    consec = 0
    first_done = False
    last_group = None                  # #Fase3: grupo de enumeração do último b-roll

    for seg, m in zip(segments, matches):
        if m.get("status") not in _ACTIVE or not m.get("broll_path"):
            # áudio puro: uma pausa real reseta a sequência de consecutivos
            if last_end is not None and seg.get("start", 0) - last_end >= PAUSE_RESET:
                consec = 0
            continue

        arc = (seg.get("arc_position") or "").lower()
        text = seg.get("text", "")

        # 1) momentos de dinheiro nunca recebem b-roll (desligável: RHYTHM_PROTECT_MONEY=0)
        if PROTECT_MONEY:
            if arc in _BLOCK_ARCS:
                _block(m, "CTA não recebe b-roll (ritmo)")
                counts["blocked"] += 1
                continue
            if _PRICE_RE.search(text):
                _block(m, "Revelação de preço — sem b-roll")
                counts["blocked"] += 1
                continue
            if _GUARANTEE_RE.search(text):
                _block(m, "Garantia/reembolso — sem b-roll")
                counts["blocked"] += 1
                continue

        s = float(m["start"])
        seg_end = float(seg.get("end", m["end"]))
        e = min(float(m["end"]), seg_end)

        # #Fase3: sub-slots da MESMA enumeração são rajada intencional → sem gap nem
        # limite de consecutivos entre irmãos (queremos os 3 ingredientes seguidos).
        group = seg.get("_enum_group")
        same_group = group is not None and group == last_group

        # 2) hook respira antes do primeiro b-roll
        if not first_done and s < HOOK_BREATH:
            s = HOOK_BREATH

        # 3) gap mínimo de áudio puro (pulado dentro da mesma enumeração)
        if not same_group and last_end is not None and s < last_end + MIN_GAP:
            s = last_end + MIN_GAP
            counts["pushed"] += 1

        # 4) máximo de consecutivos sem pausa (não conta dentro da enumeração)
        if not same_group and last_end is not None:
            if s - last_end >= PAUSE_RESET:
                consec = 0
            elif consec >= MAX_CONSEC:
                _block(m, f"Máx. {MAX_CONSEC} b-rolls seguidos — pausa de respiro")
                counts["blocked"] += 1
                prev_end = last_end
                continue

        # 5) duração dentro da janela do segmento (max_d; Qualidade Alta = mais curto)
        if e > s + max_d:
            e = s + max_d
            counts["trimmed"] += 1
        e = min(e, seg_end)

        if e - s < min_d:
            _block(m, f"Sem espaço para {min_d:.0f}s (gap/ritmo)")
            counts["blocked"] += 1
            continue

        # aprovado: grava ajustes e transição
        m["start"]      = round(s, 3)
        m["end"]        = round(e, 3)
        m["transition"] = _transition_for(arc)

        prev_end = last_end
        last_end = e
        consec = consec + 1 if not same_group else consec
        first_done = True
        last_group = group

    return counts
