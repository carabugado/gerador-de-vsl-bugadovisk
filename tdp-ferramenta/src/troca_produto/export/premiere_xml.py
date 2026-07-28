"""`troca_COMPLETO.xml` — o padrão de entrega.

    V1   vídeo CORTADO e COLORIDO por tipo de aparição (+ marcadores)
    V2   produto novo posicionado, alinhado ao MESMO frame do V1
    A1   áudio original
    A2+  clipes de VOZ visíveis (mp3 mono, uma faixa por locutor quando dá)

É esse XML "cortado colorido" que o editor abre. Tudo o resto (CSV, report)
é apoio.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from urllib.request import pathname2url

from ..media.ffmpeg import frames_to_timecode, seconds_to_frames

#: Cor do clipe no Premiere por tipo de aparição.
LABEL_BY_KIND = {
    "spoken": "Rose",        # falado
    "onscreen": "Cerulean",  # texto na tela
    "visual": "Mango",       # produto aparecendo
    "mixed": "Violet",       # mais de um tipo no mesmo trecho
    "new": "Forest",         # produto novo (V2)
    "voice": "Lavender",     # clipe de voz regravada (A2+)
    "original": "Iris",      # áudio original (A1)
}
VALID_LABELS = set(LABEL_BY_KIND.values())

KIND_PT = {
    "spoken": "falado",
    "onscreen": "texto na tela",
    "visual": "visual",
    "mixed": "misto",
}


def label_for(kind: str) -> str:
    return LABEL_BY_KIND.get(kind, LABEL_BY_KIND["mixed"])


def path_to_url(path: str | os.PathLike[str]) -> str:
    """file:// seguro — inclusive pra pasta com colchete tipo [GRUPO FENIX]."""
    absolute = Path(path).expanduser().resolve()
    # pathname2url já faz o percent-encoding; passar por quote() de novo
    # geraria %255B e o Premiere não acharia o arquivo.
    return "file://localhost" + pathname2url(str(absolute))


@dataclass
class MediaFile:
    path: str
    name: str = ""
    kind: str = "video"  # video | image | audio
    duration_frames: int = 0
    width: int = 1920
    height: int = 1080

    def __post_init__(self):
        if not self.name:
            self.name = Path(self.path).name


@dataclass
class Clip:
    file: MediaFile
    start: int          # frame na timeline
    end: int            # frame na timeline
    in_point: int = 0   # frame na fonte
    out_point: int = 0
    name: str = ""
    label: str = "Violet"
    comment: str = ""

    def __post_init__(self):
        if not self.name:
            self.name = self.file.name
        if self.out_point <= self.in_point:
            self.out_point = self.in_point + max(1, self.end - self.start)

    @property
    def duration(self) -> int:
        return max(1, self.end - self.start)


@dataclass
class Marker:
    frame: int
    name: str
    comment: str = ""


@dataclass
class Timeline:
    name: str = "troca_COMPLETO"
    fps: float = 30.0
    width: int = 1920
    height: int = 1080
    duration_frames: int = 0
    video_tracks: list[list[Clip]] = field(default_factory=list)
    audio_tracks: list[list[Clip]] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)


# ── montagem do XML ─────────────────────────────────────────────────────────
def _sub(parent: ET.Element, tag: str, text=None) -> ET.Element:
    node = ET.SubElement(parent, tag)
    if text is not None:
        node.text = str(text)
    return node


def _rate(parent: ET.Element, fps: float) -> None:
    rate = _sub(parent, "rate")
    _sub(rate, "timebase", int(round(fps)))
    _sub(rate, "ntsc", "TRUE" if abs(fps - round(fps)) > 1e-3 else "FALSE")


