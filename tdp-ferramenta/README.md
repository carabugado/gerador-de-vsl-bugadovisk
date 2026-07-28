# TDP — Troca de Produto

Ferramenta de linha de comando que pega uma **VSL longa**, acha **todas as aparições do produto antigo** (falado + texto na tela + visual) e entrega:

- `troca_COMPLETO.xml` pro Premiere — **V1** cortado e colorido por tipo (+ marcadores), **V2** produto novo alinhado ao mesmo frame, **A1** áudio original, **A2+** clipes de voz por locutor;
- o **rebrand de voz** (trocar o nome falado) com clone da própria fala de cada locutor;
- um **QA do vídeo final** que caça sobras do produto antigo e falha com exit code 2.

---

## 1. Instalação (macOS, Apple Silicon)

Pré-requisito: `ffmpeg` e `ffprobe` no PATH.

```bash
brew install ffmpeg

cd tdp-ferramenta
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip

# ferramenta + Whisper + CLIP(torch) + yt-dlp
pip install -e ".[dev,url,clip,asr]"

# recorte de produto + diarização
pip install "rembg[cpu]" resemblyzer scikit-learn "setuptools<81"

python -m pytest -q          # a suíte inteira roda sem modelo nenhum baixado
python -m troca_produto doctor
```

Ou, de uma vez: `./install_mac.sh`.

**`setuptools<81` é obrigatório.** O `resemblyzer` puxa `webrtcvad`, que importa `pkg_resources` — removido no setuptools 81+. Sem o pin, a diarização quebra na importação.

No primeiro uso, `rembg` (modelo **birefnet-general**) e o CLIP baixam modelos. É normal e acontece uma vez só.

`python -m troca_produto doctor` mostra, em uma tela, o que está instalado, o que falta e o comando exato pra instalar cada coisa.

---

## 2. Chaves (`.env` na raiz)

Copie `.env.example` para `.env`:

```
MINIMAX_API_KEY=...
ANTHROPIC_API_KEY=...      # opcional
TDP_VISUAL_ENGINE=cloud
TDP_HOTWORDS=1
```

### MiniMax (clone de voz + TTS)

Dois detalhes que custaram caro e estão codificados na ferramenta:

1. **A chave funciona OMITINDO o GroupId.** `POST https://api.minimax.io/v1/t2a_v2` vai **sem** `?GroupId=` — com o parâmetro vazio a API recusa. `minimax.endpoint()` nunca monta query string.
2. **Voice clone tem cota de slots.** Se aparecer `2052 insufficient voice slot`, o padrão é **clonar → gerar → deletar uma voz por vez** (`/v1/voice_clone` → `/v1/t2a_v2` → `/v1/delete_voice`). Nunca acumule. `MinimaxClient.ephemeral_voice()` deleta no `finally`, mesmo quando a geração falha.

E sempre confira `base_resp.status_code == 0`: a API responde HTTP 200 com o erro dentro do corpo. `check_base_resp()` faz isso em toda chamada.

### Anthropic (opcional)

Sem crédito, rode a detecção visual com `TDP_VISUAL_ENGINE=cloud`. Isso **não** usa OpenCV local (que, em VSL real, marcou ~73% do vídeo de lixo) — a visual vem só do **CLIP**, que é preciso.

E **confira os frames do CLIP você mesmo**: `review --sheet` monta um contact sheet (grade de miniaturas com timecode). Nunca aceite a saída visual sem olhar.

---

## 3. Fluxo de uma demanda

```bash
# 1. briefing
python -m troca_produto init --out briefing.json
#    edite: old_product_name, new_product_name, aliases,
#           old_assets (imagens do produto ANTIGO — essenciais pro CLIP),
#           new_assets (produto novo),
#           swap_kind (format_change se muda gotas → cápsula),
#           swap_target: both

# 2. análise (hotwords enviesa o Whisper pelo nome antigo → grafa certo)
TDP_VISUAL_ENGINE=cloud TDP_HOTWORDS=1 \
  python -m troca_produto analyze --briefing briefing.json --dir tdp_projeto

# 2b. NÃO TEM FOTO DO PRODUTO ANTIGO?
#     rode o passo 2 com swap_target: audio, e tire as referências do próprio vídeo:
python -m troca_produto refs --dir tdp_projeto
#     → frames em volta de cada menção falada + contact sheet.
#       Escolha 3–5 que mostram o produto antigo, aponte em old_assets e
#       rode o analyze de novo com swap_target: both.

# 3. CONFIRA A COBERTURA
#    se o visual cobrir >30–40% do vídeo, está errado (over-detection).
#    A ferramenta avisa sozinha; refaça com CLIP-only e confira no olho:
python -m troca_produto review --dir tdp_projeto --sheet

# 4. revisão e escolha das artes
python -m troca_produto review --dir tdp_projeto --drop 4,9-11 --auto-assets

# 5. rebrand de voz (plano; --run gera o áudio no MiniMax)
python -m troca_produto rebrand --dir tdp_projeto
python -m troca_produto rebrand --dir tdp_projeto --run

# 6. entrega
python -m troca_produto export --dir tdp_projeto
#    → tdp_projeto/export/troca_COMPLETO.xml  ← o principal
#    → tdp_projeto/export/troca.csv
#    → tdp_projeto/export/report.md

# 7. QA no vídeo FINAL editado (exit 2 = achou sobra)
python -m troca_produto qa --video final.mp4 --dir tdp_projeto
```

---

## 4. Rebrand de áudio — as regras de ouro

