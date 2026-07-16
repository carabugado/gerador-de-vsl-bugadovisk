#!/bin/bash
set -e

RAW_BASE="https://raw.githubusercontent.com/carabugado/gerador-de-vsl-bugadovisk/claude/guideify-premiere-install-l748r6/docs"

echo "=========================================================="
echo " Guideify — Botões nativos de anúncio (Face/TikTok/Reels/Shorts)"
echo "=========================================================="

echo "⬇️  Baixando overlays..."
curl -fsSL "$RAW_BASE/Guideify_AnuncioFace_9x16.png"      -o /tmp/g_anuncio.png
curl -fsSL "$RAW_BASE/Guideify_Anuncio_tiktok_9x16.png"   -o /tmp/g_anuncio_tiktok.png
curl -fsSL "$RAW_BASE/Guideify_Anuncio_reels_9x16.png"    -o /tmp/g_anuncio_reels.png
curl -fsSL "$RAW_BASE/Guideify_Anuncio_shorts_9x16.png"   -o /tmp/g_anuncio_shorts.png

patch_one() {
  local dir="$1"
  echo ""
  echo "🔧 Aplicando em: $dir"

  mkdir -p "$dir/assets/safezones"
  cp /tmp/g_anuncio.png        "$dir/assets/safezones/anuncio.png"
  cp /tmp/g_anuncio_tiktok.png "$dir/assets/safezones/anuncio_tiktok.png"
  cp /tmp/g_anuncio_reels.png  "$dir/assets/safezones/anuncio_reels.png"
  cp /tmp/g_anuncio_shorts.png "$dir/assets/safezones/anuncio_shorts.png"
  echo "   • 4 overlays instalados em assets/safezones/"

  # Botão 'Anúncio Face' (ancorado no botão nativo YouTube Shorts)
  if ! grep -q 'data-platform="anuncio"' "$dir/index.html"; then
    perl -0pi -e 's|(data-platform="shorts">YouTube Shorts<small>9:16</small></button>)|$1\n                            <button class="preset-card" type="button" data-platform="anuncio">Anúncio Face<small>9:16</small></button>|' "$dir/index.html"
  fi
  # Botões TikTok/Reels/Shorts (ancorados no botão Anúncio Face)
  if ! grep -q 'data-platform="anuncio_tiktok"' "$dir/index.html"; then
    perl -0pi -e 's|(data-platform="anuncio">Anúncio Face<small>9:16</small></button>)|$1\n                            <button class="preset-card" type="button" data-platform="anuncio_tiktok">Anúncio TikTok<small>9:16</small></button>\n                            <button class="preset-card" type="button" data-platform="anuncio_reels">Anúncio Reels<small>9:16</small></button>\n                            <button class="preset-card" type="button" data-platform="anuncio_shorts">Anúncio Shorts<small>9:16</small></button>|' "$dir/index.html"
  fi
  local nbtn
  nbtn=$(grep -c 'data-platform="anuncio' "$dir/index.html" || true)
  if [ "$nbtn" -lt 4 ]; then
    echo "   ❌ botões incompletos no index.html ($nbtn/4)"
    return 1
  fi
  echo "   • 4 botões presentes no index.html"

  # Registro das plataformas no main.js
  if ! grep -q 'anuncio: { img: null, loaded: false }' "$dir/js/main.js"; then
    perl -0pi -e 's|(shorts: \{ img: null, loaded: false \})|$1,\n        anuncio: { img: null, loaded: false }|' "$dir/js/main.js"
  fi
  if ! grep -q 'anuncio_tiktok: { img: null, loaded: false }' "$dir/js/main.js"; then
    perl -0pi -e 's|(anuncio: \{ img: null, loaded: false \})|$1,\n        anuncio_tiktok: { img: null, loaded: false },\n        anuncio_reels: { img: null, loaded: false },\n        anuncio_shorts: { img: null, loaded: false }|' "$dir/js/main.js"
  fi
  local nreg
  nreg=$(grep -c 'anuncio.*{ img: null, loaded: false }' "$dir/js/main.js" || true)
  if [ "$nreg" -lt 4 ]; then
    echo "   ❌ plataformas incompletas no main.js ($nreg/4)"
    return 1
  fi
  echo "   • 4 plataformas registradas no main.js"

  echo "   ✅ pronto"
}

PATCHED=0
SEEN=""

try_dir() {
  local dir="$1"
  [ -f "$dir/index.html" ] || return 0
  [ -f "$dir/js/main.js" ] || return 0
  grep -qi "guideify" "$dir/index.html" || return 0
  case "$SEEN" in *"|$dir|"*) return 0;; esac
  SEEN="$SEEN|$dir|"
  if patch_one "$dir"; then PATCHED=$((PATCHED+1)); fi
}

# 1. Extensões CEP instaladas
while IFS= read -r -d '' f; do
  try_dir "$(dirname "$f")"
done < <(find "$HOME/Library/Application Support/Adobe/CEP/extensions" \
              "/Library/Application Support/Adobe/CEP/extensions" \
              -maxdepth 3 -name "index.html" -print0 2>/dev/null)

# 2. Cópias no Downloads (pra não perder os botões se reinstalar)
try_dir "$HOME/Downloads/Guideify Premiere Pro/Guideify-Mac/Guideify"
try_dir "$HOME/Downloads/Guideify Premiere Pro/Guideify - Windows/Manual Install/Guideify"

echo ""
if [ "$PATCHED" -eq 0 ]; then
  echo "❌ Nenhuma instalação do Guideify encontrada para modificar."
  echo "   Instale o Guideify primeiro (install-mac.command) e rode este script de novo."
  exit 1
fi

echo "=========================================================="
echo "✅ $PATCHED cópia(s) do Guideify modificada(s)!"
echo ""
echo "PRÓXIMOS PASSOS:"
echo "  1. Feche o Premiere completamente (Cmd+Q) e abra de novo"
echo "  2. Guideify → Safe zones → Anúncio Face / TikTok / Reels / Shorts"
echo "  3. Clique Apply"
echo "=========================================================="
