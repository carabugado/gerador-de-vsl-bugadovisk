"""Amostragem de frames e contact sheet.

O contact sheet existe por um motivo: NUNCA aceitar a saída visual sem olhar.
A grade de miniaturas com timecode é o jeito rápido de conferir se o CLIP
acertou (e de pegar over-detection no olho).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import ensure_parent, ffmpeg_bin, frames_to_timecode, run


@dataclass
class Frame:
    index: int
    time: float
    path: str = ""

    def timecode(self, fps: float = 30.0) -> str:
        return frames_to_timecode(int(round(self.time * fps)), fps)


def sample_times(duration: float, *, every: float = 1.0, start: float = 0.0) -> list[float]:
    """Tempos de amostragem, um a cada `every` segundos."""
    if duration <= 0 or every <= 0:
        return []
    times: list[float] = []
    t = float(start)
    while t < duration - 1e-9:
        times.append(round(t, 3))
        t += every
    return times


def extract_frames_cmd(
    video: str | os.PathLike[str],
    out_dir: str | os.PathLike[str],
    *,
    every: float = 1.0,
    width: int = 512,
    pattern: str = "frame_%06d.jpg",
) -> list[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    return [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(video),
        "-vf",
        f"fps=1/{every},scale={width}:-2",
        "-q:v",
        "3",
        str(Path(out_dir) / pattern),
    ]


def extract_frame_at_cmd(
    video: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    time: float,
    *,
    width: int = 512,
) -> list[str]:
    ensure_parent(out_path)
    return [
        ffmpeg_bin(),
        "-y",
        "-ss",
        f"{max(0.0, float(time)):.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-2",
        str(out_path),
    ]


def extract_frames(video, out_dir, *, every: float = 1.0, width: int = 512) -> list[Frame]:
    """Extrai frames e devolve a lista com tempo de cada um."""
    out = Path(out_dir)
    run(extract_frames_cmd(video, out, every=every, width=width), check=False)
    files = sorted(p for p in listdir_paths(out) if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    return [Frame(index=i, time=round(i * every, 3), path=str(p)) for i, p in enumerate(files)]


def listdir_paths(directory: str | os.PathLike[str]) -> list[Path]:
    """os.listdir, NUNCA glob.

    Caminho com colchete (ex.: '[GRUPO FENIX]') quebra glob.glob — colchete é
    classe de caractere no padrão, e a pasta simplesmente "some".
    """
    base = Path(directory)
    if not base.is_dir():
        return []
    return sorted((base / name) for name in os.listdir(base) if not name.startswith("."))


def contact_sheet(
    frames: list[Frame],
    out_path: str | os.PathLike[str],
    *,
    columns: int = 6,
    thumb_width: int = 320,
    fps: float = 30.0,
) -> str:
    """Monta a grade de miniaturas com o timecode escrito em cada uma.

    Precisa de Pillow (extra `clip`).
    """
    from PIL import Image, ImageDraw  # import tardio: extra opcional

    items = [f for f in frames if f.path and Path(f.path).is_file()]
    if not items:
        raise ValueError("sem frames pra montar o contact sheet")

    thumbs = []
    for frame in items:
        img = Image.open(frame.path).convert("RGB")
        ratio = thumb_width / img.width
        img = img.resize((thumb_width, max(1, int(img.height * ratio))))
        draw = ImageDraw.Draw(img)
        label = f"{frame.timecode(fps)}  ({frame.time:.1f}s)"
        draw.rectangle([0, img.height - 18, img.width, img.height], fill=(0, 0, 0))
        draw.text((4, img.height - 15), label, fill=(255, 255, 255))
        thumbs.append(img)

    cols = max(1, columns)
    rows = (len(thumbs) + cols - 1) // cols
    cell_h = max(t.height for t in thumbs)
    sheet = Image.new("RGB", (cols * thumb_width, rows * cell_h), (18, 18, 18))
    for i, thumb in enumerate(thumbs):
        x = (i % cols) * thumb_width
        y = (i // cols) * cell_h
        sheet.paste(thumb, (x, y))
    ensure_parent(out_path)
    sheet.save(out_path)
    return str(out_path)
