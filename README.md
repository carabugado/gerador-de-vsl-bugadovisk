# 🎬 Highlights Cutter

Transforma um episódio longo (1h+) num corte de highlights de ~3 minutos **direto no Premiere Pro**. Você deixa o vídeo na timeline, o painel **mapeia os melhores momentos** com IA (o Gemini *ouvindo* o áudio, ou Whisper local + LLM) e **monta o corte** numa sequência nova — sem sair do Premiere.

<p align="center">
  <img src="docs/flow.svg" alt="Fluxo: vídeo na timeline → análise (áudio→Gemini ou Whisper local) → mapa de clips → corte de ~3min" width="100%">
</p>

---

## ✨ O que ele faz

- **Detecta** o vídeo automaticamente da sua timeline (1º clip da V1).
- **Mapeia** 6–9 momentos fortes e curtos (humor, debate, nerdola, reação, momento, insight), com **score** e na ordem narrativa ideal.
- Respeita a **duração alvo** que você escolhe (1:30 / 3:00 / 5:00 / 10:00 / livre) — com trava de orçamento pra não estourar o tempo.
- **Monta o corte** numa sequência nova (sem mexer na sua timeline original) e adiciona **marcadores** por clip.

Dois modos de análise:
- **🎧 Áudio → Gemini** — renderiza o áudio comprimido (<20 MB) e manda pro Gemini *ouvir* (tom, timing, risada → seleção melhor). Precisa de uma chave Gemini grátis.
- **📝 Local (Whisper)** — transcreve offline com Whisper e escolhe via LLM local (Ollama). Timestamps mais precisos, funciona sem internet.
- **⚡ Auto** — usa o Gemini se houver chave, senão cai pro local.

<p align="center">
  <img src="docs/panel.svg" alt="Print do painel Highlights Cutter no Premiere" width="380">
</p>

---

## 📦 Pré-requisitos

- **macOS** ou **Windows** + **Adobe Premiere Pro 2022 ou mais novo**
- **Python 3** e **ffmpeg** (instruções por SO abaixo, na seção de Instalação)
- **Opcional (modo local / offline):** [Ollama](https://ollama.com) — `ollama pull qwen2.5:7b`
- **Opcional (modo áudio→Gemini):** uma chave grátis em [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)

> ⚠️ A 1ª transcrição com Whisper baixa um modelo (~145 MB) e demora um pouco num vídeo de 1h. As próximas usam cache.

---

## 🚀 Instalação

### 🍎 macOS

```bash
# pré-requisitos
brew install python ffmpeg          # (opcional: brew install ollama)

# 1. Clone o projeto
git clone https://github.com/carabugado/highlights-cutter.git
cd highlights-cutter

# 2. Cria o ambiente Python e instala as dependências do backend
./install_mac.sh

# 3. Instala o painel Highlights Cutter no Premiere
./install_highlights_mac.sh
```

### 🪟 Windows

Pré-requisitos: **Python 3** (no instalador, marque **"Add python.exe to PATH"**) e **ffmpeg** no PATH:

```powershell
winget install Gyan.FFmpeg     # depois REABRA o terminal
# (opcional) Ollama: baixe em https://ollama.com e rode: ollama pull qwen2.5:7b
```

Depois, na pasta do projeto:

```powershell
# 1. Clone o projeto
git clone https://github.com/carabugado/highlights-cutter.git
cd highlights-cutter

# 2. Instala tudo: venv + dependências, debug do CEP (registro) e o painel
powershell -ExecutionPolicy Bypass -File .\install_highlights_win.ps1
```

> Ambos os instaladores ligam o **modo debug do CEP** (necessário pra extensão não-assinada carregar). Depois de instalar, **reinicie o Premiere**.

---

## ▶️ Como usar

1. **Ligue o backend** (deixe esta janela aberta enquanto usa o Premiere):
   ```bash
   ./start_server.sh       # macOS
   start_server.bat        # Windows
   ```
   Sobe em `http://127.0.0.1:7821`.

2. No Premiere, com o **vídeo de 1h na timeline**, abra o painel:
   **Janela → Extensões → Highlights Cutter**

3. (Opcional, modo áudio) clique em **⚙ Chave Gemini**, cole a chave e salve.

4. Confira a **origem** (detectada da timeline) → escolha a **duração** e os **tipos** → **🔍 Mapear melhores momentos**.

5. Revise os clips (pode desligar os que não quiser) → **✂️ Cortar em nova sequência**.

Pronto: uma sequência nova com o corte montado e marcadores em cada clip.

---

## 🛠️ Ferramenta extra: transcrição via terminal

Gera um `.srt` de qualquer vídeo/áudio com o Whisper local (salva ao lado do vídeo):

```bash
./transcribe_video.sh "/caminho/do/episodio.mp4"    # macOS
transcribe_video.bat "C:\caminho\episodio.mp4"      # Windows
```

---

## 🩺 Solução de problemas

| Sintoma | O que fazer |
|---|---|
| Painel diz **"backend offline"** | Rode `./start_server.sh` (macOS) ou `start_server.bat` (Windows) e deixe aberto. |
| **"Erro ao mapear: Not Found"** | O servidor está numa versão antiga — pare (Ctrl+C) e ligue de novo. |
| Painel **não aparece** no menu Extensões | Rode o instalador do seu SO e **reinicie o Premiere**. No Windows, confirme que o debug do CEP foi ligado (registro). |
| **ffmpeg não encontrado** (Windows) | `winget install Gyan.FFmpeg` e **reabra o terminal**. |
| **Sem chave Gemini** | Use o modo **📝 Local** com o Ollama rodando (`ollama serve`). |
| Modo áudio **falha/limite (429)** | A chave bateu a cota — use outra chave, ou o modo **📝 Local**. |
| Cortes no **tempo errado** | No modo 🎧 áudio os timestamps são estimados de ouvido; use **📝 Local** (Whisper) pra tempo cravado. |

> Diagnóstico: o backend loga cada etapa no terminal do `start_server.sh` (qual engine, tamanho do áudio, resgate de resposta etc.).

---

## 🗂️ Estrutura

```
backend/                 # servidor FastAPI (porta 7821)
  highlights.py          # mapeamento dos melhores momentos (áudio/Gemini ou transcrição)
  transcribe.py          # Whisper local (+ CLI de SRT)
  llm.py                 # IA: Ollama local → Gemini → Anthropic
  server.py              # rotas (inclui /highlights)
cep-highlights/          # painel CEP "Highlights Cutter" do Premiere
install_highlights_mac.sh / install_highlights_win.ps1   # instaladores (Mac / Windows)
install_mac.sh
start_server.sh / start_server.bat
transcribe_video.sh / transcribe_video.bat
```

> ℹ️ Este repositório também inclui o **VSL B-Roll Generator** (painel `cep/` + módulos `broll_*` no backend), que **compartilha o mesmo backend** do Highlights Cutter. Por isso o backend é único.
