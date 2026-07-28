"""Executa o rebrand de voz: diariza, planeja e (se pedirem) gera o áudio.

Ordem que funciona:
  1. diarizar no áudio LIMPO (base.wav), nunca no vídeo com TTS embutido;
  2. montar o centroide do narrador com trechos da seção de oferta;
  3. classificar cada menção (sim >= 0.85 → narrador);
  4. narrador troca a palavra / depoimento regrava a frase;
  5. clonar a PRÓPRIA fala do locutor (clone → gera → deleta, um por vez).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..briefing import Briefing
from ..config import Settings
from ..media import audio as audio_mod
from ..media.ffmpeg import run
from ..media.wavio import read_wav, slice_samples, write_wav
from ..pipeline.text_detect import Mention, to_tokens
from ..project import Project
from . import diarize
from .minimax import MinimaxClient, MinimaxError
from .rebrand import RebrandItem, build_plan

NARRATOR_REF_COUNT = 5
MENTION_PAD = 0.6


def offer_section(duration: float, *, fraction: float = 0.35) -> tuple[float, float]:
    """A seção de oferta: parte final da VSL, onde o narrador fala sozinho."""
    if duration <= 0:
        return 0.0, 0.0
    return max(0.0, duration * (1.0 - fraction)), duration


def pick_narrator_spans(
    words,
    duration: float,
    *,
    count: int = NARRATOR_REF_COUNT,
    span: float = 4.0,
) -> list[tuple[float, float]]:
    """Trechos contínuos de fala dentro da oferta, pra montar o centroide."""
    tokens = to_tokens(words)
    start, end = offer_section(duration)
    inside = [t for t in tokens if t.start >= start and t.end <= end]
    if not inside:
        inside = tokens
    if not inside:
        return []
    spans: list[tuple[float, float]] = []
    cursor = inside[0].start
    while cursor + span <= inside[-1].end and len(spans) < count:
        window = [t for t in inside if cursor <= t.start and t.end <= cursor + span]
        if len(window) >= 4:  # trecho com fala de verdade
            spans.append((round(cursor, 3), round(cursor + span, 3)))
            cursor += span * 1.5
        else:
            cursor += span / 2
    return spans


def export_span(source: str | os.PathLike[str], out_path: str | os.PathLike[str], start: float, end: float) -> str:
    run(audio_mod.slice_cmd(source, out_path, start, end), check=False)
    return str(out_path)


@dataclass
class RebrandRun:
    items: list[RebrandItem] = field(default_factory=list)
    speakers: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    generated: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "items": [i.to_dict() for i in self.items],
            "speakers": self.speakers,
            "warnings": self.warnings,
            "generated": self.generated,
        }


def diarize_mentions(
    project: Project,
    mentions: list[Mention],
    words,
    duration: float,
) -> tuple[list, list[str]]:
    """Rotula cada menção como narrador ou locutor distinto."""
    warnings: list[str] = []
    base = project.base_audio  # SEMPRE o áudio limpo da VSL base
    if not base.is_file():
        return [], [f"áudio limpo não encontrado ({base}); rode o analyze antes do rebrand."]

    ref_spans = pick_narrator_spans(words, duration)
    if not ref_spans:
        return [], ["não deu pra montar a referência do narrador (transcrição vazia?)."]

    tmp = project.voice_dir / "ref"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        ref_embeddings = []
        for i, (start, end) in enumerate(ref_spans):
            piece = tmp / f"narrador_{i:02d}.wav"
            export_span(base, piece, start, end)
            ref_embeddings.append(diarize.embed_wav(str(piece)))
        reference = diarize.NarratorReference.from_embeddings(ref_embeddings, sources=[str(s) for s in ref_spans])
    except Exception as exc:  # resemblyzer ausente ou áudio ruim
        return [], [f"diarização indisponível ({exc}); tudo será tratado como narrador."]

    samples, sample_rate = read_wav(str(base))
    spans: list[tuple[float, float]] = []
    embeddings = []
    f0s: list[float] = []
    for index, mention in enumerate(mentions):
        start = max(0.0, mention.start - MENTION_PAD)
        end = mention.end + MENTION_PAD
        piece = tmp / f"mencao_{index:03d}.wav"
        export_span(base, piece, start, end)
        try:
            embeddings.append(diarize.embed_wav(str(piece)))
        except Exception as exc:
            warnings.append(f"menção {index + 1}: sem embedding ({exc})")
            continue
        spans.append((start, end))
        f0s.append(diarize.estimate_f0(slice_samples(samples, sample_rate, start, end), sample_rate))

    tags = diarize.tag_speakers(spans, embeddings, reference, f0s=f0s)
    return tags, warnings


def run_rebrand(
    project: Project,
    briefing: Briefing,
    settings: Settings,
    mentions: list[Mention],
    words,
    duration: float,
    *,
    generate: bool = False,
    client: MinimaxClient | None = None,
) -> RebrandRun:
    """Monta o plano e, com `generate=True`, gera os mp3 de voz."""
    project.ensure()
    tags, warnings = diarize_mentions(project, mentions, words, duration)
    tokens = to_tokens(words)
    items = build_plan(mentions, tokens, tags, new_product_name=briefing.new_product_name)
    result = RebrandRun(items=items, speakers=[t.to_dict() for t in tags], warnings=list(warnings))

    for item in items:
        if item.role == "narrator" and item.gender == "female":
            item.gender = "male"
            result.warnings.append("narrador marcado como feminino pelo F0 — corrigido, narrador nunca usa voz feminina.")

    if not generate:
        return result

    api = client or MinimaxClient(settings.minimax_api_key)
    base = project.base_audio
    samples, sample_rate = read_wav(str(base)) if base.is_file() else (None, 16000)

    for index, item in enumerate(items):
        out_mp3 = project.voice_dir / f"voz_{index:03d}_{item.speaker_id}.mp3"
        reference_wav = None
        if item.reference_spans and samples is not None:
            # concatena os trechos limpos do locutor: clone longo sai MUITO melhor
            chunks = [slice_samples(samples, sample_rate, a, b) for a, b in item.reference_spans]
            merged = _concat(chunks)
            if merged is not None and len(merged) > sample_rate * 3:
                reference_wav = project.voice_dir / f"ref_{item.speaker_id}.wav"
                write_wav(reference_wav, merged, sample_rate)
        try:
            if reference_wav:
                audio = api.speak_cloned(reference_wav, item.text_to_speak).audio
            else:
                audio = api.t2a(item.text_to_speak, item.voice.voice_id).audio
            out_mp3.parent.mkdir(parents=True, exist_ok=True)
            out_mp3.write_bytes(audio)
            item.audio_path = str(out_mp3)
            result.generated.append(str(out_mp3))
        except MinimaxError as exc:
            result.warnings.append(f"menção {index + 1}: {exc}")
    return result


def _concat(chunks):
    usable = [c for c in chunks if c is not None and len(c)]
    if not usable:
        return None
    return np.concatenate(usable)


def voice_tracks_for_xml(items: list[RebrandItem]) -> dict:
    """{speaker_id: [(start, end, mp3, texto)]} — vira A2+ no Premiere."""
    tracks: dict = {}
    for item in items:
        if not item.audio_path:
            continue
        tracks.setdefault(item.speaker_id or "narrator", []).append(
            (item.start, item.end, item.audio_path, item.text_to_speak)
        )
    for values in tracks.values():
        values.sort(key=lambda t: t[0])
    return tracks
