# Continuar a demanda localmente (BurnTide → Redutide)

Este arquivo é o handoff: a ferramenta foi construída numa sessão do Claude
Code **na web** (container na nuvem, sem acesso aos arquivos do Mac). Daqui em
diante o processo roda **local**, onde o vídeo e as artes estão.

---

## 1. Instalar o Claude Code no Mac (uma vez)

```bash
curl -fsSL https://claude.ai/install.sh | bash     # instalador nativo (auto-atualiza)
# ou:  brew install --cask claude-code
claude --version
```

## 2. Trazer o projeto pro Mac (uma vez)

```bash
git clone -b claude/tdp-ferramenta-setup-ha1x3e \
  https://github.com/carabugado/gerador-de-vsl-bugadovisk.git ~/tdp
cd ~/tdp/tdp-ferramenta
./install_mac.sh
source .venv/bin/activate
cp .env.example .env        # preencha MINIMAX_API_KEY
python -m troca_produto doctor
```

## 3. Abrir a sessão local

```bash
cd ~/tdp/tdp-ferramenta
claude
```

O `CLAUDE.md` desta pasta é lido sozinho na abertura — a sessão local já começa
sabendo as regras do projeto (fuzzy em vez de regex, cobertura 30/40%, MiniMax
sem GroupId, narrador nunca com voz feminina, `os.listdir` em vez de glob…).

---

## Estado da demanda

| | |
|---|---|
| produto antigo | **BurnTide** (aliases: "burn tide", "burnt tide") |
| produto novo | **Redutide** |
| vídeo base | `/Users/rene/Downloads/troca/BST [ML03] PITCH 37_48_00.mp4` (37:48) |
| arte do produto novo | `/Users/rene/Downloads/troca/1_a.png` → `new_assets` |
| fotos do produto antigo | **ainda não existem** — saem do próprio vídeo, pelo passo 2 |
| swap_kind | `same_format` (mudar pra `format_change` se o formato mudar) |
| pasta de trabalho | `tdp_burntide/` |
| script | `demandas/burntide_para_redutide.sh` |

Nada foi executado ainda: nenhum passo rodou, porque a sessão da nuvem não
enxerga os arquivos do Mac.

## Passos

```bash
./demandas/burntide_para_redutide.sh 1   # menções faladas de BurnTide
./demandas/burntide_para_redutide.sh 2   # frames candidatos a foto do BurnTide
#   ↑ parada manual: abrir tdp_burntide/refs/contact_sheet.jpg e copiar
#     3–5 frames que mostram o BurnTide para tdp_burntide/refs_escolhidos/
./demandas/burntide_para_redutide.sh 3   # análise completa (fala + visual)
./demandas/burntide_para_redutide.sh 4   # revisão + contact sheet + artes
./demandas/burntide_para_redutide.sh 5   # plano de voz   (5 run = gera no MiniMax)
./demandas/burntide_para_redutide.sh 6   # troca_COMPLETO.xml
./demandas/burntide_para_redutide.sh 7   # QA do vídeo final
```

## Onde prestar atenção

- **Passo 1** demora: Whisper `large-v3` num vídeo de 37 min. Não é travamento.
- **Passo 3** extrai ~2.300 frames (1 por segundo) e roda CLIP em todos.
- **Cobertura**: se o visual passar de 30–40%, é over-detection. Suba o limiar:
  `--visual-threshold 0.84 --force`.
- **"burn" aparece o tempo todo** numa VSL de emagrecimento. O matcher já foi
  conferido: pega `burntide`/`burn tide`/`bern tide`/`burn tied` e ignora
  `burn`, `fat burn`, `burn fat fast`. Se escapar algo, ajuste o limiar ou as
  `stop_phrases` de `pipeline/text_detect.py`.
- **MINIMAX_API_KEY** só é necessária no passo 5 com `run`.

## Primeira mensagem pra colar na sessão local

> Estou continuando a demanda BurnTide → Redutide desta ferramenta. Leia
> `demandas/CONTINUAR_LOCAL.md` e `demandas/burntide_para_redutide.sh`.
> Confira o ambiente com `python -m troca_produto doctor`, depois rode o passo 1
> e me mostre as menções faladas encontradas, com timecode. Não passe do passo 2
> sem eu escolher os frames do BurnTide.
