#!/bin/bash
set -e

echo "=============================================="
echo " Guideify — Instalador para Premiere Pro (Mac)"
echo "=============================================="

# Pasta de origem: argumento ou padrão do Downloads
SRC="${1:-$HOME/Downloads/Guideify Premiere Pro/Guideify-Mac}"

if [ ! -e "$SRC" ]; then
  FOUND=$(find "$HOME/Downloads" -maxdepth 3 -iname "*guideify*" -print -quit 2>/dev/null || true)
  if [ -n "$FOUND" ]; then
    SRC="$FOUND"
  else
    echo "❌ Não encontrei a pasta do Guideify."
    echo "   Uso: ./install_guideify_mac.sh \"/caminho/para/Guideify-Mac\""
    exit 1
  fi
fi

echo "📁 Origem: $SRC"

echo "[1/4] Removendo bloqueio de quarentena do macOS..."
xattr -cr "$SRC" 2>/dev/null || true

echo "[2/4] Habilitando extensões não assinadas no Premiere..."
for v in 10 11 12; do
  defaults write com.adobe.CSXS.$v PlayerDebugMode 1
done

CEP_ROOT="$HOME/Library/Application Support/Adobe/CEP/extensions"
mkdir -p "$CEP_ROOT"

install_cep_folder() {
  local folder="$1"
  local manifest="$folder/CSXS/manifest.xml"
  local id
  id=$(sed -n 's/.*ExtensionBundleId="\([^"]*\)".*/\1/p' "$manifest" | head -n1)
  [ -z "$id" ] && id="com.guideify.panel"
  local dest="$CEP_ROOT/$id"
  echo "[3/4] Instalando extensão CEP em: $dest"
  rm -rf "$dest"
  mkdir -p "$dest"
  cp -R "$folder/." "$dest/"
  echo "[4/4] ✅ Instalação concluída!"
  echo ""
  echo "PRÓXIMOS PASSOS:"
  echo "  1. Feche o Premiere completamente (Cmd+Q) e abra de novo"
  echo "  2. Menu: Janela → Extensões → Guideify"
  exit 0
}

# Caso 1: a própria pasta já é uma extensão CEP
if [ -f "$SRC/CSXS/manifest.xml" ]; then
  install_cep_folder "$SRC"
fi

# Caso 2: extensão CEP em alguma subpasta
MANIFEST=$(find "$SRC" -maxdepth 4 -path "*/CSXS/manifest.xml" -print -quit 2>/dev/null || true)
if [ -n "$MANIFEST" ]; then
  install_cep_folder "$(dirname "$(dirname "$MANIFEST")")"
fi

# Caso 3: arquivo .zxp (é um zip — extrai direto na pasta de extensões)
ZXP=$(find "$SRC" -maxdepth 4 -iname "*.zxp" -print -quit 2>/dev/null || true)
if [ -n "$ZXP" ]; then
  echo "📦 Encontrei um .zxp: $ZXP"
  TMP=$(mktemp -d)
  unzip -qo "$ZXP" -d "$TMP"
  if [ -f "$TMP/CSXS/manifest.xml" ]; then
    install_cep_folder "$TMP"
  fi
  MANIFEST=$(find "$TMP" -maxdepth 4 -path "*/CSXS/manifest.xml" -print -quit 2>/dev/null || true)
  if [ -n "$MANIFEST" ]; then
    install_cep_folder "$(dirname "$(dirname "$MANIFEST")")"
  fi
  echo "❌ O .zxp não parece conter uma extensão CEP válida."
  exit 1
fi

# Caso 4: instalador .pkg ou .app
PKG=$(find "$SRC" -maxdepth 3 \( -iname "*.pkg" -o -iname "*.app" \) -print -quit 2>/dev/null || true)
if [ -n "$PKG" ]; then
  echo "[3/4] Encontrei um instalador: $PKG"
  echo "[4/4] Abrindo — siga as instruções na tela."
  echo "      (a quarentena já foi removida, o macOS não deve mais bloquear)"
  open "$PKG"
  exit 0
fi

# Caso 5: imagem de disco .dmg
DMG=$(find "$SRC" -maxdepth 3 -iname "*.dmg" -print -quit 2>/dev/null || true)
if [ -n "$DMG" ]; then
  echo "💿 Encontrei uma imagem de disco: $DMG — abrindo..."
  open "$DMG"
  echo "Quando ela montar, rode este script de novo apontando para o volume:"
  echo "  ./install_guideify_mac.sh /Volumes/<nome-do-volume>"
  exit 0
fi

echo "❌ Não reconheci o formato do Guideify nessa pasta."
echo "   Conteúdo encontrado:"
ls -la "$SRC"
echo ""
echo "   Me mande essa lista acima que eu te digo o próximo passo."
exit 1
