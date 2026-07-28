"""Diarização POR REFERÊNCIA DO NARRADOR.

Não é clustering genérico: a gente monta o centroide do narrador com vários
trechos da seção de oferta (onde ele fala sozinho, limpo) e mede a
similaridade de cada menção contra esse centroide.

    sim >= 0.85 → narrador
    sim <  0.85 → locutor distinto (mesmo quando é outro homem)

O pitch (F0) valida o gênero: homem < 165 Hz, mulher >= 165 Hz.

E o mais importante: diarize SEMPRE no áudio limpo (a VSL base). Rodar no
vídeo que já tem TTS embutido contamina o centroide e o resultado vira lixo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

NARRATOR_SIM_THRESHOLD = 0.85
GENDER_F0_SPLIT = 165.0
MALE_F0_RANGE = (70.0, 200.0)
FEMALE_F0_RANGE = (140.0, 300.0)


def cosine(a, b) -> float:
    va = np.asarray(a, dtype=np.float64).ravel()
    vb = np.asarray(b, dtype=np.float64).ravel()
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def centroid(embeddings) -> np.ndarray:
    """Centroide L2-normalizado de vários trechos do mesmo locutor."""
    arr = np.asarray(embeddings, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[None, :]
    if arr.size == 0:
        raise ValueError("centroide precisa de pelo menos um embedding")
    mean = arr.mean(axis=0)
    norm = np.linalg.norm(mean)
    return mean / norm if norm else mean


@dataclass
class NarratorReference:
    """Centroide do narrador + o limiar de decisão."""

    vector: np.ndarray
    threshold: float = NARRATOR_SIM_THRESHOLD
    sources: list[str] = field(default_factory=list)

    @classmethod
    def from_embeddings(cls, embeddings, *, threshold: float = NARRATOR_SIM_THRESHOLD, sources=None) -> "NarratorReference":
        return cls(vector=centroid(embeddings), threshold=threshold, sources=list(sources or []))

    def similarity(self, embedding) -> float:
        return cosine(self.vector, embedding)

    def is_narrator(self, embedding) -> bool:
        return self.similarity(embedding) >= self.threshold

    def classify(self, embedding) -> tuple[str, float]:
        sim = self.similarity(embedding)
        return ("narrator" if sim >= self.threshold else "speaker"), round(sim, 4)


def gender_from_f0(f0: float) -> str:
    """< 165 Hz → male; >= 165 Hz → female. 0/NaN → unknown."""
    try:
        value = float(f0)
    except (TypeError, ValueError):
        return "unknown"
    if not np.isfinite(value) or value <= 0:
        return "unknown"
    return "male" if value < GENDER_F0_SPLIT else "female"


def estimate_f0(samples, sample_rate: int, *, fmin: float = 60.0, fmax: float = 350.0) -> float:
    """F0 média por autocorrelação (numpy puro, sem librosa)."""
    x = np.asarray(samples, dtype=np.float64).ravel()
    if x.size < sample_rate // 20 or sample_rate <= 0:
        return 0.0
    x = x - x.mean()
    if not np.any(x):
        return 0.0
    frame = int(sample_rate * 0.04)  # 40 ms
    hop = max(1, frame // 2)
    min_lag = max(2, int(sample_rate / fmax))
    max_lag = min(int(sample_rate / fmin), frame - 1)
    if max_lag <= min_lag:
        return 0.0
    pitches: list[float] = []
    for start in range(0, max(1, x.size - frame), hop):
        chunk = x[start : start + frame]
        if chunk.size < frame or not np.any(chunk):
            continue
        energy = float(np.sqrt(np.mean(chunk**2)))
        if energy < 1e-4:  # silêncio: não vota
            continue
        corr = np.correlate(chunk, chunk, mode="full")[frame - 1 :]
        if corr[0] <= 0:
            continue
        window = corr[min_lag : max_lag + 1]
        if window.size == 0:
            continue
        lag = int(np.argmax(window)) + min_lag
        if corr[lag] / corr[0] < 0.3:  # sem periodicidade clara
            continue
        pitches.append(sample_rate / lag)
    if not pitches:
        return 0.0
    return float(np.median(pitches))


@dataclass
class SpeakerTag:
    start: float
    end: float
    role: str = "narrator"  # narrator | speaker
    gender: str = "unknown"  # male | female | unknown
    similarity: float = 0.0
    f0: float = 0.0
    speaker_id: str = "narrator"

    @property
    def is_narrator(self) -> bool:
        return self.role == "narrator"

    def to_dict(self) -> dict:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "role": self.role,
            "gender": self.gender,
            "similarity": round(self.similarity, 4),
            "f0": round(self.f0, 1),
            "speaker_id": self.speaker_id,
        }


def tag_speakers(
    spans: list[tuple[float, float]],
    embeddings,
    reference: NarratorReference,
    *,
    f0s: list[float] | None = None,
    group_threshold: float = 0.80,
) -> list[SpeakerTag]:
    """Rotula cada trecho: narrador ou locutor distinto (e agrupa os distintos).

    Os não-narradores são agrupados entre si por similaridade, pra dar uma
    faixa de áudio por locutor no Premiere.
    """
    tags: list[SpeakerTag] = []
    others: list[tuple[str, np.ndarray]] = []
    for i, (span, emb) in enumerate(zip(spans, embeddings)):
        role, sim = reference.classify(emb)
        f0 = float(f0s[i]) if f0s and i < len(f0s) else 0.0
        gender = gender_from_f0(f0) if f0 else ("male" if role == "narrator" else "unknown")
        speaker_id = "narrator"
        if role != "narrator":
            speaker_id = ""
            for existing_id, vector in others:
                if cosine(vector, emb) >= group_threshold:
                    speaker_id = existing_id
                    break
            if not speaker_id:
                speaker_id = f"speaker_{len(others) + 1:02d}"
                others.append((speaker_id, np.asarray(emb, dtype=np.float64).ravel()))
        tags.append(
            SpeakerTag(
                start=float(span[0]),
                end=float(span[1]),
                role=role,
                gender=gender,
                similarity=sim,
                f0=f0,
                speaker_id=speaker_id,
            )
        )
    return tags


def check_source(audio_path: str, base_audio_path: str) -> list[str]:
    """Avisa se você está prestes a diarizar no áudio errado."""
    problems: list[str] = []
    lowered = str(audio_path).lower()
    if any(mark in lowered for mark in ("final", "render", "tts", "rebrand", "export", "v2")):
        problems.append(
            f"{audio_path}: parece áudio já editado/com TTS. Diarize no áudio limpo da VSL base "
            f"({base_audio_path}) — TTS embutido contamina o centroide do narrador."
        )
    if base_audio_path and str(audio_path) != str(base_audio_path):
        problems.append(f"diarização rodando fora da base ({base_audio_path}); confirme que é o áudio limpo.")
    return problems


# ── resemblyzer (lazy) ──────────────────────────────────────────────────────
def embed_wav(path: str):
    """Embedding de voz de um wav. Requer o extra `voice` (resemblyzer)."""
    from resemblyzer import preprocess_wav  # noqa: PLC0415

    encoder = _encoder()
    wav = preprocess_wav(path)
    return encoder.embed_utterance(wav)


_ENCODER = None


def _encoder():
    global _ENCODER
    if _ENCODER is None:
        from resemblyzer import VoiceEncoder  # noqa: PLC0415

        _ENCODER = VoiceEncoder()
    return _ENCODER
