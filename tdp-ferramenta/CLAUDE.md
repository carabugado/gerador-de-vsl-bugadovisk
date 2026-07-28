# TDP — Troca de Produto

Ferramenta de linha de comando que acha o produto antigo numa VSL (falado +
texto na tela + visual) e gera o XML do Premiere + o rebrand de voz.

Ambiente: macOS (Apple Silicon), Python 3.12, venv em `.venv`, ffmpeg no PATH.
Rode sempre com a venv ativa (`source .venv/bin/activate`).

## Comandos

```bash
python -m pytest -q                    # 181 testes, < 1 s, sem baixar modelo
python -m troca_produto doctor         # o que está instalado e o que falta
python -m troca_produto --help
```

Fluxo de uma demanda: `init` → `analyze` → (`refs`) → `review` → `rebrand` →
`export` → `qa`. O padrão de entrega é `troca_COMPLETO.xml`: **V1** vídeo
cortado e colorido por tipo (+ marcadores) · **V2** produto novo alinhado ao
mesmo frame · **A1** áudio original · **A2+** vozes por locutor.

## Regras do projeto (foram custosas de descobrir — não reverta)

**Detecção**
- O match das menções faladas é **fuzzy + fonético** (`pipeline/text_detect.py::_fuzzy_score`),
  janelas de 1–3 palavras, limiar ~72. **Nunca** troque por regex ou `in`: o ASR
  entorta o nome ("sugarbind" → "sugar bean", "burntide" → "bern tide").
- Cobertura visual acima de **30% avisa, 40% reprova**. É o sintoma de
  over-detection; a guarda vive em `pipeline/ranges.py::check_coverage`.
- `TDP_VISUAL_ENGINE=cloud` (padrão) **nunca** cai no OpenCV local — em VSL real
  ele marcou ~73% do vídeo de lixo. Sem chave Anthropic, a visual sai só do
  CLIP e **alguém tem que olhar o contact sheet**.
- Faixas do V1 nunca se sobrepõem: cruzamento de tipos vira `mixed`.

**Voz**
- MiniMax: chamadas **sem `?GroupId=`** (com ele vazio a API recusa) e sempre
  conferindo `base_resp.status_code == 0`. Clone é **clonar → gerar → deletar,
  um por vez** (cota de slots, erro 2052); `ephemeral_voice()` deleta no `finally`.
- Narrador → troca só a **palavra**. Depoimento → regenera a **frase inteira**.
- Diarização por **referência do narrador** (`sim >= 0.85`), F0 pro gênero
  (< 165 Hz homem). Sempre no **áudio limpo** (`audio/base.wav`), nunca no
  vídeo com TTS embutido.
- Fallback por gênero, com trava: **narrador jamais recebe voz feminina**.
- Aparar silêncio **só das pontas**. `stop_periods` corta na primeira pausa
  interna e trunca a frase.

**Assets**
- Recorte com `rembg` + `new_session("birefnet-general")`. PNG de produto
  costuma vir sem alpha ("png falso") — `cutout --audit` aponta.
- B-roll com transparência é **ProRes 4444 `.mov`**; MP4 não guarda alpha.
- Ao regenerar, **sobrescreva os mesmos nomes** (não quebra o link no Premiere).
- **`os.listdir`, nunca `glob`**: caminho com colchete (`[GRUPO FENIX]`,
  `BST [ML03] PITCH…`) some no glob, porque colchete é classe de caractere.

## Estilo do código

- Toda dependência pesada (torch, whisper, rembg, resemblyzer, Pillow) é
  importada **dentro da função** que usa. É o que mantém a suíte em < 1 s.
- Construtor de comando ffmpeg é função pura que devolve a lista de args —
  assim dá pra testar sem ffmpeg instalado.
- Comentário explica **por quê**, não o quê. Se um trecho parece estranho,
  provavelmente tem um aprendizado atrás: documente antes de "limpar".
- Mensagens de CLI em português, diretas, dizendo o próximo passo.
