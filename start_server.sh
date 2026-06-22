#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── IA: Ollama LOCAL (primário, grátis, sem cota) + Gemini (reserva) ───────────
# Tarefas inteligentes (classificador, UGC, visão, auto-tag, PHOENIX) → Ollama local.
# Gemini entra de reserva quando a cota free dele estiver disponível. Busca de
# B-roll é sempre CLIP local. (OpenRouter foi removido — cota free não aguentava.)
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
export OLLAMA_VISION_MODEL="${OLLAMA_VISION_MODEL:-llama3.2-vision:11b}"

if command -v ollama >/dev/null 2>&1; then
  if ! curl -s -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "🧠 Iniciando Ollama..."
    ollama serve > /tmp/ollama.log 2>&1 &
    for i in $(seq 1 15); do
      curl -s -m 2 http://localhost:11434/api/tags >/dev/null 2>&1 && break
      sleep 1
    done
  fi
  # Reserva: só baixa um modelo de texto se o Ollama NÃO tiver nenhum (não força
  # download quando OpenRouter é o primário e já há qualquer modelo instalado).
  if ! ollama list 2>/dev/null | tail -n +2 | grep -q .; then
    echo "⬇️  Ollama sem modelos — baixando $OLLAMA_MODEL como reserva (uma vez)..."
    ollama pull "$OLLAMA_MODEL"
  fi
  if [ "$OLLAMA_PULL_VISION" = "1" ] && ! ollama list 2>/dev/null | grep -q "${OLLAMA_VISION_MODEL%%:*}"; then
    echo "⬇️  Baixando $OLLAMA_VISION_MODEL (uma vez, ~8GB)..."
    ollama pull "$OLLAMA_VISION_MODEL"
  fi
  echo "🧠 Ollama (reserva) pronto."
else
  echo "ℹ️  Ollama não instalado — sem reserva offline (OpenRouter cobre as tarefas)."
fi

source "$SCRIPT_DIR/backend/.venv/bin/activate"
cd "$SCRIPT_DIR/backend"
echo "🚀 Servidor VSL iniciando em http://127.0.0.1:7821"
echo "   Deixe esta janela aberta enquanto usa o Premiere."
python server.py
