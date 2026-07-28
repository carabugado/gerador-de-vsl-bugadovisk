"""Orquestra a análise: fala + texto na tela + visual → faixas revisáveis."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..briefing import Briefing
from ..config import Settings
from ..media import audio as audio_mod
from ..media import frames as frames_mod
from ..media.ffmpeg import has_ffmpeg, probe, run
from ..project import Project, State
from . import ocr_detect, transcribe, visual_detect
from .ranges import Range, check_coverage, merge_ranges
from .text_detect import find_mentions


def combine_ranges(*groups) -> list[Range]:
    """Junta os três detectores em faixas SEM sobreposição.

    Isso não é estética: duas faixas sobrepostas na mesma trilha V1 viram
    clipe em cima de clipe no Premiere. Onde tipos diferentes se cruzam, a
    faixa vira `mixed`.
    """
    everything: list[Range] = []
    for group in groups:
        everything.extend(group or [])
    if not everything:
        return []
    everything.sort(key=lambda r: (r.start, r.end))

    out: list[Range] = []
    current = None
    kinds: set[str] = set()
    labels: list[str] = []
    for item in everything:
        if current is None:
            current = Range(item.start, item.end, item.kind, item.score, item.label)
            kinds = {item.kind}
            labels = [item.label] if item.label else []
            continue
        if item.start <= current.end:  # encosta ou sobrepõe
            current.end = max(current.end, item.end)
            current.score = max(current.score, item.score)
            kinds.add(item.kind)
            if item.label and item.label not in labels:
                labels.append(item.label)
            continue
        current.kind = next(iter(kinds)) if len(kinds) == 1 else "mixed"
        current.label = " + ".join(labels)
        out.append(current)
        current = Range(item.start, item.end, item.kind, item.score, item.label)
        kinds = {item.kind}
        labels = [item.label] if item.label else []
    if current is not None:
        current.kind = next(iter(kinds)) if len(kinds) == 1 else "mixed"
        current.label = " + ".join(labels)
        out.append(current)
    return out


def mentions_to_ranges(mentions, *, pad: float = 0.15) -> list[Range]:
    """Menções faladas viram faixas (com uma folguinha nas pontas)."""
    ranges = [
        Range(
            start=max(0.0, m.start - pad),
            end=m.end + pad,
            kind="spoken",
            score=m.score / 100.0,
            label=f'falado: "{m.text}" (≈{m.target}, {m.score:.0f})',
            meta={"text": m.text, "target": m.target},
        )
        for m in mentions
    ]
    return merge_ranges(ranges, gap=0.4)


#: Deslocamentos (em segundos) ao redor de cada menção falada. O packshot
#: costuma entrar um pouco depois de o locutor falar o nome.
REF_OFFSETS = (-1.5, -0.5, 0.5, 1.5, 3.0)


def candidate_times(mentions, duration: float, *, offsets=REF_OFFSETS, limit: int = 60) -> list[float]:
    """Momentos onde vale procurar o produto antigo na tela.

    Serve pra quem ainda NÃO tem foto do produto antigo: roda a análise só de
    áudio, extrai estes frames e escolhe 3–5 que mostram o produto — eles
    viram os `old_assets` do CLIP.
    """
    times: set[float] = set()
    for item in mentions or []:
        start = float(item["start"] if isinstance(item, dict) else getattr(item, "start", 0.0))
        for offset in offsets:
            moment = round(start + offset, 2)
            if 0.0 <= moment <= max(0.0, duration):
                times.add(moment)
    return sorted(times)[:limit]


def is_url(value: str) -> bool:
    return str(value).startswith(("http://", "https://"))


def download_video(url: str, out_dir: str | os.PathLike[str]) -> str:
    """Baixa a VSL com yt-dlp (extra `url`)."""
    import yt_dlp  # noqa: PLC0415

    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    options = {
        "outtmpl": str(target / "vsl_original.%(ext)s"),
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


@dataclass
class AnalysisResult:
    state: State
    coverage: object = None
    warnings: list[str] = field(default_factory=list)


def analyze(
    briefing: Briefing,
    project: Project,
    settings: Settings,
    *,
    every: float = 1.0,
    threshold_visual: float = visual_detect.DEFAULT_THRESHOLD,
    force: bool = False,
    with_ocr: bool = False,
) -> AnalysisResult:
    """Roda a análise completa e grava o estado da demanda."""
    project.ensure()
    warnings: list[str] = []

    plan = visual_detect.engine_plan(settings.visual_engine, has_anthropic=settings.has_anthropic)
    warnings.extend(plan.warnings)

    source = briefing.video
    if is_url(source):
        source = download_video(source, project.dir)
    if not source or not Path(source).is_file():
        raise FileNotFoundError(f"vídeo não encontrado: {briefing.video}")

    if not has_ffmpeg():
        raise RuntimeError("ffmpeg/ffprobe não estão no PATH (macOS: brew install ffmpeg)")

    info = probe(source)
    state = State(
        video=str(Path(source).resolve()),
        duration=info.duration,
        fps=info.fps,
        width=info.width or 1920,
        height=info.height or 1080,
        engine=plan.engine,
    )

    # ── 1. fala ─────────────────────────────────────────────────────────
    spoken: list[Range] = []
    if briefing.wants_audio:
        if force or not project.base_audio.is_file():
            run(audio_mod.extract_audio_cmd(source, project.base_audio), check=False)
        transcript = transcribe.transcribe(
            project.base_audio,
            model_name=settings.whisper_model,
            language=briefing.language or None,
            hotwords=settings.hotwords,
            terms=briefing.old_terms,
            cache_path=project.transcript_path,
            force=force,
        )
        words = transcript.words or []
        mentions = find_mentions(words, briefing.old_terms)
        state.mentions = [m.to_dict() for m in mentions]
        spoken = mentions_to_ranges(mentions)
        if not mentions:
            warnings.append(
                "nenhuma menção falada encontrada — confira o nome/aliases no briefing "
                "e rode com TDP_HOTWORDS=1."
            )

    # ── 2. frames ───────────────────────────────────────────────────────
    frame_list: list[frames_mod.Frame] = []
    if briefing.wants_visual:
        existing = [p for p in frames_mod.listdir_paths(project.frames_dir) if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
        if force or not existing:
            frame_list = frames_mod.extract_frames(source, project.frames_dir, every=every)
        else:
            frame_list = [frames_mod.Frame(index=i, time=round(i * every, 3), path=str(p)) for i, p in enumerate(sorted(existing))]

    # ── 3. visual (CLIP) ────────────────────────────────────────────────
    visual: list[Range] = []
    if briefing.wants_visual and frame_list:
        paths = [f.path for f in frame_list]
        times = [f.time for f in frame_list]
        visual, _scores = visual_detect.detect_visual(
            paths, times, briefing.old_assets, threshold=threshold_visual, every=every
        )
        if plan.use_opencv:
            warnings.append("engine=local: confira TUDO no contact sheet, o OpenCV infla a detecção.")

    # ── 4. texto na tela (OCR) ──────────────────────────────────────────
    onscreen: list[Range] = []
    if with_ocr and frame_list:
        if not ocr_detect.ocr_available():
            warnings.append("OCR pedido mas pytesseract não está instalado — etapa pulada.")
        else:
            onscreen = ocr_detect.detect_onscreen(
                [f.path for f in frame_list], [f.time for f in frame_list], briefing.old_terms, every=every
            )

    # ── 5. junta e confere cobertura ────────────────────────────────────
    state.ranges = combine_ranges(spoken, visual, onscreen)
    coverage = check_coverage(visual, info.duration)
    state.coverage = {"ratio": round(coverage.ratio, 4), "level": coverage.level, "message": coverage.message}
    if coverage.level != "ok":
        warnings.append(coverage.message)
    state.warnings = warnings
    project.save_state(state)
    return AnalysisResult(state=state, coverage=coverage, warnings=warnings)
