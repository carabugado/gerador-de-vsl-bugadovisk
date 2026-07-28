#!/usr/bin/env bash
# Instalação do TDP no macOS (Apple Silicon).
set -euo pipefail

cd "$(dirname "$0")"

echo "▸ 1/6  ffmpeg"
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "   ffmpeg não encontrado. Instalando via Homebrew…"
  brew install ffmpeg
fi
ffmpeg -version | head -1

echo "▸ 2/6  venv (python3.12)"
PY=python3.12
command -v $PY >/dev/null 2>&1 || PY=python3
$PY -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python --version

echo "▸ 3/6  pip"
pip install -U pip >/dev/null

echo "▸ 4/6  ferramenta + Whisper + CLIP(torch) + yt-dlp  (demora, baixa torch)"
pip install -e ".[dev,url,clip,asr]"

echo "▸ 5/6  recorte de produto + diarização"
# setuptools<81 é obrigatório: webrtcvad (via resemblyzer) usa pkg_resources
pip install "rembg[cpu]" resemblyzer scikit-learn "setuptools<81"

echo "▸ 6/6  testes"
python -m pytest -q

echo
echo "✔ pronto. Agora:"
echo "   source .venv/bin/activate"
echo "   cp .env.example .env      # e preencha MINIMAX_API_KEY"
echo "   python -m troca_produto doctor"
