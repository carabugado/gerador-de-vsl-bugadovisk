"""ffmpeg/ffprobe: localização, execução e sondagem.

Os construtores de comando são funções puras (retornam a lista de argumentos)
pra dar pra testar tudo sem ffmpeg instalado.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class FFmpegMissing(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    path = os.environ.get("TDP_FFMPEG") or shutil.which("ffmpeg")
    if not path:
        raise FFmpegMissing("ffmpeg não encontrado no PATH (macOS: brew install ffmpeg)")
    return path


def ffprobe_bin() -> str:
    path = os.environ.get("TDP_FFPROBE") or shutil.which("ffprobe")
    if not path:
        raise FFmpegMissing("ffprobe não encontrado no PATH (macOS: brew install ffmpeg)")
    return path


def has_ffmpeg() -> bool:
    try:
        ffmpeg_bin()
        ffprobe_bin()
    except FFmpegMissing:
        return False
    return True


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check)


@dataclass
class MediaInfo:
    duration: float = 0.0
    fps: float = 30.0
    width: int = 0
    height: int = 0
    has_audio: bool = False

    @property
    def frames(self) -> int:
        return int(round(self.duration * self.fps))


def parse_fps(raw: str | None) -> float:
    """'30000/1001' → 29.97. Valor inválido cai em 30."""
    if not raw:
        return 30.0
    try:
        value = float(Fraction(raw))
    except (ZeroDivisionError, ValueError):
        return 30.0
    return value if value > 0 else 30.0


def parse_probe(payload: dict) -> MediaInfo:
    """Converte o JSON do ffprobe em MediaInfo."""
    streams = payload.get("streams") or []
    fmt = payload.get("format") or {}
    info = MediaInfo()
    try:
        info.duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        info.duration = 0.0
    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and not info.width:
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.fps = parse_fps(stream.get("r_frame_rate"))
            if not info.duration:
                try:
                    info.duration = float(stream.get("duration") or 0.0)
                except (TypeError, ValueError):
                    pass
        elif codec_type == "audio":
            info.has_audio = True
    return info


def probe_cmd(path: str | os.PathLike[str]) -> list[str]:
    return [
        ffprobe_bin(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]


def probe(path: str | os.PathLike[str]) -> MediaInfo:
    result = run(probe_cmd(path))
    return parse_probe(json.loads(result.stdout or "{}"))


def seconds_to_frames(seconds: float, fps: float) -> int:
    return int(round(max(0.0, seconds) * fps))


def frames_to_timecode(frame: int, fps: float) -> str:
    fps_int = max(1, int(round(fps)))
    total = max(0, int(frame))
    ff = total % fps_int
    total //= fps_int
    ss = total % 60
    total //= 60
    mm = total % 60
    hh = total // 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def ensure_parent(path: str | os.PathLike[str]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
