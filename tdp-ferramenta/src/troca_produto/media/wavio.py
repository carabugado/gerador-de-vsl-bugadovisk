"""Leitura de WAV PCM com stdlib + numpy (sem soundfile/librosa)."""

from __future__ import annotations

import os
import wave

import numpy as np


def read_wav(path: str | os.PathLike[str]) -> tuple[np.ndarray, int]:
    """(amostras float32 em -1..1, sample_rate). Estéreo vira mono."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"largura de amostra não suportada: {width} bytes")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def slice_samples(samples: np.ndarray, sample_rate: int, start: float, end: float) -> np.ndarray:
    a = max(0, int(float(start) * sample_rate))
    b = min(len(samples), int(float(end) * sample_rate))
    if b <= a:
        return np.zeros((0,), dtype=np.float32)
    return samples[a:b]


def write_wav(path: str | os.PathLike[str], samples: np.ndarray, sample_rate: int) -> str:
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return str(path)


def duration_of(samples: np.ndarray, sample_rate: int) -> float:
    if sample_rate <= 0:
        return 0.0
    return len(samples) / float(sample_rate)
