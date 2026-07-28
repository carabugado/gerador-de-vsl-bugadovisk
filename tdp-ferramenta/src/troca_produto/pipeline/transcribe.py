"""Transcrição (Whisper local), com hotwords.

TDP_HOTWORDS=1 injeta o nome antigo como `initial_prompt`: o Whisper passa a
grafar a marca do jeito certo em boa parte das vezes. Onde ele ainda entortar,
quem resolve é o match fuzzy do `text_detect` — nunca um regex.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .text_detect import hotwords_prompt


@dataclass
class Transcript:
    text: str = ""
    language: str = ""
    segments: list[dict] = field(default_factory=list)
    words: list[dict] = field(default_factory=list)
    model: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "language": self.language,
            "segments": self.segments,
            "words": self.words,
            "model": self.model,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Transcript":
        return cls(
            text=data.get("text", ""),
            language=data.get("language", ""),
            segments=data.get("segments") or [],
            words=data.get("words") or [],
            model=data.get("model", ""),
            source=data.get("source", ""),
        )

    def save(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Transcript":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def flatten_words(segments: list[dict]) -> list[dict]:
    """Extrai a lista plana de palavras com timestamp do resultado do Whisper."""
    words: list[dict] = []
    for seg in segments or []:
        for word in seg.get("words") or []:
            text = (word.get("word") or word.get("text") or "").strip()
            if not text:
                continue
            words.append(
                {
                    "word": text,
                    "start": float(word.get("start", seg.get("start", 0.0)) or 0.0),
                    "end": float(word.get("end", seg.get("end", 0.0)) or 0.0),
                }
            )
    return words


def build_options(
    *,
    language: str | None = None,
    hotwords: bool = False,
    terms: list[str] | None = None,
    temperature: float = 0.0,
) -> dict:
    options: dict = {
        "word_timestamps": True,
        "temperature": temperature,
        "condition_on_previous_text": False,
    }
    if language:
        options["language"] = language
    if hotwords:
        prompt = hotwords_prompt(terms or [])
        if prompt:
            options["initial_prompt"] = prompt
    return options


def transcribe(
    source: str | os.PathLike[str],
    *,
    model_name: str = "large-v3",
    language: str | None = None,
    hotwords: bool = False,
    terms: list[str] | None = None,
    cache_path: str | os.PathLike[str] | None = None,
    force: bool = False,
) -> Transcript:
    """Transcreve com Whisper local. Usa cache se já existir."""
    if cache_path and not force and Path(cache_path).is_file():
        return Transcript.load(cache_path)

    import whisper  # noqa: PLC0415 — extra `asr`

    model = whisper.load_model(model_name)
    result = model.transcribe(str(source), **build_options(language=language, hotwords=hotwords, terms=terms))
    transcript = Transcript(
        text=(result.get("text") or "").strip(),
        language=result.get("language") or (language or ""),
        segments=result.get("segments") or [],
        words=flatten_words(result.get("segments") or []),
        model=model_name,
        source=str(source),
    )
    if cache_path:
        transcript.save(cache_path)
    return transcript
