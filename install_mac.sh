#!/bin/bash
set -e

echo "======================================"
echo " VSL B-Roll Generator — Instalação Mac"
echo "======================================"

# 1. Habilita extensões não assinadas no Premiere Pro (CEP 12)
echo "[1/4] Habilitando modo debug CEP..."
defaults write com.adobe.CSXS.12 PlayerDebugMode 1
defaults write com.adobe.CSXS.11 PlayerDebugMode 1

# 2. Instala a extensão CEP no Premiere
CEP_DIR="$HOME/Library/Application Support/Adobe/CEP/extensions/com.vsl.brollgenerator"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[2/4] Instalando extensão CEP em: $CEP_DIR"
rm -rf "$CEP_DIR"
mkdir -p "$CEP_DIR"
cp -r "$SCRIPT_DIR/cep/." "$CEP_DIR/"

# 3. Cria ambiente Python e instala dependências
echo "[3/4] Configurando ambiente Python..."
cd "$SCRIPT_DIR/backend"

if ! command -v python3 &>/dev/null; then
  echo "ERRO: Python 3 não encontrado. Instale via https://brew.sh → 'brew install python'"
  exit 1
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Verifica ffmpeg
if ! command -v ffmpeg &>/dev/null; then
  echo ""
  echo "⚠️  ffmpeg não encontrado. Instale com: brew install ffmpeg"
  echo "    (necessário para extração de áudio)"
fi

echo "[4/4] Preparando o atalho de inicialização..."
# Usa o start_server.sh versionado no repo (ele sobe o Ollama de reserva e exporta os
# modelos). NÃO regerar aqui — a versão mínima antiga apagava esse comportamento e
# fazia o pipeline cair pra nuvem sem o usuário entender por quê.
if [ -f "$SCRIPT_DIR/start_server.sh" ]; then
  chmod +x "$SCRIPT_DIR/start_server.sh"
else
  echo "⚠️  start_server.sh não encontrado no repo — rode o servidor com:"
  echo "    cd backend && source .venv/bin/activate && python server.py"
fi

echo ""
echo "======================================"
echo "✅ Instalação concluída!"
echo ""
echo "PRÓXIMOS PASSOS:"
echo "  1. Execute: ./start_server.sh"
echo "  2. Abra o Premiere Pro"
echo "  3. Menu: Janela → Extensões → VSL B-Roll Generator"
echo "======================================"
