"""Configuração: .env da raiz + variáveis TDP_*.

Sem dependência de python-dotenv — parser simples, previsível.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Motores de detecção visual.
#   cloud → CLIP (+ visão do Claude, se houver crédito). NUNCA OpenCV.
#   clip  → só CLIP local.
#   local → OpenCV/template matching. DESACONSELHADO: marca ~73% do vídeo de lixo.
VISUAL_ENGINES = ("cloud", "clip", "local")
DEFAULT_VISUAL_ENGINE = "cloud"


def parse_env_file(text: str) -> dict[str, str]:
    """Lê o conteúdo de um .env. Ignora comentários, aceita `export`, tira aspas."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load_dotenv(root: str | os.PathLike[str] | None = None, *, override: bool = False) -> dict[str, str]:
    """Carrega `.env` de `root` (ou cwd) para o os.environ. Retorna o que leu."""
    base = Path(root) if root else Path.cwd()
    path = base / ".env"
    if not path.is_file():
        return {}
    values = parse_env_file(path.read_text(encoding="utf-8"))
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "sim"}


def resolve_visual_engine(env: dict[str, str] | None = None) -> str:
    """TDP_VISUAL_ENGINE normalizado. Valor desconhecido cai no padrão (cloud)."""
    source = env if env is not None else os.environ
    raw = (source.get("TDP_VISUAL_ENGINE") or "").strip().lower()
    if raw in VISUAL_ENGINES:
        return raw
    return DEFAULT_VISUAL_ENGINE


@dataclass
class Settings:
    minimax_api_key: str = ""
    anthropic_api_key: str = ""
    visual_engine: str = DEFAULT_VISUAL_ENGINE
    hotwords: bool = False
    whisper_model: str = "large-v3"
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def has_minimax(self) -> bool:
        return bool(self.minimax_api_key)

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def uses_opencv(self) -> bool:
        return self.visual_engine == "local"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        source = dict(env) if env is not None else dict(os.environ)
        return cls(
            minimax_api_key=source.get("MINIMAX_API_KEY", "").strip(),
            anthropic_api_key=source.get("ANTHROPIC_API_KEY", "").strip(),
            visual_engine=resolve_visual_engine(source),
            hotwords=(source.get("TDP_HOTWORDS", "").strip().lower() in {"1", "true", "yes", "on", "sim"}),
            whisper_model=source.get("TDP_WHISPER_MODEL", "large-v3").strip() or "large-v3",
            extras=source,
        )


def load_settings(root: str | os.PathLike[str] | None = None) -> Settings:
    load_dotenv(root)
    return Settings.from_env()
