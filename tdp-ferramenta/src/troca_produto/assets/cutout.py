"""Recorte do produto (PNG transparente) e b-roll com alpha.

APRENDIZADOS:
  • PNG de produto quase sempre vem RGB SEM alpha — o "png falso". No overlay
    aparece a caixa branca de fundo. Confira com `has_alpha()` antes de usar.
  • Recorte com rembg usando `new_session("birefnet-general")`. É MUITO melhor
    que o isnet padrão em frasco/rótulo.
  • B-roll com transparência precisa ser ProRes 4444 (.mov). MP4 não guarda
    alpha — não adianta insistir.
  • Ao regenerar, SOBRESCREVA os mesmos nomes de arquivo, senão o link no
    Premiere quebra e o editor tem que relinkar tudo na mão.
  • Caminho com colchete (ex.: '[GRUPO FENIX]') quebra glob.glob. Use os.listdir.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..media.ffmpeg import ensure_parent, ffmpeg_bin

REMBG_MODEL = "birefnet-general"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
ALPHA_MODES = {"RGBA", "LA", "PA"}


def list_assets(directory: str | os.PathLike[str], *, exts: set[str] | None = None) -> list[str]:
    """Lista arquivos de uma pasta com os.listdir — NUNCA glob.

    `glob.glob('/algo/[GRUPO FENIX]/*.png')` devolve vazio: o colchete é
    interpretado como classe de caractere. Já custou uma tarde.
    """
    base = Path(directory)
    if not base.is_dir():
        return []
    wanted = exts or IMAGE_EXTS
    out: list[str] = []
    for name in sorted(os.listdir(base)):
        if name.startswith("."):
            continue
        path = base / name
        if path.is_file() and path.suffix.lower() in wanted:
            out.append(str(path))
    return out


def is_fake_png(mode: str) -> bool:
    """'png falso': extensão .png mas modo sem canal alpha."""
    return (mode or "").upper() not in ALPHA_MODES


def has_alpha(path: str | os.PathLike[str]) -> bool:
    """True se a imagem realmente tem canal alpha (precisa de Pillow)."""
    from PIL import Image  # noqa: PLC0415

    with Image.open(path) as img:
        mode = img.mode
        if mode.upper() in ALPHA_MODES:
            return True
        return "transparency" in img.info


@dataclass
class CutoutResult:
    source: str
    output: str
    model: str = REMBG_MODEL
    overwritten: bool = False


def _session():
    from rembg import new_session  # noqa: PLC0415

    return new_session(REMBG_MODEL)


def cutout_image(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str] | None = None,
    *,
    session=None,
) -> CutoutResult:
    """Recorta o fundo e salva PNG com alpha de verdade.

    Sem `output`, sobrescreve o próprio arquivo (mantém o link do Premiere).
    """
    from rembg import remove  # noqa: PLC0415

    src = Path(source)
    dst = Path(output) if output else src.with_suffix(".png")
    ensure_parent(dst)
    data = src.read_bytes()
    result = remove(data, session=session or _session())
    overwritten = dst.exists()
    dst.write_bytes(result)
    return CutoutResult(source=str(src), output=str(dst), overwritten=overwritten)


def cutout_folder(
    directory: str | os.PathLike[str],
    out_dir: str | os.PathLike[str] | None = None,
) -> list[CutoutResult]:
    """Recorta a pasta inteira MANTENDO os nomes (não quebra link no Premiere)."""
    session = _session()
    results: list[CutoutResult] = []
    for path in list_assets(directory):
        name = Path(path).stem + ".png"
        target = Path(out_dir) / name if out_dir else Path(path).with_name(name)
        results.append(cutout_image(path, target, session=session))
    return results


def prores4444_cmd(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    fps: float | None = None,
) -> list[str]:
    """B-roll com transparência: ProRes 4444 em .mov. MP4 não guarda alpha."""
    out = Path(output)
    if out.suffix.lower() != ".mov":
        raise ValueError("b-roll com alpha precisa ser .mov (ProRes 4444); mp4 não guarda alpha")
    cmd = [ffmpeg_bin(), "-y"]
    if fps:
        cmd += ["-framerate", f"{fps:g}"]
    cmd += [
        "-i",
        str(source),
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4444",
        "-pix_fmt",
        "yuva444p10le",
        "-alpha_bits",
        "16",
        str(out),
    ]
    return cmd


def audit_assets(paths: list[str]) -> list[str]:
    """Aponta os PNGs falsos antes que virem caixa branca no overlay."""
    problems: list[str] = []
    for path in paths:
        if Path(path).suffix.lower() != ".png":
            continue
        try:
            if not has_alpha(path):
                problems.append(f"{path}: PNG sem alpha (png falso) — recorte com rembg/{REMBG_MODEL}")
        except Exception as exc:  # imagem quebrada
            problems.append(f"{path}: não deu pra ler ({exc})")
    return problems
