"""Texto na tela (packshot, bullets, selo de garantia).

Mesma regra da fala: o OCR erra o nome da marca, então o match é fuzzy —
nunca `text in linha`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ranges import Range, merge_ranges
from .text_detect import _fuzzy_score, normalize

DEFAULT_THRESHOLD = 78.0


@dataclass
class OcrHit:
    time: float
    text: str
    target: str
    score: float


def match_ocr_text(text: str, targets: list[str], *, threshold: float = DEFAULT_THRESHOLD) -> tuple[float, str]:
    """Melhor (score, alvo) numa linha de OCR, testando janelas de 1–3 palavras."""
    words = normalize(text).split()
    best_score, best_target = 0.0, ""
    for i in range(len(words)):
        for n in (1, 2, 3):
            window = " ".join(words[i : i + n])
            if not window:
                continue
            for target in targets or []:
                score = _fuzzy_score(window, target)
                if score > best_score:
                    best_score, best_target = score, target
    if best_score < threshold:
        return 0.0, ""
    return best_score, best_target


def ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401,PLC0415
    except Exception:
        return False
    return True


def read_frame_text(path: str) -> str:
    """OCR de um frame. Requer pytesseract + tesseract instalado."""
    import pytesseract  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    return pytesseract.image_to_string(Image.open(path))


def detect_onscreen(
    frame_paths: list[str],
    frame_times: list[float],
    targets: list[str],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    every: float = 1.0,
    reader=None,
) -> list[Range]:
    """Faixas onde o nome antigo aparece ESCRITO na tela."""
    if not frame_paths or not targets:
        return []
    read = reader or read_frame_text
    hits: list[Range] = []
    for path, time in zip(frame_paths, frame_times):
        try:
            text = read(path)
        except Exception:
            continue
        score, target = match_ocr_text(text or "", targets, threshold=threshold)
        if not target:
            continue
        hits.append(
            Range(
                start=float(time),
                end=float(time) + every,
                kind="onscreen",
                score=score / 100.0,
                label=f"texto na tela: {target}",
            )
        )
    return merge_ranges(hits, gap=1.5)
