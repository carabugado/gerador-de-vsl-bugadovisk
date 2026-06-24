#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── IA: roteamento híbrido grátis — Groq/Gemini (nuvem, rápido) + Ollama LOCAL ──
# Tarefas de texto (classificador, UGC, diretor, PHOENIX) → Groq-first quando há
# chave; senão Ollama local. Visão/auto-tag → Gemini ou Ollama. Ollama local é a
# reserva offline. Busca de B-roll é sempre por embeddings (CLIP local).
# (OpenRouter foi removido — a cota free não aguentava o volume de uma VSL.)
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
  # Só baixa um modelo de texto se o Ollama NÃO tiver nenhum (não força download
  # quando já existe qualquer modelo instalado).
  if ! ollama list 2>/dev/null | tail -n +2 | grep -q .; then
    echo "⬇️  Ollama sem modelos — baixando $OLLAMA_MODEL como reserva (uma vez)..."
    ollama pull "$OLLAMA_MODEL"
  fi
  if [ "$OLLAMA_PULL_VISION" = "1" ] && ! ollama list 2>/dev/null | grep -q "${OLLAMA_VISION_MODEL%%:*}"; then
    echo "⬇️  Baixando $OLLAMA_VISION_MODEL (uma vez, ~8GB)..."
    ollama pull "$OLLAMA_VISION_MODEL"
  fi
  echo "🧠 Ollama (reserva local) pronto."
else
  echo "ℹ️  Ollama não instalado — sem reserva offline. As tarefas dependem de chave Groq/Gemini."
fi

source "$SCRIPT_DIR/backend/.venv/bin/activate"
cd "$SCRIPT_DIR/backend"
echo "🚀 Servidor VSL iniciando em http://127.0.0.1:7821"
echo "   Deixe esta janela aberta enquanto usa o Premiere."
python server.py
