#!/bin/bash
set -e

echo "======================================"
echo " VSL B-Roll Generator — Instalação UXP"
echo "======================================"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UXP_DIR="$HOME/Library/Application Support/Adobe/UXP/PluginsStorage/PPRO/Developer"
PLUGIN_DIR="$UXP_DIR/com.vsl.brollgenerator"

# 1. Instala plugin UXP
echo "[1/2] Instalando plugin UXP..."
mkdir -p "$PLUGIN_DIR"
cp -r "$SCRIPT_DIR/uxp-plugin/." "$PLUGIN_DIR/"
echo "      Plugin em: $PLUGIN_DIR"

# 2. Verifica backend Python
echo "[2/2] Verificando backend Python..."
if [ ! -d "$SCRIPT_DIR/backend/.venv" ]; then
  echo "ERRO: Backend não instalado. Execute primeiro: ./install_mac.sh"
  exit 1
fi
echo "      Backend OK."

echo ""
echo "======================================"
echo "✅ Instalação UXP concluída!"
echo ""
echo "PRÓXIMOS PASSOS:"
echo "  1. Abra o Premiere Pro 2026"
echo "  2. Menu: Janela → Extensões (UXP) → Gerenciar Plugins"
echo "     OU: Help → UXP Developer Tools"
echo "  3. Clique em '+ Add Plugin' e selecione:"
echo "     $PLUGIN_DIR/manifest.json"
echo "  4. Em outro terminal, execute: ./start_server.sh"
echo "======================================"
