@echo off
REM Liga o backend do Highlights Cutter (http://127.0.0.1:7821).
REM Deixe esta janela aberta enquanto usa o Premiere.
setlocal
set "ROOT=%~dp0"

if not exist "%ROOT%backend\.venv\Scripts\python.exe" (
  echo ERRO: ambiente Python nao encontrado.
  echo Rode primeiro:  powershell -ExecutionPolicy Bypass -File .\install_highlights_win.ps1
  pause
  exit /b 1
)

REM (Opcional) inicia o Ollama local, se estiver instalado
where ollama >nul 2>nul && start "Ollama" /b ollama serve

cd /d "%ROOT%backend"
echo Servidor iniciando em http://127.0.0.1:7821
echo Deixe esta janela aberta enquanto usa o Premiere.
"%ROOT%backend\.venv\Scripts\python.exe" server.py
