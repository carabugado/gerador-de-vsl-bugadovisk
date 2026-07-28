"""CLI do TDP.

    python -m troca_produto init    --out briefing.json
    python -m troca_produto doctor
    python -m troca_produto analyze --briefing briefing.json --dir tdp_projeto
    python -m troca_produto refs    --dir tdp_projeto
    python -m troca_produto review  --dir tdp_projeto
    python -m troca_produto rebrand --dir tdp_projeto [--run]
    python -m troca_produto export  --dir tdp_projeto
    python -m troca_produto qa      --video final.mp4 --dir tdp_projeto
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__, review
from .briefing import Briefing, write_template
from .config import load_settings
from .export.premiere_xml import build_timeline, write_xml
from .export.reports import build_report, write_csv, write_report
from .pipeline.ranges import check_coverage
from .project import Project

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_LEFTOVERS = 2


def _print(*args):
    print(*args, flush=True)


# ── init ────────────────────────────────────────────────────────────────────
def cmd_init(args) -> int:
    path = write_template(args.out, force=args.force)
    _print(f"briefing criado: {path}")
    _print("edite: old_product_name, new_product_name, aliases, old_assets, new_assets, swap_kind, swap_target")
    return EXIT_OK


# ── doctor ──────────────────────────────────────────────────────────────────
def _module_ok(name: str) -> bool:
    try:
        __import__(name)
    except Exception:
        return False
    return True


def cmd_doctor(args) -> int:
    settings = load_settings(args.root)
    _print(f"TDP {__version__} · python {sys.version.split()[0]}")
    _print("")
    _print("binários:")
    for binary in ("ffmpeg", "ffprobe"):
        found = shutil.which(binary)
        _print(f"  {'ok ' if found else 'NÃO'} {binary}  {found or '→ brew install ffmpeg'}")
    _print("")
    _print("pacotes:")
    checks = [
        ("whisper", "transcrição", 'pip install -e ".[asr]"'),
        ("torch", "CLIP", 'pip install -e ".[clip]"'),
        ("transformers", "CLIP", 'pip install -e ".[clip]"'),
        ("PIL", "imagens/contact sheet", 'pip install -e ".[clip]"'),
        ("yt_dlp", "baixar VSL por URL", 'pip install -e ".[url]"'),
        ("rembg", "recorte de produto", 'pip install "rembg[cpu]"'),
        ("resemblyzer", "diarização", 'pip install resemblyzer "setuptools<81"'),
        ("sklearn", "diarização", "pip install scikit-learn"),
        ("pkg_resources", "webrtcvad precisa disso", 'pip install "setuptools<81"'),
        ("pytesseract", "texto na tela (opcional)", "pip install pytesseract && brew install tesseract"),
    ]
    for module, what, fix in checks:
        ok = _module_ok(module)
        _print(f"  {'ok ' if ok else 'NÃO'} {module:<14} {what:<26} {'' if ok else fix}")
    _print("")
    _print("chaves:")
    _print(f"  {'ok ' if settings.has_minimax else 'NÃO'} MINIMAX_API_KEY    (clone de voz + TTS)")
    _print(f"  {'ok ' if settings.has_anthropic else '—  '} ANTHROPIC_API_KEY  (opcional: visão do Claude)")
    _print("")
    _print(f"engine visual: {settings.visual_engine}" + ("  ⚠ OpenCV infla a detecção" if settings.uses_opencv else ""))
    _print(f"hotwords: {'ligado' if settings.hotwords else 'desligado (use TDP_HOTWORDS=1)'}")
    if not settings.has_anthropic:
        _print("sem crédito na Anthropic: a visual sai só do CLIP — monte o contact sheet e confira no olho.")
    return EXIT_OK


# ── analyze ─────────────────────────────────────────────────────────────────
def cmd_analyze(args) -> int:
    from .pipeline.analyze import analyze  # import tardio: puxa whisper/torch

    settings = load_settings(args.root)
    briefing = Briefing.load(args.briefing)
    problems = briefing.validate()
    if problems:
        _print("briefing incompleto:")
        for item in problems:
            _print(f"  - {item}")
        return EXIT_ERROR

    project = Project(args.dir).ensure()
    project.save_briefing(briefing)
    result = analyze(
        briefing,
        project,
        settings,
        every=args.every,
        threshold_visual=args.visual_threshold,
        force=args.force,
        with_ocr=args.ocr,
    )
    state = result.state
    _print(f"vídeo: {state.video}")
    _print(f"duração: {state.duration / 60:.1f} min · {state.fps:g} fps · {state.width}x{state.height}")
    _print(f"menções faladas: {len(state.mentions)}")
    _print(f"aparições (após juntar): {len(state.ranges)}")
    _print(result.coverage.message)
    for warning in result.warnings:
        _print(f"⚠ {warning}")
    _print("")
    _print(review.format_table(state))
    _print("")
    _print(f"estado salvo em {project.state_path}")
    _print(f"próximo passo: python -m troca_produto review --dir {args.dir}")
    return EXIT_OK


# ── review ──────────────────────────────────────────────────────────────────
def cmd_review(args) -> int:
    project = Project(args.dir)
    state = project.load_state()
    if not state.ranges and not project.state_path.is_file():
        _print(f"nada analisado em {args.dir} — rode o analyze primeiro.")
        return EXIT_ERROR

    changed = False
    if args.drop:
        review.drop(state, review.parse_indices(args.drop, total=len(state.ranges)))
        changed = True
    if args.keep:
        review.keep(state, review.parse_indices(args.keep, total=len(state.ranges)))
        changed = True
    if args.keep_only:
        review.keep_only(state, review.parse_indices(args.keep_only, total=len(state.ranges)))
        changed = True
    for pair in args.set_asset or []:
        index, _, path = pair.partition("=")
        try:
            review.set_asset(state, int(index) - 1, path)
            changed = True
        except ValueError:
            _print(f"--set-asset inválido: {pair} (use 3=caminho/arte.png)")
    if args.auto_assets:
        review.auto_assign_assets(state, project.load_briefing())
        changed = True

    if changed:
        # recalcula a cobertura só com o que sobrou
        coverage = check_coverage([r for r in state.kept_ranges if r.kind in ("visual", "mixed")], state.duration)
        state.coverage = {"ratio": round(coverage.ratio, 4), "level": coverage.level, "message": coverage.message}
        project.save_state(state)

    if args.sheet:
        _build_contact_sheet(project, state)

    _print(review.format_table(state))
    if changed:
        _print("")
        _print(f"estado atualizado em {project.state_path}")
    return EXIT_OK


# ── refs ────────────────────────────────────────────────────────────────────
def cmd_refs(args) -> int:
    """Frames candidatos a virar `old_assets`, tirados das menções faladas."""
    from .media.ffmpeg import has_ffmpeg, run
    from .media.frames import Frame, contact_sheet, extract_frame_at_cmd
    from .pipeline.analyze import candidate_times

    project = Project(args.dir)
    state = project.load_state()
    if not state.mentions:
        _print("nenhuma menção falada no estado — rode o analyze (pode ser com swap_target: audio).")
        return EXIT_ERROR
    if not has_ffmpeg():
        _print("ffmpeg/ffprobe não estão no PATH (macOS: brew install ffmpeg)")
        return EXIT_ERROR

    out_dir = project.dir / "refs"
    out_dir.mkdir(parents=True, exist_ok=True)
    times = candidate_times(state.mentions, state.duration, limit=args.limit)
    frames = []
    for index, moment in enumerate(times):
        path = out_dir / f"ref_{index:03d}_{moment:07.2f}s.jpg"
        run(extract_frame_at_cmd(state.video, path, moment, width=640), check=False)
        if path.is_file():
            frames.append(Frame(index=index, time=moment, path=str(path)))

    _print(f"{len(frames)} frames em {out_dir}  (nas {len(state.mentions)} menções faladas)")
    try:
        sheet = contact_sheet(frames, out_dir / "contact_sheet.jpg", fps=state.fps)
        _print(f"contact sheet: {sheet}")
    except Exception as exc:
        _print(f"contact sheet não montado: {exc}")
    _print("")
    _print("agora: abra a pasta, apague os frames que NÃO mostram o produto antigo,")
    _print("e aponte os 3–5 melhores em `old_assets` no briefing.")
    return EXIT_OK


def _build_contact_sheet(project: Project, state) -> None:
    from .media.frames import Frame, contact_sheet, listdir_paths

    files = [p for p in listdir_paths(project.frames_dir) if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    if not files:
        _print("sem frames pra montar o contact sheet (rode o analyze).")
        return
    kept = state.kept_ranges or state.ranges
    wanted: list[Frame] = []
    for item in kept:
        index = int(item.start)
        if 0 <= index < len(files):
            wanted.append(Frame(index=index, time=float(index), path=str(files[index])))
    if not wanted:
        wanted = [Frame(index=i, time=float(i), path=str(p)) for i, p in enumerate(files[:60])]
    try:
        path = contact_sheet(wanted, project.contact_sheet_path, fps=state.fps)
    except Exception as exc:
        _print(f"não deu pra montar o contact sheet: {exc}")
        return
    _print(f"contact sheet: {path}  ← ABRA E CONFIRA os frames antes de exportar")


# ── rebrand ─────────────────────────────────────────────────────────────────
def cmd_rebrand(args) -> int:
    from .pipeline.text_detect import Mention
    from .pipeline.transcribe import Transcript
    from .voice.runner import run_rebrand

    settings = load_settings(args.root)
    project = Project(args.dir)
    state = project.load_state()
    briefing = project.load_briefing()
    if not state.mentions:
        _print("nenhuma menção falada no estado — rode o analyze primeiro.")
        return EXIT_ERROR
    if not project.transcript_path.is_file():
        _print(f"transcrição não encontrada em {project.transcript_path} — rode o analyze primeiro.")
        return EXIT_ERROR
    if args.run and not settings.has_minimax:
        _print("MINIMAX_API_KEY não configurada (.env na raiz) — sem ela dá só pra ver o plano.")
        return EXIT_ERROR

    transcript = Transcript.load(project.transcript_path)
    mentions = [
        Mention(
            start=m["start"],
            end=m["end"],
            text=m["text"],
            target=m.get("target", ""),
            score=m.get("score", 0.0),
            words=m.get("words", 1),
        )
        for m in state.mentions
    ]
    result = run_rebrand(
        project, briefing, settings, mentions, transcript.words, state.duration, generate=args.run
    )
    state.rebrand = [i.to_dict() for i in result.items]
    state.speakers = result.speakers
    project.save_state(state)

    narrador = sum(1 for i in result.items if i.role == "narrator")
    _print(f"{len(result.items)} menções · {narrador} do narrador (troca a palavra) · {len(result.items) - narrador} de depoimento (frase inteira)")
    for index, item in enumerate(result.items, start=1):
        voice = item.voice.voice_id if item.voice else "—"
        flag = "" if not (item.voice and item.voice.is_fallback) else f"  [fallback: {item.voice.reason}]"
        _print(f"  {index:>3} {item.start:8.2f}s  {item.speaker_id:<12} {item.role:<9} {item.mode:<8} {voice:<18} {item.text_to_speak[:48]}{flag}")
    for warning in result.warnings:
        _print(f"⚠ {warning}")
    if result.generated:
        _print(f"{len(result.generated)} mp3 gerados em {project.voice_dir}")
    else:
        _print("plano montado (use --run pra gerar o áudio no MiniMax)")
    return EXIT_OK


# ── export ──────────────────────────────────────────────────────────────────
def cmd_export(args) -> int:
    project = Project(args.dir)
    state = project.load_state()
    briefing = project.load_briefing()
    if not state.ranges:
        _print("nada pra exportar — rode o analyze.")
        return EXIT_ERROR

    kept = state.kept_ranges
    assets = review.assets_for_export(state)
    voice_tracks = {}
    if state.rebrand:
        from .voice.runner import voice_tracks_for_xml
        from .voice.rebrand import RebrandItem
        from .voice.rebrand import VoiceChoice

        items = []
        for raw in state.rebrand:
            voice = raw.get("voice") or {}
            items.append(
                RebrandItem(
                    start=raw["start"],
                    end=raw["end"],
                    mode=raw.get("mode", "word"),
                    role=raw.get("role", "narrator"),
                    speaker_id=raw.get("speaker_id", "narrator"),
                    gender=raw.get("gender", "unknown"),
                    original_text=raw.get("original_text", ""),
                    text_to_speak=raw.get("text_to_speak", ""),
                    voice=VoiceChoice(voice.get("voice_id", ""), voice.get("kind", "cloned"), voice.get("reason", "")) if voice else None,
                    audio_path=raw.get("audio_path", ""),
                )
            )
        voice_tracks = voice_tracks_for_xml(items)

    timeline = build_timeline(
        name=args.name,
        video_path=state.video,
        duration=state.duration,
        fps=state.fps,
        width=state.width,
        height=state.height,
        ranges=kept,
        new_assets=assets,
        voice_items=voice_tracks,
        audio_path=str(project.base_audio) if project.base_audio.is_file() else None,
    )
    xml_path = write_xml(timeline, project.export_dir / f"{args.name}.xml")
    csv_path = write_csv(kept, project.csv_path, fps=state.fps, new_assets=assets)
    coverage = check_coverage([r for r in kept if r.kind in ("visual", "mixed")], state.duration)
    report = build_report(
        briefing=briefing,
        ranges=kept,
        duration=state.duration,
        coverage=coverage,
        rebrand_items=None,
        warnings=state.warnings,
        fps=state.fps,
    )
    report_path = write_report(report, project.report_path)

    _print(f"XML:    {xml_path}")
    _print("        V1 cortado+colorido (+marcadores) · V2 produto novo · A1 áudio original · A2+ vozes")
    _print(f"CSV:    {csv_path}")
    _print(f"report: {report_path}")
    if coverage.level != "ok":
        _print(f"⚠ {coverage.message}")
    return EXIT_OK


# ── cutout ──────────────────────────────────────────────────────────────────
def cmd_cutout(args) -> int:
    from .assets.cutout import REMBG_MODEL, audit_assets, cutout_folder, cutout_image, list_assets

    source = Path(args.input)
    if not source.exists():
        _print(f"não existe: {source}")
        return EXIT_ERROR
    if args.audit:
        paths = list_assets(source) if source.is_dir() else [str(source)]
        problems = audit_assets(paths)
        if not problems:
            _print(f"{len(paths)} arquivo(s) — todos com alpha de verdade.")
            return EXIT_OK
        for item in problems:
            _print(f"⚠ {item}")
        return EXIT_OK

    _print(f"recortando com rembg/{REMBG_MODEL} (o primeiro uso baixa o modelo)")
    if source.is_dir():
        results = cutout_folder(source, args.out)
    else:
        results = [cutout_image(source, args.out)]
    for item in results:
        flag = " (sobrescrito — link do Premiere preservado)" if item.overwritten else ""
        _print(f"  {item.output}{flag}")
    return EXIT_OK


# ── qa ──────────────────────────────────────────────────────────────────────
def cmd_qa(args) -> int:
    from .pipeline.qa import scan

    settings = load_settings(args.root)
    project = Project(args.dir)
    briefing = project.load_briefing()
    report = scan(args.video, briefing, project, settings, every=args.every)
    _print(report.summary())
    for warning in report.warnings:
        _print(f"⚠ {warning}")
    out = project.dir / "qa" / "report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    _print(f"detalhes: {out}")
    return report.exit_code


# ── parser ──────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="troca-produto", description="TDP — Troca de Produto em VSL")
    parser.add_argument("--version", action="version", version=f"TDP {__version__}")
    parser.add_argument("--root", default=".", help="pasta onde está o .env (padrão: .)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="cria o briefing.json")
    p.add_argument("--out", default="briefing.json")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="confere ambiente, dependências e chaves")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("analyze", help="acha o produto antigo na VSL")
    p.add_argument("--briefing", required=True)
    p.add_argument("--dir", required=True)
    p.add_argument("--every", type=float, default=1.0, help="segundos entre frames amostrados")
    p.add_argument("--visual-threshold", type=float, default=0.78)
    p.add_argument("--ocr", action="store_true", help="também procurar o nome ESCRITO na tela")
    p.add_argument("--force", action="store_true", help="ignora cache (áudio, frames, transcrição)")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("review", help="revisa as aparições detectadas")
    p.add_argument("--dir", required=True)
    p.add_argument("--drop", help="derruba faixas: 1,3,5-7")
    p.add_argument("--keep", help="recupera faixas derrubadas")
    p.add_argument("--keep-only", help="mantém só estas")
    p.add_argument("--set-asset", action="append", help="3=assets/novo/pack_6.png")
    p.add_argument("--auto-assets", action="store_true", help="escolhe a arte por quantidade falada")
    p.add_argument("--sheet", action="store_true", help="monta o contact sheet pra conferência visual")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("refs", help="frames candidatos a old_assets (quando você não tem foto do produto antigo)")
    p.add_argument("--dir", required=True)
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(func=cmd_refs)

    p = sub.add_parser("rebrand", help="plano (e geração) da troca de voz")
    p.add_argument("--dir", required=True)
    p.add_argument("--run", action="store_true", help="gera o áudio no MiniMax (clone → gera → deleta)")
    p.set_defaults(func=cmd_rebrand)

    p = sub.add_parser("export", help="gera troca_COMPLETO.xml + CSV + report")
    p.add_argument("--dir", required=True)
    p.add_argument("--name", default="troca_COMPLETO")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("cutout", help="recorta o produto (PNG transparente)")
    p.add_argument("--input", required=True, help="arquivo ou pasta")
    p.add_argument("--out", help="destino (padrão: sobrescreve, preservando o nome)")
    p.add_argument("--audit", action="store_true", help="só aponta os PNG falsos (sem alpha)")
    p.set_defaults(func=cmd_cutout)

    p = sub.add_parser("qa", help="caça sobras do produto antigo no vídeo FINAL")
    p.add_argument("--video", required=True)
    p.add_argument("--dir", required=True)
    p.add_argument("--every", type=float, default=2.0)
    p.set_defaults(func=cmd_qa)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _print("cancelado")
        return EXIT_ERROR
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
        _print(f"erro: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
