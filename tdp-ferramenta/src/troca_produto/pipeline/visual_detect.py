"""Detecção visual do produto antigo.

POLÍTICA (aprendida na dor):
  • `TDP_VISUAL_ENGINE=cloud` (padrão) → CLIP + (opcional) visão do Claude.
    NÃO usa OpenCV local, que marca ~73% do vídeo de lixo.
  • Sem crédito na Anthropic, a visual sai só do CLIP — e você CONFERE os
    frames você mesmo, montando o contact sheet e olhando. Nunca aceite a
    saída visual sem conferir.
  • `local` (OpenCV) existe só pra quem sabe o que está fazendo; a ferramenta
    avisa em alto e bom som.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from ..config import resolve_visual_engine
from .ranges import Range, merge_ranges

DEFAULT_CLIP_MODEL = "openai/clip-vit-large-patch14"
DEFAULT_THRESHOLD = 0.78
MIN_HIT_DURATION = 0.6


@dataclass
class EnginePlan:
    engine: str
    use_clip: bool
    use_cloud_vision: bool
    use_opencv: bool
    warnings: list[str] = field(default_factory=list)


def engine_plan(engine: str | None = None, *, has_anthropic: bool = False) -> EnginePlan:
    """Traduz TDP_VISUAL_ENGINE no que realmente vai rodar."""
    resolved = resolve_visual_engine({"TDP_VISUAL_ENGINE": engine} if engine else None)
    warnings: list[str] = []
    if resolved == "local":
        warnings.append(
            "engine=local usa OpenCV: em VSL real isso marcou ~73% do vídeo de lixo. "
            "Use TDP_VISUAL_ENGINE=cloud."
        )
        return EnginePlan(resolved, use_clip=True, use_cloud_vision=False, use_opencv=True, warnings=warnings)
    use_cloud = resolved == "cloud" and has_anthropic
    if resolved == "cloud" and not has_anthropic:
        warnings.append(
            "sem ANTHROPIC_API_KEY (ou sem crédito): a visual vem só do CLIP. "
            "Monte o contact sheet e confira os frames no olho antes de exportar."
        )
    return EnginePlan(resolved, use_clip=True, use_cloud_vision=use_cloud, use_opencv=False, warnings=warnings)


# ── matemática do CLIP (pura, testável sem torch) ───────────────────────────
def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    arr = np.asarray(matrix, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr[None, :]
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def cosine_matrix(frames: np.ndarray, refs: np.ndarray) -> np.ndarray:
    """(n_frames, n_refs) de similaridade cosseno."""
    return l2_normalize(frames) @ l2_normalize(refs).T


def best_scores(frames: np.ndarray, refs: np.ndarray) -> np.ndarray:
    """Melhor referência por frame — o produto aparece de vários ângulos."""
    sims = cosine_matrix(frames, refs)
    if sims.size == 0:
        return np.zeros((sims.shape[0],), dtype=np.float32)
    return sims.max(axis=1)


def hits_to_ranges(
    times: list[float],
    scores,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    every: float = 1.0,
    gap: float = 1.5,
    min_duration: float = MIN_HIT_DURATION,
    kind: str = "visual",
    label: str = "produto antigo (CLIP)",
) -> list[Range]:
    """Frames acima do limiar viram faixas contínuas."""
    values = list(np.asarray(scores, dtype=float).ravel())
    raw: list[Range] = []
    for time, score in zip(times, values):
        if score < threshold:
            continue
        raw.append(Range(start=float(time), end=float(time) + every, kind=kind, score=float(score), label=label))
    merged = merge_ranges(raw, gap=gap)
    return [r for r in merged if r.duration >= min_duration]


def suggest_threshold(scores, *, quantile: float = 0.97, floor: float = DEFAULT_THRESHOLD) -> float:
    """Limiar sugerido: alto por padrão. Na dúvida, detectar de menos."""
    values = np.asarray(scores, dtype=float).ravel()
    if values.size == 0:
        return floor
    return float(max(floor, np.quantile(values, quantile)))


# ── CLIP de verdade (lazy) ──────────────────────────────────────────────────
class ClipEmbedder:
    """Wrapper fino do CLIP. Importa torch/transformers só quando usado."""

    def __init__(self, model_name: str = DEFAULT_CLIP_MODEL, device: str | None = None):
        self.model_name = model_name
        self.device = device
        self._model = None
        self._processor = None

    def _ensure(self):
        if self._model is not None:
            return
        import torch  # noqa: PLC0415
        from transformers import CLIPModel, CLIPProcessor  # noqa: PLC0415

        if self.device is None:
            if torch.backends.mps.is_available():
                self.device = "mps"  # Apple Silicon
            elif torch.cuda.is_available():
                self.device = "cuda"
            else:
                self.device = "cpu"
        self._model = CLIPModel.from_pretrained(self.model_name).to(self.device).eval()
        self._processor = CLIPProcessor.from_pretrained(self.model_name)

    def embed_images(self, paths: list[str]) -> np.ndarray:
        self._ensure()
        import torch  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        usable = [p for p in paths if p and os.path.isfile(p)]
        if not usable:
            return np.zeros((0, 768), dtype=np.float32)
        images = [Image.open(p).convert("RGB") for p in usable]
        inputs = self._processor(images=images, return_tensors="pt").to(self.device)
        with torch.no_grad():
            feats = self._model.get_image_features(**inputs)
        return l2_normalize(feats.cpu().numpy())


def detect_visual(
    frame_paths: list[str],
    frame_times: list[float],
    old_assets: list[str],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    every: float = 1.0,
    embedder: ClipEmbedder | None = None,
) -> tuple[list[Range], np.ndarray]:
    """Roda o CLIP nos frames e devolve (faixas, scores por frame)."""
    if not frame_paths or not old_assets:
        return [], np.zeros((0,), dtype=np.float32)
    clip = embedder or ClipEmbedder()
    refs = clip.embed_images(list(old_assets))
    frames = clip.embed_images(list(frame_paths))
    if refs.shape[0] == 0 or frames.shape[0] == 0:
        return [], np.zeros((0,), dtype=np.float32)
    scores = best_scores(frames, refs)
    return hits_to_ranges(frame_times, scores, threshold=threshold, every=every), scores
