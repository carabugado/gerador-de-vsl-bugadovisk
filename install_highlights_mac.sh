#!/bin/bash
set -e

echo "======================================"
echo " Highlights Cutter — Instalação Mac"
echo "======================================"

# 1. Habilita extensões CEP não assinadas
echo "[1/2] Habilitando modo debug CEP..."
defaults write com.adobe.CSXS.12 PlayerDebugMode 1 2>/dev/null || true
defaults write com.adobe.CSXS.11 PlayerDebugMode 1 2>/dev/null || true

# 2. Instala a extensão (separada do painel VSL B-Roll)
CEP_DIR="$HOME/Library/Application Support/Adobe/CEP/extensions/com.highlights.cutter"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[2/2] Instalando extensão CEP em: $CEP_DIR"
rm -rf "$CEP_DIR"
mkdir -p "$CEP_DIR"
cp -r "$SCRIPT_DIR/cep-highlights/." "$CEP_DIR/"

echo ""
echo "======================================"
echo "✅ Instalação concluída!"
echo ""
echo "PRÓXIMOS PASSOS:"
echo "  1. Ligue o backend:  ./start_server.sh   (deixe a janela aberta)"
echo "  2. REINICIE o Premiere (com o vídeo de 1h na timeline)"
echo "  3. Menu: Janela → Extensões → Highlights Cutter"
echo "  4. Escolha a duração → 'Mapear melhores momentos' → 'Cortar em nova sequência'"
echo "     (origem detectada da timeline; transcrição via Whisper é automática)"
echo "======================================"
