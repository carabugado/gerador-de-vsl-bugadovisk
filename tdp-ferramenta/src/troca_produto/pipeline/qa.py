"""QA do vídeo FINAL: caça sobras do produto antigo.

Roda no arquivo que vai pro cliente. Áudio (fuzzy) + visual (CLIP).
Exit code 2 = achou sobra. Serve pra travar entrega.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..briefing import Briefing
from ..config import Settings
from ..media import audio as audio_mod
from ..media import frames as frames_mod
from ..media.ffmpeg import has_ffmpeg, probe, run
from ..project import Project
from . import transcribe, visual_detect
from .text_detect import find_mentions

EXIT_CLEAN = 0
EXIT_LEFTOVERS = 2


@dataclass
class Finding:
    kind: str  # spoken | visual
    start: float
    end: float
    detail: str
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "detail": self.detail,
            "score": round(self.score, 3),
        }


@dataclass
class QaReport:
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_audio: bool = False
    checked_visual: bool = False

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def exit_code(self) -> int:
        return EXIT_CLEAN if self.clean else EXIT_LEFTOVERS

    def to_dict(self) -> dict:
        return {
            "clean": self.clean,
            "exit_code": self.exit_code,
            "findings": [f.to_dict() for f in self.findings],
            "warnings": self.warnings,
            "checked_audio": self.checked_audio,
            "checked_visual": self.checked_visual,
        }

    def summary(self) -> str:
        if self.clean:
            return "QA limpo: nenhuma sobra do produto antigo."
        lines = [f"QA REPROVADO: {len(self.findings)} sobra(s) do produto antigo."]
        for item in self.findings:
            lines.append(f"  [{item.kind}] {item.start:8.2f}s → {item.end:8.2f}s  {item.detail}")
        return "\n".join(lines)


def qa_exit_code(findings) -> int:
    return EXIT_LEFTOVERS if findings else EXIT_CLEAN


def scan(
    video: str | os.PathLike[str],
    briefing: Briefing,
    project: Project,
    settings: Settings,
    *,
    every: float = 2.0,
    visual_threshold: float = visual_detect.DEFAULT_THRESHOLD,
) -> QaReport:
    report = QaReport()
    if not Path(video).is_file():
        raise FileNotFoundError(f"vídeo final não encontrado: {video}")
    if not has_ffmpeg():
        raise RuntimeError("ffmpeg/ffprobe não estão no PATH")

    project.ensure()
    qa_dir = project.dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    info = probe(video)

    # ── áudio ───────────────────────────────────────────────────────────
    wav = qa_dir / "final.wav"
    run(audio_mod.extract_audio_cmd(video, wav), check=False)
    if wav.is_file():
        transcript = transcribe.transcribe(
            wav,
            model_name=settings.whisper_model,
            language=briefing.language or None,
            hotwords=settings.hotwords,
            terms=briefing.old_terms,
            cache_path=qa_dir / "final_transcript.json",
        )
        report.checked_audio = True
        for mention in find_mentions(transcript.words, briefing.old_terms):
            report.findings.append(
                Finding("spoken", mention.start, mention.end, f'ainda fala "{mention.text}" (≈{mention.target})', mention.score / 100.0)
            )

    # ── visual ──────────────────────────────────────────────────────────
    if briefing.old_assets:
        frame_dir = qa_dir / "frames"
        frame_list = frames_mod.extract_frames(video, frame_dir, every=every)
        if frame_list:
            ranges, _ = visual_detect.detect_visual(
                [f.path for f in frame_list],
                [f.time for f in frame_list],
                briefing.old_assets,
                threshold=visual_threshold,
                every=every,
            )
            report.checked_visual = True
            for item in ranges:
                report.findings.append(Finding("visual", item.start, item.end, "produto antigo ainda aparece (CLIP)", item.score))
    else:
        report.warnings.append("sem old_assets no briefing: QA visual não rodou.")

    if info.duration and report.findings:
        report.findings.sort(key=lambda f: f.start)
    return report
