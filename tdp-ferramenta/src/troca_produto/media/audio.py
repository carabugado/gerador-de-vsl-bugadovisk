"""Áudio: extração, aparo de silêncio e encaixe no tempo do trecho.

APRENDIZADO CARO: aparar silêncio SÓ das pontas. O `silenceremove` com
`stop_periods` corta na primeira pausa interna e TRUNCA a frase — em
depoimento isso come metade da fala. Aqui a gente mede o silêncio das bordas
com `silencedetect` e corta com -ss/-to, sem tocar no miolo.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .ffmpeg import ensure_parent, ffmpeg_bin, run

SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")


def extract_audio_cmd(video: str | os.PathLike[str], out_wav: str | os.PathLike[str], *, sample_rate: int = 16000) -> list[str]:
    """WAV mono 16k — formato que Whisper e resemblyzer querem."""
    return [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]


def slice_cmd(
    source: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    start: float,
    end: float,
    *,
    sample_rate: int = 16000,
) -> list[str]:
    duration = max(0.01, float(end) - float(start))
    return [
        ffmpeg_bin(),
        "-y",
        "-ss",
        f"{max(0.0, float(start)):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(out_path),
    ]


def silencedetect_cmd(path: str | os.PathLike[str], *, noise_db: float = -35.0, min_dur: float = 0.12) -> list[str]:
    return [
        ffmpeg_bin(),
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_dur}",
        "-f",
        "null",
        "-",
    ]


def parse_silences(stderr: str) -> list[tuple[float, float]]:
    """Extrai os pares (start, end) do log do silencedetect."""
    events = [(m.group(1), float(m.group(2))) for m in SILENCE_RE.finditer(stderr or "")]
    spans: list[tuple[float, float]] = []
    open_start: float | None = None
    for kind, value in events:
        if kind == "start":
            open_start = value
        elif kind == "end" and open_start is not None:
            spans.append((open_start, value))
            open_start = None
    if open_start is not None:
        spans.append((open_start, float("inf")))
    return spans


def edge_trim_points(silences: list[tuple[float, float]], duration: float, *, tolerance: float = 0.05) -> tuple[float, float]:
    """Quanto cortar do começo e do fim — e SÓ das pontas.

    Um silêncio no meio da fala é ignorado de propósito.
    """
    start = 0.0
    end = float(duration)
    for s_start, s_end in silences:
        if s_start <= tolerance:
            start = max(start, min(s_end, end))
        if s_end >= duration - tolerance or s_end == float("inf"):
            end = min(end, max(s_start, start))
    if end <= start:
        return 0.0, float(duration)
    return start, end


def trim_edges_cmd(
    source: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    start: float,
    end: float,
) -> list[str]:
    """Corte por -ss/-to. Nunca usa stop_periods (trunca frase)."""
    return [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(source),
        "-ss",
        f"{max(0.0, float(start)):.3f}",
        "-to",
        f"{max(float(start) + 0.01, float(end)):.3f}",
        "-c:a",
        "pcm_s16le",
        str(out_path),
    ]


def atempo_chain(ratio: float) -> list[str]:
    """`atempo` aceita 0.5–2.0 por filtro; ratios fora disso viram cadeia."""
    value = float(ratio)
    if value <= 0:
        raise ValueError("ratio de atempo precisa ser > 0")
    if abs(value - 1.0) < 1e-3:
        return []
    steps: list[float] = []
    remaining = value
    while remaining > 2.0:
        steps.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        steps.append(0.5)
        remaining /= 0.5
    steps.append(remaining)
    return [f"atempo={step:.6g}" for step in steps]


def fit_duration_cmd(
    source: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    source_duration: float,
    target_duration: float,
    *,
    max_stretch: float = 1.35,
) -> list[str]:
    """Encaixa o TTS no tempo do trecho original via atempo.

    `max_stretch` segura o quanto a gente aceita distorcer: acima disso é
    melhor regenerar a frase inteira do que esticar (fica robótico).
    """
    src = max(0.01, float(source_duration))
    dst = max(0.01, float(target_duration))
    ratio = src / dst
    ratio = min(max(ratio, 1.0 / max_stretch), max_stretch)
    filters = atempo_chain(ratio)
    cmd = [ffmpeg_bin(), "-y", "-i", str(source)]
    if filters:
        cmd += ["-filter:a", ",".join(filters)]
    cmd += [str(out_path)]
    return cmd


def concat_cmd(inputs: list[str], out_path: str | os.PathLike[str], list_file: str | os.PathLike[str]) -> list[str]:
    """Concat demuxer — usado pra montar a referência limpa de uma voz."""
    ensure_parent(list_file)
    with open(list_file, "w", encoding="utf-8") as handle:
        for item in inputs:
            escaped = str(item).replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")
    return [
        ffmpeg_bin(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(out_path),
    ]


def to_mono_mp3_cmd(source: str | os.PathLike[str], out_path: str | os.PathLike[str], *, bitrate: str = "192k") -> list[str]:
    """Clipes de voz entram no Premiere como mp3 MONO (uma faixa por locutor)."""
    return [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-b:a",
        bitrate,
        str(out_path),
    ]


@dataclass
class TrimResult:
    start: float
    end: float
    duration: float


def trim_edges(source: str | os.PathLike[str], out_path: str | os.PathLike[str], duration: float) -> TrimResult:
    """Mede o silêncio das pontas e corta. Requer ffmpeg de verdade."""
    detect = run(silencedetect_cmd(source), check=False)
    silences = parse_silences(detect.stderr)
    start, end = edge_trim_points(silences, duration)
    ensure_parent(out_path)
    run(trim_edges_cmd(source, out_path, start, end), check=False)
    return TrimResult(start=start, end=end, duration=max(0.0, end - start))