def _file_element(parent: ET.Element, media: MediaFile, fps: float, *, file_ids: dict, defined: set) -> None:
    """Define o <file> na primeira aparição; depois só referencia pelo id."""
    key = str(Path(media.path))
    if key not in file_ids:
        file_ids[key] = f"file-{len(file_ids) + 1}"
    file_id = file_ids[key]
    node = _sub(parent, "file")
    node.set("id", file_id)
    if file_id in defined:
        return
    defined.add(file_id)
    _sub(node, "name", media.name)
    _sub(node, "pathurl", path_to_url(media.path))
    _rate(node, fps)
    _sub(node, "duration", max(1, media.duration_frames))
    media_node = _sub(node, "media")
    if media.kind in ("video", "image"):
        video = _sub(media_node, "video")
        _sub(video, "duration", max(1, media.duration_frames))
        chars = _sub(video, "samplecharacteristics")
        _sub(chars, "width", media.width)
        _sub(chars, "height", media.height)
    if media.kind in ("video", "audio"):
        audio = _sub(media_node, "audio")
        chars = _sub(audio, "samplecharacteristics")
        _sub(chars, "depth", 16)
        _sub(chars, "samplerate", 48000)
        _sub(audio, "channelcount", 1 if media.kind == "audio" else 2)


def _clip_element(track: ET.Element, clip: Clip, clip_id: str, fps: float, *, media_type: str, file_ids: dict, defined: set) -> None:
    node = _sub(track, "clipitem")
    node.set("id", clip_id)
    _sub(node, "name", clip.name)
    _sub(node, "enabled", "TRUE")
    _sub(node, "duration", clip.duration)
    _rate(node, fps)
    _sub(node, "start", clip.start)
    _sub(node, "end", clip.end)
    _sub(node, "in", clip.in_point)
    _sub(node, "out", clip.out_point)
    _file_element(node, clip.file, fps, file_ids=file_ids, defined=defined)
    if media_type == "audio":
        source = _sub(node, "sourcetrack")
        _sub(source, "mediatype", "audio")
        _sub(source, "trackindex", 1)
    else:
        source = _sub(node, "sourcetrack")
        _sub(source, "mediatype", "video")
    labels = _sub(node, "labels")
    _sub(labels, "label2", clip.label if clip.label in VALID_LABELS else "Violet")
    if clip.comment:
        _sub(node, "comments").text = clip.comment
        _sub(node, "logginginfo").text = clip.comment


def build_xml(timeline: Timeline) -> ET.ElementTree:
    root = ET.Element("xmeml", {"version": "5"})
    sequence = _sub(root, "sequence")
    sequence.set("id", "sequence-1")
    _sub(sequence, "name", timeline.name)
    _sub(sequence, "duration", max(1, timeline.duration_frames))
    _rate(sequence, timeline.fps)

    timecode = _sub(sequence, "timecode")
    _rate(timecode, timeline.fps)
    _sub(timecode, "string", frames_to_timecode(0, timeline.fps))
    _sub(timecode, "frame", 0)
    _sub(timecode, "displayformat", "NDF")

    media = _sub(sequence, "media")
    video = _sub(media, "video")
    fmt = _sub(video, "format")
    chars = _sub(fmt, "samplecharacteristics")
    _rate(chars, timeline.fps)
    _sub(chars, "width", timeline.width)
    _sub(chars, "height", timeline.height)

    file_ids: dict = {}
    defined: set = set()
    counter = 0
    for clips in timeline.video_tracks:
        track = _sub(video, "track")
        for clip in clips:
            counter += 1
            _clip_element(
                track,
                clip,
                f"clipitem-{counter}",
                timeline.fps,
                media_type="video",
                file_ids=file_ids,
                defined=defined,
            )
        _sub(track, "enabled", "TRUE")
        _sub(track, "locked", "FALSE")

    audio = _sub(media, "audio")
    for clips in timeline.audio_tracks:
        track = _sub(audio, "track")
        for clip in clips:
            counter += 1
            _clip_element(
                track,
                clip,
                f"clipitem-{counter}",
                timeline.fps,
                media_type="audio",
                file_ids=file_ids,
                defined=defined,
            )
        _sub(track, "enabled", "TRUE")
        _sub(track, "locked", "FALSE")

    for marker in timeline.markers:
        node = _sub(sequence, "marker")
        _sub(node, "name", marker.name)
        _sub(node, "comment", marker.comment)
        _sub(node, "in", marker.frame)
        _sub(node, "out", -1)

    return ET.ElementTree(root)


