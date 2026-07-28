"""Revisão humana das faixas detectadas.

A ferramenta detecta; quem decide é você. Aqui dá pra derrubar falso positivo,
recuperar o que foi derrubado e apontar qual arte do produto novo entra em
cada aparição.
"""

from __future__ import annotations

from .assets.packs import pick_pack
from .briefing import Briefing
from .export.premiere_xml import KIND_PT
from .export.reports import timecode
from .project import State


def parse_indices(spec: str, *, total: int | None = None) -> list[int]:
    """'1,3,5-7' → [0, 2, 4, 5, 6] (entrada 1-based, saída 0-based)."""
    out: list[int] = []
    for chunk in (spec or "").replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk[1:]:
            head, _, tail = chunk.partition("-")
            try:
                start, end = int(head), int(tail)
            except ValueError:
                continue
            out.extend(range(start, end + 1))
        else:
            try:
                out.append(int(chunk))
            except ValueError:
                continue
    zero_based = sorted({i - 1 for i in out if i >= 1})
    if total is not None:
        zero_based = [i for i in zero_based if i < total]
    return zero_based


def drop(state: State, indices: list[int]) -> State:
    state.dropped = sorted(set(state.dropped) | set(indices))
    return state


def keep(state: State, indices: list[int]) -> State:
    state.dropped = sorted(set(state.dropped) - set(indices))
    return state


def keep_only(state: State, indices: list[int]) -> State:
    wanted = set(indices)
    state.dropped = sorted(i for i in range(len(state.ranges)) if i not in wanted)
    return state


def set_asset(state: State, index: int, asset: str) -> State:
    state.new_assets[str(index)] = asset
    return state


def auto_assign_assets(state: State, briefing: Briefing) -> State:
    """Escolhe a arte do produto novo pra cada aparição.

    Quando o trecho fala quantidade ("6 frascos", "free +3", "3 1"), usa o pack
    correspondente do briefing; senão, cai na primeira arte de `new_assets`.
    """
    default = briefing.new_assets[0] if briefing.new_assets else ""
    for index, item in enumerate(state.ranges):
        if str(index) in state.new_assets:
            continue
        spoken = (item.meta or {}).get("text", "") or item.label
        pack = pick_pack(spoken, briefing.quantity_map) if briefing.quantity_map else ""
        chosen = pack or default
        if chosen:
            state.new_assets[str(index)] = chosen
    return state


def assets_for_export(state: State) -> dict:
    """{índice → caminho}, já sem as faixas derrubadas."""
    dropped = set(state.dropped)
    out: dict = {}
    position = 0
    for index in range(len(state.ranges)):
        if index in dropped:
            continue
        asset = state.new_assets.get(str(index)) or state.new_assets.get(index)
        if asset:
            out[position] = asset
        position += 1
    return out


def format_table(state: State) -> str:
    """Tabela de revisão pro terminal."""
    if not state.ranges:
        return "nenhuma aparição detectada."
    dropped = set(state.dropped)
    lines = [
        f"{'#':>3}  {'':1} {'tipo':<12} {'timecode':<12} {'dur':>6} {'score':>6}  descrição",
        "-" * 96,
    ]
    for index, item in enumerate(state.ranges):
        mark = "x" if index in dropped else "•"
        asset = state.new_assets.get(str(index), "")
        label = item.label or ""
        if asset:
            label = f"{label}   → {asset}"
        lines.append(
            f"{index + 1:>3}  {mark:1} {KIND_PT.get(item.kind, item.kind):<12} "
            f"{timecode(item.start, state.fps):<12} {item.duration:>5.1f}s {item.score:>6.2f}  {label}"
        )
    kept = len(state.ranges) - len(dropped)
    lines.append("-" * 96)
    lines.append(f"{kept} mantidas · {len(dropped)} derrubadas · cobertura {state.coverage.get('ratio', 0) * 100:.1f}%")
    if state.coverage.get("level") not in ("ok", "", None):
        lines.append(f"⚠ {state.coverage.get('message')}")
    return "\n".join(lines)