- **Nunca liste as menções com regex fixo.** O ASR entorta o nome: `sugarbind` vira "sugar bean", "sugarbent"; `glicovita` vira "glykavit". O match é **fuzzy + fonético** (`troca_produto.pipeline.text_detect._fuzzy_score`), em janelas de 1–3 palavras, limiar ~72. Depois disso, os falsos "blood sugar X" são descartados pelo contexto.
- **Narrador → troca só a PALAVRA** (preserva a locução real). **Depoimento → regenera a FRASE INTEIRA** na voz do locutor: soa natural e não precisa esticar áudio.
- **Diarização por REFERÊNCIA DO NARRADOR** (embedding com `resemblyzer`): o centroide vem de vários trechos da seção de oferta; cada menção é medida contra ele. `sim >= 0.85` → narrador; `< 0.85` → locutor distinto (mesmo sendo outro homem). O pitch (F0) valida o gênero: homem < 165 Hz, mulher >= 165 Hz.
- **Clone da PRÓPRIA fala** de cada locutor, expandindo o trecho não-narrador ao redor. Pra voz feminina, a ferramenta concatena vários trechos limpos só dela (~13–20 s) e clona disso — fica bem melhor que clonar 3 segundos.
- **Fallback seguro por gênero**: se o clone falha, mulher cai em **outra voz feminina** já clonada (ou na stock `Wise_Woman`) e homem cai em `Deep_Voice_Man`. **Jamais voz feminina no narrador** — é uma trava dura em `pick_voice()`.
- **Aparar silêncio só das pontas.** `silenceremove` com `stop_periods` corta na primeira pausa interna e trunca a frase. Aqui o silêncio das bordas é medido com `silencedetect` e cortado com `-ss/-to`; o encaixe no tempo do trecho é `atempo`, limitado a 1.35× (acima disso fica robótico).
- **Diarize sempre no áudio limpo** (`tdp_projeto/audio/base.wav`), nunca no vídeo que já tem TTS embutido — isso contamina o centroide.

---

## 5. Recorte de produto (PNG transparente / b-roll)

- PNG de produto quase sempre vem RGB **sem alpha** — o "png falso", que aparece com caixa de fundo no overlay. `cutout --audit` aponta todos antes de virar problema.
- O recorte usa `rembg` com `new_session("birefnet-general")` — muito melhor que o isnet padrão em frasco e rótulo.
- B-roll com transparência precisa ser **ProRes 4444 `.mov`** (`-c:v prores_ks -profile:v 4444 -pix_fmt yuva444p10le`). MP4 não guarda alpha; `prores4444_cmd()` recusa `.mp4`.
- Ao regenerar, **sobrescreva os mesmos nomes** pra não quebrar o link no Premiere (é o padrão do `cutout` sem `--out`).
- Caminho com colchete tipo `[GRUPO FENIX]` **quebra `glob.glob`** (colchete é classe de caractere). A ferramenta usa `os.listdir` em todo lugar.

```bash
python -m troca_produto cutout --input "assets/[GRUPO FENIX]" --audit
python -m troca_produto cutout --input "assets/[GRUPO FENIX]"
```

---

## 6. Padrão de entrega

`troca_COMPLETO.xml`:

| trilha | conteúdo |
|---|---|
| **V1** | vídeo cortado, **colorido por tipo** (falado = Rose, texto na tela = Cerulean, visual = Mango, misto = Violet) + um marcador por aparição |
| **V2** | produto novo posicionado, alinhado ao **mesmo frame** do clipe do V1 |
| **A1** | áudio original |
| **A2+** | clipes de voz visíveis (mp3 mono, uma faixa por locutor quando dá) |

As faixas do V1 nunca se sobrepõem: onde falado e visual se cruzam, vira um clipe `misto`.

A **quantidade falada** ("6 frascos", "free +3", "3 1") é casada com o pack certo do produto novo pelo `quantity_map` do briefing (`review --auto-assets`).

---

## 7. Mapa do código

```
src/troca_produto/
  cli.py                  init · doctor · analyze · refs · review · rebrand · export · cutout · qa
  briefing.py             briefing da demanda + validação
  project.py              layout de tdp_projeto/ e o state.json
  review.py               revisão humana das aparições
  config.py               .env e TDP_*
  media/                  ffmpeg, áudio (aparo/atempo), frames, contact sheet, wav
  pipeline/
    transcribe.py         Whisper + hotwords
    text_detect.py        _fuzzy_score, fonética, janelas 1–3, falsos positivos
    ocr_detect.py         nome ESCRITO na tela
    visual_detect.py      CLIP + política de engine (nunca OpenCV no cloud)
    ranges.py             faixas + guarda de cobertura (30% avisa, 40% reprova)
    analyze.py            orquestra tudo
    qa.py                 sobras no vídeo final (exit 2)
  voice/
    diarize.py            centroide do narrador, sim 0.85, F0/gênero
    minimax.py            clone → gera → deleta, sem GroupId
    rebrand.py            palavra vs frase, escolha de voz, fallback por gênero
    runner.py             executa o rebrand ponta a ponta
  assets/
    cutout.py             rembg birefnet-general, ProRes 4444, os.listdir
    packs.py              quantidade falada → pack certo
  export/
    premiere_xml.py       troca_COMPLETO.xml (V1/V2/A1/A2+)
    reports.py            CSV e report.md
```

Toda dependência pesada (torch, whisper, rembg, resemblyzer, Pillow) é importada **tarde**, dentro da função que usa. Por isso `pytest` roda em menos de um segundo, sem baixar modelo nenhum.
