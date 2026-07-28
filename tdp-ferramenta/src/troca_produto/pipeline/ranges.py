"""Faixas de tempo + guarda de cobertura (anti over-detection).

Se `product_visible` cobre mais de 30–40% do vídeo, a detecção está errada.
Isso não é opinião: é o sintoma clássico de detector visual local (OpenCV)
marcando o vídeo inteiro de lixo. A guarda abaixo grita antes de você exportar.
"""

from __future__ import annotations

from dataclasses import dataclass, field

WARN_RATIO = 0.30
FAIL_RATIO = 0.40


@dataclass
class Range:
    start: float
    end: float
    kind: str = "visual"
    score: float = 0.0
    label: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def overlaps(self, other: "Range") -> bool:
        return self.start < other.end and other.start < self.end

    def to_dict(self) -> dict:
        data = {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "kind": self.kind,
            "score": round(self.score, 3),
            "label": self.label,
        }
        if self.meta:
            data["meta"] = self.meta
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Range":
        return cls(
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            kind=data.get("kind", "visual"),
            score=float(data.get("score", 0.0) or 0.0),
            label=data.get("label", ""),
            meta=data.get("meta") or {},
        )


def merge_ranges(ranges: list[Range], *, gap: float = 0.75, same_kind: bool = True) -> list[Range]:
    """Junta faixas próximas. `gap` é o buraco máximo tolerado entre elas."""
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda r: (r.kind if same_kind else "", r.start))
    merged: list[Range] = []
    for item in ordered:
        if merged:
            last = merged[-1]
            compatible = (not same_kind) or last.kind == item.kind
            if compatible and item.start - last.end <= gap:
                last.end = max(last.end, item.end)
                last.score = max(last.score, item.score)
                if item.label and item.label not in last.label:
                    last.label = f"{last.label}+{item.label}" if last.label else item.label
                continue
        merged.append(Range(item.start, item.end, item.kind, item.score, item.label, dict(item.meta)))
    merged.sort(key=lambda r: r.start)
    return merged


def total_covered(ranges: list[Range]) -> float:
    """Duração da união das faixas (sem contar sobreposição duas vezes)."""
    flat = merge_ranges([Range(r.start, r.end, "any", r.score) for r in ranges], gap=0.0, same_kind=False)
    return sum(r.duration for r in flat)


def coverage_ratio(ranges: list[Range], duration: float) -> float:
    if duration <= 0:
        return 0.0
    return min(1.0, total_covered(ranges) / duration)


@dataclass
class CoverageReport:
    ratio: float
    level: str  # ok | warn | fail
    message: str

    @property
    def ok(self) -> bool:
        return self.level == "ok"


def check_coverage(
    ranges: list[Range],
    duration: float,
    *,
    warn: float = WARN_RATIO,
    fail: float = FAIL_RATIO,
    what: str = "product_visible",
) -> CoverageReport:
    ratio = coverage_ratio(ranges, duration)
    pct = ratio * 100
    if ratio >= fail:
        return CoverageReport(
            ratio,
            "fail",
            f"{what} cobre {pct:.1f}% do vídeo — isso é over-detection. "
            "Refaça com CLIP-only (TDP_VISUAL_ENGINE=cloud) e confira o contact sheet no olho.",
        )
    if ratio >= warn:
        return CoverageReport(
            ratio,
            "warn",
            f"{what} cobre {pct:.1f}% do vídeo — suspeito. Confira o contact sheet antes de exportar.",
        )
    return CoverageReport(ratio, "ok", f"{what} cobre {pct:.1f}% do vídeo.")


def subtract(base: list[Range], holes: list[Range]) -> list[Range]:
    """Remove de `base` os pedaços cobertos por `holes`."""
    result: list[Range] = []
    for item in base:
        pieces = [(item.start, item.end)]
        for hole in holes:
            nxt: list[tuple[float, float]] = []
            for start, end in pieces:
                if hole.end <= start or hole.start >= end:
                    nxt.append((start, end))
                    continue
                if hole.start > start:
                    nxt.append((start, hole.start))
                if hole.end < end:
                    nxt.append((hole.end, end))
            pieces = nxt
        for start, end in pieces:
            if end - start > 1e-6:
                result.append(Range(start, end, item.kind, item.score, item.label, dict(item.meta)))
    return result
