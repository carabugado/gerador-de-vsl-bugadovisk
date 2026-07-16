#!/bin/bash
set -e

PNG_URL="https://raw.githubusercontent.com/carabugado/gerador-de-vsl-bugadovisk/claude/guideify-premiere-install-l748r6/docs/Guideify_AnuncioFace_9x16.png"

echo "=============================================="
echo " Guideify — Adicionar overlay 'Anúncio Face'"
echo "=============================================="

# 1. Localiza a extensão Guideify instalada
DIRS=(
  "$HOME/Library/Application Support/Adobe/CEP/extensions"
  "/Library/Application Support/Adobe/CEP/extensions"
)
EXT=""
for d in "${DIRS[@]}"; do
  hit=$(find "$d" -maxdepth 1 -iname "*guideify*" -type d -print -quit 2>/dev/null || true)
  if [ -n "$hit" ]; then EXT="$hit"; break; fi
done
if [ -z "$EXT" ]; then
  for d in "${DIRS[@]}"; do
    hit=$(grep -ril "guideify" "$d"/*/CSXS/manifest.xml 2>/dev/null | head -n1 || true)
    if [ -n "$hit" ]; then EXT="$(dirname "$(dirname "$hit")")"; break; fi
  done
fi
if [ -z "$EXT" ]; then
  echo "❌ Não achei o Guideify instalado nas pastas de extensões CEP."
  echo "   Pastas verificadas:"
  printf '   - %s\n' "${DIRS[@]}"
  exit 1
fi
echo "📁 Guideify encontrado em: $EXT"

# 2. Baixa o overlay do anúncio
TMP_PNG="/tmp/Guideify_AnuncioFace_9x16.png"
echo "⬇️  Baixando overlay..."
curl -fsSL "$PNG_URL" -o "$TMP_PNG"

# 3. Procura a pasta onde ficam os overlays nativos (Reels/TikTok/Shorts)
OVERLAY_DIR=$(find "$EXT" -type f \
  \( -iname "*reels*" -o -iname "*tiktok*" -o -iname "*shorts*" -o -iname "*overlay*" -o -iname "*safe*" \) \
  \( -iname "*.png" -o -iname "*.svg" -o -iname "*.mov" -o -iname "*.mp4" -o -iname "*.webp" \) \
  -print0 2>/dev/null | xargs -0 -n1 dirname 2>/dev/null | sort | uniq -c | sort -rn | head -n1 | sed -E 's/^ *[0-9]+ //')

if [ -n "$OVERLAY_DIR" ]; then
  cp "$TMP_PNG" "$OVERLAY_DIR/"
  echo "✅ Overlay copiado para dentro do plugin: $OVERLAY_DIR"
  echo "   Arquivos nessa pasta agora:"
  ls -1 "$OVERLAY_DIR" | sed 's/^/     /'
else
  cp "$TMP_PNG" "$EXT/"
  echo "⚠️  Não identifiquei a pasta de overlays nativos — copiei para a raiz: $EXT"
fi

# 4. Diagnóstico: estrutura do plugin + onde as plataformas são definidas
echo ""
echo "════════ COPIE DAQUI PRA BAIXO E MANDE PRO CLAUDE ════════"
echo "--- ESTRUTURA DO PLUGIN ---"
find "$EXT" -maxdepth 3 -not -path "*/node_modules/*" | sed "s|$EXT|.|" | head -n 120
echo ""
echo "--- ARQUIVOS QUE DEFINEM AS PLATAFORMAS ---"
grep -rl "Shorts\|Reels\|TikTok" --include="*.js" --include="*.html" --include="*.json" --include="*.jsx" "$EXT" 2>/dev/null | sed "s|$EXT|.|"
echo ""
echo "--- TRECHOS ONDE 'Shorts' APARECE ---"
grep -rn "Shorts" --include="*.js" --include="*.json" --include="*.html" "$EXT" 2>/dev/null | head -n 30 | cut -c1-220
echo "════════════════ FIM — COPIE ATÉ AQUI ════════════════"
echo ""
echo "Reinicie o Premiere (Cmd+Q) e veja se o overlay já aparece no Guideify."
echo "Se não aparecer, me mande o bloco acima que eu crio o botão nativo no painel."
