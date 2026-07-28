"""CSV e relatório em texto — apoio pra revisão humana."""

from __future__ import annotations

import csv
import os
from pathlib import Path

from ..media.ffmpeg import frames_to_timecode, seconds_to_frames
from .premiere_xml import KIND_PT

CSV_HEADER = ["#", "tipo", "inicio_s", "fim_s", "timecode", "duracao_s", "score", "descricao", "produto_novo"]


def timecode(seconds: float, fps: float = 30.0) -> str:
    return frames_to_timecode(seconds_to_frames(seconds, fps), fps)


def write_csv(ranges, path: str | os.PathLike[str], *, fps: float = 30.0, new_assets: dict | None = None) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    assets = dict(new_assets or {})
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        for index, item in enumerate(ranges):
            writer.writerow(
                [
                    index + 1,
                    KIND_PT.get(item.kind, item.kind),
                    f"{item.start:.3f}",
                    f"{item.end:.3f}",
                    timecode(item.start, fps),
                    f"{item.duration:.3f}",
                    f"{item.score:.3f}",
                    item.label,
                    assets.get(index, assets.get(str(index), "")),
                ]
            )
    return str(target)


def build_report(
    *,
    briefing,
    ranges,
    duration: float,
    coverage,
    rebrand_items=None,
    warnings=None,
    fps: float = 30.0,
) -> str:
    """Relatório em markdown — é o que vai junto com a entrega."""
    by_kind: dict[str, list] = {}
    for item in ranges:
        by_kind.setdefault(item.kind, []).append(item)

    lines: list[str] = []
    lines.append(f"# TDP — {briefing.old_product_name} → {briefing.new_product_name}")
    lines.append("")
    lines.append(f"- vídeo: `{briefing.video}`")
    lines.append(f"- duração: {duration / 60:.1f} min ({duration:.1f} s)")
    lines.append(f"- tipo de troca: `{briefing.swap_kind}` · alvo: `{briefing.swap_target}`")
    lines.append(f"- aparições encontradas: **{len(ranges)}**")
    for kind, items in sorted(by_kind.items()):
        total = sum(i.duration for i in items)
        lines.append(f"  - {KIND_PT.get(kind, kind)}: {len(items)} ({total:.1f}s)")
    lines.append("")
    lines.append(f"## Cobertura — {coverage.level.upper()}")
    lines.append(coverage.message)
    if coverage.level != "ok":
        lines.append("")
        lines.append("> Acima de 30–40% é over-detection. Refaça com CLIP-only e confira o contact sheet no olho.")
    lines.append("")

    if warnings:
        lines.append("## Avisos")
        for item in warnings:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("## Aparições")
    lines.append("")
    lines.append("| # | tipo | timecode | dur | score | descrição |")
    lines.append("|---|------|----------|-----|-------|-----------|")
    for index, item in enumerate(ranges):
        lines.append(
            f"| {index + 1} | {KIND_PT.get(item.kind, item.kind)} | {timecode(item.start, fps)} | "
            f"{item.duration:.1f}s | {item.score:.2f} | {item.label} |"
        )
    lines.append("")

    if rebrand_items:
        lines.append("## Rebrand de voz")
        lines.append("")
        lines.append("| # | timecode | locutor | modo | voz | texto |")
        lines.append("|---|----------|---------|------|-----|-------|")
        for index, item in enumerate(rebrand_items):
            voice = item.voice.voice_id if item.voice else "—"
            lines.append(
                f"| {index + 1} | {timecode(item.start, fps)} | {item.speaker_id} ({item.role}) | "
                f"{item.mode} | {voice} | {item.text_to_speak[:60]} |"
            )
        lines.append("")

    lines.append("## Entrega")
    lines.append("")
    lines.append("- `troca_COMPLETO.xml` — V1 cortado+colorido (+marcadores) · V2 produto novo · A1 áudio original · A2+ vozes")
    lines.append("- `troca.csv` — a mesma lista pra conferir na planilha")
    lines.append("- `contact_sheet.jpg` — CONFIRA os frames antes de entregar")
    return "\n".join(lines) + "\n"


def write_report(text: str, path: str | os.PathLike[str]) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return str(target)
