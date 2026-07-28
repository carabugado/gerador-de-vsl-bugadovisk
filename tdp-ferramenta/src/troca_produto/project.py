"""Pasta de projeto de uma demanda (tdp_projeto/) e seu estado."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .briefing import Briefing
from .pipeline.ranges import Range

STATE_VERSION = 1


@dataclass
class State:
    version: int = STATE_VERSION
    video: str = ""
    duration: float = 0.0
    fps: float = 30.0
    width: int = 1920
    height: int = 1080
    ranges: list[Range] = field(default_factory=list)
    mentions: list[dict] = field(default_factory=list)
    speakers: list[dict] = field(default_factory=list)
    rebrand: list[dict] = field(default_factory=list)
    new_assets: dict = field(default_factory=dict)
    dropped: list[int] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    engine: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "video": self.video,
            "duration": self.duration,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "ranges": [r.to_dict() for r in self.ranges],
            "mentions": self.mentions,
            "speakers": self.speakers,
            "rebrand": self.rebrand,
            "new_assets": self.new_assets,
            "dropped": self.dropped,
            "coverage": self.coverage,
            "warnings": self.warnings,
            "engine": self.engine,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "State":
        data = data or {}
        return cls(
            version=int(data.get("version", STATE_VERSION)),
            video=data.get("video", ""),
            duration=float(data.get("duration", 0.0) or 0.0),
            fps=float(data.get("fps", 30.0) or 30.0),
            width=int(data.get("width", 1920) or 1920),
            height=int(data.get("height", 1080) or 1080),
            ranges=[Range.from_dict(r) for r in data.get("ranges") or []],
            mentions=data.get("mentions") or [],
            speakers=data.get("speakers") or [],
            rebrand=data.get("rebrand") or [],
            new_assets=data.get("new_assets") or {},
            dropped=[int(i) for i in data.get("dropped") or []],
            coverage=data.get("coverage") or {},
            warnings=data.get("warnings") or [],
            engine=data.get("engine", ""),
        )

    @property
    def kept_ranges(self) -> list[Range]:
        """Faixas que sobraram depois da revisão humana."""
        dropped = set(self.dropped)
        return [r for i, r in enumerate(self.ranges) if i not in dropped]


class Project:
    """Layout da pasta de trabalho de uma demanda."""

    def __init__(self, directory: str | os.PathLike[str]):
        self.dir = Path(directory)

    # ── caminhos ────────────────────────────────────────────────────────
    @property
    def briefing_path(self) -> Path:
        return self.dir / "briefing.json"

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    @property
    def cache_dir(self) -> Path:
        return self.dir / "cache"

    @property
    def transcript_path(self) -> Path:
        return self.cache_dir / "transcript.json"

    @property
    def frames_dir(self) -> Path:
        return self.dir / "frames"

    @property
    def audio_dir(self) -> Path:
        return self.dir / "audio"

    @property
    def base_audio(self) -> Path:
        """Áudio LIMPO da VSL. É sempre aqui que a diarização roda."""
        return self.audio_dir / "base.wav"

    @property
    def voice_dir(self) -> Path:
        return self.dir / "voz"

    @property
    def export_dir(self) -> Path:
        return self.dir / "export"

    @property
    def xml_path(self) -> Path:
        return self.export_dir / "troca_COMPLETO.xml"

    @property
    def csv_path(self) -> Path:
        return self.export_dir / "troca.csv"

    @property
    def report_path(self) -> Path:
        return self.export_dir / "report.md"

    @property
    def contact_sheet_path(self) -> Path:
        return self.export_dir / "contact_sheet.jpg"

    # ── ciclo de vida ───────────────────────────────────────────────────
    def ensure(self) -> "Project":
        for path in (self.dir, self.cache_dir, self.frames_dir, self.audio_dir, self.voice_dir, self.export_dir):
            path.mkdir(parents=True, exist_ok=True)
        return self

    def save_state(self, state: State) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)
        return self.state_path

    def load_state(self) -> State:
        if not self.state_path.is_file():
            return State()
        return State.from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))

    def save_briefing(self, briefing: Briefing) -> Path:
        return briefing.save(self.briefing_path)

    def load_briefing(self, path: str | os.PathLike[str] | None = None) -> Briefing:
        return Briefing.load(path or self.briefing_path)