def write_xml(timeline: Timeline, path: str | os.PathLike[str]) -> str:
    """Escreve o XML com o DOCTYPE que o Premiere espera."""
    tree = build_xml(timeline)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = ET.tostring(tree.getroot(), encoding="unicode")
    target.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n",
        encoding="utf-8",
    )
    return str(target)


# ── montagem a partir da análise ────────────────────────────────────────────
def build_timeline(
    *,
    name: str = "troca_COMPLETO",
    video_path: str,
    duration: float,
    fps: float = 30.0,
    width: int = 1920,
    height: int = 1080,
    ranges,
    new_assets: dict | None = None,
    voice_items: dict | None = None,
    audio_path: str | None = None,
) -> Timeline:
    """Monta V1/V2/A1/A2+ a partir das faixas detectadas e do plano de voz.

    `new_assets`: {índice da faixa → caminho do produto novo}
    `voice_items`: {speaker_id → [(start, end, mp3_path, texto)]}
    """
    total_frames = max(1, seconds_to_frames(duration, fps))
    source = MediaFile(path=video_path, kind="video", duration_frames=total_frames, width=width, height=height)

    v1: list[Clip] = []
    v2: list[Clip] = []
    markers: list[Marker] = []
    assets = dict(new_assets or {})

    for index, item in enumerate(ranges):
        start_f = seconds_to_frames(item.start, fps)
        end_f = max(start_f + 1, seconds_to_frames(item.end, fps))
        kind = item.kind
        pretty = KIND_PT.get(kind, kind)
        v1.append(
            Clip(
                file=source,
                start=start_f,
                end=end_f,
                in_point=start_f,
                out_point=end_f,
                name=f"{index + 1:02d} {pretty} — {item.label or 'produto antigo'}",
                label=label_for(kind),
                comment=f"{pretty} | score {item.score:.2f} | {frames_to_timecode(start_f, fps)}",
            )
        )
        markers.append(
            Marker(
                frame=start_f,
                name=f"{index + 1:02d} {pretty}",
                comment=item.label or f"produto antigo ({pretty})",
            )
        )
        asset = assets.get(index) or assets.get(str(index))
        if asset:
            still = MediaFile(path=asset, kind="image", duration_frames=max(1, end_f - start_f), width=width, height=height)
            v2.append(
                Clip(
                    file=still,
                    start=start_f,  # MESMO frame do V1
                    end=end_f,
                    in_point=0,
                    out_point=max(1, end_f - start_f),
                    name=f"NOVO {index + 1:02d} — {Path(asset).name}",
                    label=LABEL_BY_KIND["new"],
                    comment="produto novo alinhado ao clipe do V1",
                )
            )

    audio_file = MediaFile(
        path=audio_path or video_path,
        kind="audio" if audio_path else "video",
        duration_frames=total_frames,
    )
    a1 = [
        Clip(
            file=audio_file,
            start=0,
            end=total_frames,
            in_point=0,
            out_point=total_frames,
            name="áudio original",
            label=LABEL_BY_KIND["original"],
        )
    ]

    audio_tracks: list[list[Clip]] = [a1]
    for speaker_id, items in sorted((voice_items or {}).items()):
        track: list[Clip] = []
        for start, end, mp3_path, text in items:
            start_f = seconds_to_frames(start, fps)
            end_f = max(start_f + 1, seconds_to_frames(end, fps))
            clip_file = MediaFile(path=mp3_path, kind="audio", duration_frames=max(1, end_f - start_f))
            track.append(
                Clip(
                    file=clip_file,
                    start=start_f,
                    end=end_f,
                    in_point=0,
                    out_point=max(1, end_f - start_f),
                    name=f"VOZ {speaker_id}: {text[:40]}",
                    label=LABEL_BY_KIND["voice"],
                    comment=text,
                )
            )
        if track:
            audio_tracks.append(track)

    return Timeline(
        name=name,
        fps=fps,
        width=width,
        height=height,
        duration_frames=total_frames,
        video_tracks=[v1, v2],
        audio_tracks=audio_tracks,
        markers=markers,
    )
