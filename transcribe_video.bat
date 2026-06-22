@echo off
REM Gera a transcricao (.srt) de um video/audio com Whisper local.
REM Uso:
REM   transcribe_video.bat "C:\caminho\episodio.mp4"
REM   transcribe_video.bat "C:\caminho\episodio.mp4" saida.srt
setlocal
set "ROOT=%~dp0"

if "%~1"=="" (
  echo Uso: transcribe_video.bat "C:\caminho\video.mp4" [saida.srt]
  exit /b 1
)
if not exist "%ROOT%backend\.venv\Scripts\python.exe" (
  echo ERRO: rode primeiro install_highlights_win.ps1
  exit /b 1
)

cd /d "%ROOT%backend"
if "%~2"=="" (
  "%ROOT%backend\.venv\Scripts\python.exe" -m transcribe "%~1"
) else (
  "%ROOT%backend\.venv\Scripts\python.exe" -m transcribe "%~1" -o "%~2"
)
