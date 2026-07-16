#!/bin/bash
set -e

PNG_URL="https://raw.githubusercontent.com/carabugado/gerador-de-vsl-bugadovisk/claude/guideify-premiere-install-l748r6/docs/Guideify_AnuncioFace_9x16.png"

echo "===================================================="
echo " Guideify — Adicionar botão nativo 'Anúncio Face'"
echo "===================================================="

TMP_PNG="/tmp/guideify_anuncio.png"
echo "⬇️  Baixando overlay do anúncio..."
curl -fsSL "$PNG_URL" -o "$TMP_PNG"

patch_one() {
  local dir="$1"
  echo ""
  echo "🔧 Aplicando em: $dir"

  mkdir -p "$dir/assets/safezones"
  cp "$TMP_PNG" "$dir/assets/safezones/anuncio.png"
  echo "   • assets/safezones/anuncio.png instalado"

  if grep -q 'data-platform="anuncio"' "$dir/index.html"; then
    echo "   • botão já existia no index.html"
  else
    perl -0pi -e 's|(data-platform="shorts">YouTube Shorts<small>9:16</small></button>)|$1\n                            <button class="preset-card" type="button" data-platform="anuncio">Anúncio Face<small>9:16</small></button>|' "$dir/index.html"
    if grep -q 'data-platform="anuncio"' "$dir/index.html"; then
      echo "   • botão adicionado ao index.html"
    else
      echo "   ❌ não consegui inserir o botão no index.html"
      return 1
    fi
  fi

  if grep -q 'anuncio: { img: null, loaded: false }' "$dir/js/main.js"; then
    echo "   • plataforma já registrada no main.js"
  else
    perl -0pi -e 's|(shorts: \{ img: null, loaded: false \})|$1,\n        anuncio: { img: null, loaded: false }|' "$dir/js/main.js"
    if grep -q 'anuncio: { img: null, loaded: false }' "$dir/js/main.js"; then
      echo "   • plataforma registrada no main.js"
    else
      echo "   ❌ não consegui registrar a plataforma no main.js"
      return 1
    fi
  fi

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

# 2. Cópias no Downloads (pra não perder o botão se reinstalar)
try_dir "$HOME/Downloads/Guideify Premiere Pro/Guideify-Mac/Guideify"
try_dir "$HOME/Downloads/Guideify Premiere Pro/Guideify - Windows/Manual Install/Guideify"

echo ""
if [ "$PATCHED" -eq 0 ]; then
  echo "❌ Nenhuma instalação do Guideify encontrada para modificar."
  echo "   Instale o Guideify primeiro (install-mac.command) e rode este script de novo."
  exit 1
fi

echo "===================================================="
echo "✅ $PATCHED cópia(s) do Guideify modificada(s)!"
echo ""
echo "PRÓXIMOS PASSOS:"
echo "  1. Feche o Premiere completamente (Cmd+Q) e abra de novo"
echo "  2. Guideify → aba Safe zones → botão 'Anúncio Face 9:16'"
echo "  3. Clique Apply"
echo "===================================================="
