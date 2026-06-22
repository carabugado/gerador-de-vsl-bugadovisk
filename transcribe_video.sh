#!/bin/bash
# Gera a transcrição (.srt) de um vídeo/áudio com Whisper local, pronta pra colar
# no HIGHLIGHTS_MAP. Reusa o mesmo Whisper/cache do backend do plugin.
#
# Uso:
#   ./transcribe_video.sh "/Volumes/portatil/TRABALHOS/JOVEM NERD/episodio.mp4"
#   ./transcribe_video.sh "/caminho/episodio.mp4" minha_legenda.srt
#
# Sem o 2º argumento, salva o .srt ao lado do vídeo.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -z "$1" ]; then
  echo "Uso: ./transcribe_video.sh \"/caminho/do/video.mp4\" [saida.srt]"
  exit 1
fi

if [ ! -x "$SCRIPT_DIR/backend/.venv/bin/python" ]; then
  echo "ERRO: ambiente Python não encontrado. Rode ./install_mac.sh primeiro."
  exit 1
fi

source "$SCRIPT_DIR/backend/.venv/bin/activate"
cd "$SCRIPT_DIR/backend"

if [ -n "$2" ]; then
  python -m transcribe "$1" -o "$2"
else
  python -m transcribe "$1"
fi
