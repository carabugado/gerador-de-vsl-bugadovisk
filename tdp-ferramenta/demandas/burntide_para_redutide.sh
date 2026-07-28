#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# DEMANDA: BurnTide → Redutide
# Rode NO SEU MAC, de dentro da pasta tdp-ferramenta/, com a venv ativa.
#
#   ./demandas/burntide_para_redutide.sh <passo>
#
#   1 audio    acha as menções FALADAS (não precisa de foto do burntide)
#   2 refs     extrai frames candidatos → você escolhe as fotos do burntide
#   3 visual   análise completa (fala + visual) com os old_assets escolhidos
#   4 revisar  tabela + contact sheet + escolha das artes
#   5 voz      plano do rebrand   (use: 5 run  → gera o áudio no MiniMax)
#   6 entrega  troca_COMPLETO.xml + CSV + report
#   7 qa       caça sobras no vídeo FINAL editado (exit 2 = achou)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

# ── configuração da demanda ──────────────────────────────────────────────────
VIDEO="/Users/rene/Downloads/troca/BST [ML03] PITCH 37_48_00.mp4"
PNG_ENVIADO="/Users/rene/Downloads/troca/1_a.png"

# O 1_a.png é o produto NOVO (Redutide) ou o ANTIGO (burntide)?
#   novo   → vira new_assets (a arte que entra na V2)
#   antigo → vira old_assets (a referência que o CLIP procura)
PAPEL_DO_PNG="novo"

PRODUTO_ANTIGO="BurnTide"
PRODUTO_NOVO="Redutide"

# same_format  → mesmo formato (frasco → frasco)
# format_change → muda o formato (ex.: gotas → cápsula); b-roll antigo não serve
SWAP_KIND="same_format"

PROJETO="tdp_burntide"
BRIEFING="demandas/briefing_burntide.json"
FINAL="${FINAL:-/Users/rene/Downloads/troca/final.mp4}"

export TDP_VISUAL_ENGINE=cloud
export TDP_HOTWORDS=1

PASSO="${1:-}"
MODO="${2:-}"

# ── helpers ──────────────────────────────────────────────────────────────────
escrever_briefing() {  # $1 = swap_target ("audio" ou "both")
  local alvo="$1"
  VIDEO="$VIDEO" PNG_ENVIADO="$PNG_ENVIADO" PAPEL_DO_PNG="$PAPEL_DO_PNG" \
  PRODUTO_ANTIGO="$PRODUTO_ANTIGO" PRODUTO_NOVO="$PRODUTO_NOVO" \
  SWAP_KIND="$SWAP_KIND" ALVO="$alvo" BRIEFING="$BRIEFING" PROJETO="$PROJETO" \
  python - <<'PY'
import json, os
from pathlib import Path

from troca_produto.assets.cutout import list_assets
from troca_produto.briefing import Briefing

papel = os.environ["PAPEL_DO_PNG"]
png = os.environ["PNG_ENVIADO"]
alvo = os.environ["ALVO"]
projeto = Path(os.environ["PROJETO"])
destino = Path(os.environ["BRIEFING"])

# old_assets: o que você escolheu na pasta refs/ (passo 2) — os.listdir, nunca
# glob, porque o caminho do vídeo tem colchete.
escolhidos = list_assets(projeto / "refs_escolhidos")
old_assets = escolhidos or ([png] if papel == "antigo" else [])
new_assets = [png] if papel == "novo" else []

brief = Briefing(
    video=os.environ["VIDEO"],
    old_product_name=os.environ["PRODUTO_ANTIGO"],
    new_product_name=os.environ["PRODUTO_NOVO"],
    # o match é fuzzy+fonético, então isto aqui é só pra enviesar o Whisper
    old_aliases=["burn tide", "burnt tide"],
    new_aliases=["redu tide"],
    old_assets=old_assets,
    new_assets=new_assets,
    swap_kind=os.environ["SWAP_KIND"],
    swap_target=alvo,
    language="en",
)
brief.save(destino)

problemas = brief.validate()
print(f"briefing: {destino}  (swap_target={alvo})")
print(f"  old_assets: {len(old_assets)}  ·  new_assets: {len(new_assets)}")
for item in problemas:
    print(f"  ⚠ {item}")
PY
}

case "$PASSO" in

1)
  echo "▸ passo 1 — menções FALADAS de $PRODUTO_ANTIGO"
  echo "  (só áudio: ainda não precisamos de foto do produto antigo)"
  escrever_briefing audio
  python -m troca_produto analyze --briefing "$BRIEFING" --dir "$PROJETO"
  echo
  echo "próximo:  $0 2"
  ;;

2)
  echo "▸ passo 2 — frames candidatos a foto do $PRODUTO_ANTIGO"
  python -m troca_produto refs --dir "$PROJETO"
  mkdir -p "$PROJETO/refs_escolhidos"
  echo
  echo "AGORA, na mão:"
  echo "  1. abra  $PROJETO/refs/contact_sheet.jpg"
  echo "  2. copie pra $PROJETO/refs_escolhidos/ os 3–5 frames que mostram o $PRODUTO_ANTIGO"
  echo "     (frente do rótulo, bem visível; ângulos diferentes ajudam)"
  echo "  3. rode:  $0 3"
  ;;

3)
  echo "▸ passo 3 — análise completa (fala + visual)"
  escrever_briefing both
  python -m troca_produto analyze --briefing "$BRIEFING" --dir "$PROJETO" --force
  echo
  echo "CONFIRA A COBERTURA acima. Se o visual passar de 30–40%, é over-detection:"
  echo "  suba o limiar e refaça →  python -m troca_produto analyze --briefing $BRIEFING --dir $PROJETO --visual-threshold 0.84 --force"
  echo
  echo "próximo:  $0 4"
  ;;

4)
  echo "▸ passo 4 — revisão"
  python -m troca_produto review --dir "$PROJETO" --sheet --auto-assets
  echo
  echo "abra $PROJETO/export/contact_sheet.jpg e confira NO OLHO."
  echo "derrube o que estiver errado:  python -m troca_produto review --dir $PROJETO --drop 4,9-11"
  echo "próximo:  $0 5"
  ;;

5)
  echo "▸ passo 5 — rebrand de voz"
  if [ "$MODO" = "run" ]; then
    echo "  gerando áudio no MiniMax (clone → gera → deleta, uma voz por vez)"
    python -m troca_produto rebrand --dir "$PROJETO" --run
  else
    python -m troca_produto rebrand --dir "$PROJETO"
    echo
    echo "confira o plano acima (quem é narrador, quem é depoimento, qual voz)."
    echo "pra gerar de verdade:  $0 5 run"
  fi
  ;;

6)
  echo "▸ passo 6 — entrega"
  python -m troca_produto export --dir "$PROJETO"
  echo
  echo "abra no Premiere:  $PROJETO/export/troca_COMPLETO.xml"
  ;;

7)
  echo "▸ passo 7 — QA do vídeo final"
  python -m troca_produto qa --video "$FINAL" --dir "$PROJETO" || {
    echo "⚠ exit 2 = ainda tem sobra do $PRODUTO_ANTIGO. Corrija e rode de novo."
    exit 2
  }
  ;;

*)
  sed -n '2,17p' "$0"
  exit 1
  ;;
esac
