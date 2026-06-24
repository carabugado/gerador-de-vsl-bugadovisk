# VSL B-Roll Generator — Instalador Windows
# Execute como: powershell -ExecutionPolicy Bypass -File install_win.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "======================================" -ForegroundColor Cyan
Write-Host " VSL B-Roll Generator — Instalacao Windows" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan

# 1. Habilita extensoes CEP nao assinadas no Premiere
Write-Host "`n[1/4] Habilitando modo debug CEP..." -ForegroundColor Yellow
reg add "HKCU\Software\Adobe\CSXS.12" /v PlayerDebugMode /t REG_SZ /d 1 /f | Out-Null
reg add "HKCU\Software\Adobe\CSXS.11" /v PlayerDebugMode /t REG_SZ /d 1 /f | Out-Null
reg add "HKCU\Software\Adobe\CSXS.10" /v PlayerDebugMode /t REG_SZ /d 1 /f | Out-Null

# 2. Instala a extensao CEP
$CEPDir = "$env:APPDATA\Adobe\CEP\extensions\com.vsl.brollgenerator"
Write-Host "[2/4] Instalando extensao CEP em: $CEPDir" -ForegroundColor Yellow

if (Test-Path $CEPDir) { Remove-Item $CEPDir -Recurse -Force }
New-Item -ItemType Directory -Path $CEPDir -Force | Out-Null
Copy-Item "$ScriptDir\cep\*" $CEPDir -Recurse -Force

# 3. Cria ambiente Python
Write-Host "[3/4] Configurando ambiente Python..." -ForegroundColor Yellow

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "ERRO: Python nao encontrado. Instale em https://python.org (marque 'Add to PATH')" -ForegroundColor Red
    exit 1
}

$BackendDir = "$ScriptDir\backend"
Set-Location $BackendDir

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& ".venv\Scripts\pip" install --upgrade pip -q
& ".venv\Scripts\pip" install -r requirements.txt -q

# Verifica ffmpeg
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "AVISO: ffmpeg nao encontrado." -ForegroundColor Yellow
    Write-Host "  Instale com: winget install ffmpeg" -ForegroundColor Yellow
    Write-Host "  Ou baixe em: https://ffmpeg.org/download.html" -ForegroundColor Yellow
}

# 4. Cria script de start
Write-Host "[4/4] Criando start_server.bat..." -ForegroundColor Yellow
$batContent = @"
@echo off
cd /d "%~dp0backend"
call .venv\Scripts\activate
echo Servidor VSL iniciando em http://127.0.0.1:7821
echo Deixe esta janela aberta enquanto usa o Premiere.
python server.py
pause
"@
Set-Content "$ScriptDir\start_server.bat" $batContent

Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host " Instalacao concluida!" -ForegroundColor Green
Write-Host ""
Write-Host "PROXIMOS PASSOS:" -ForegroundColor White
Write-Host "  1. Execute: start_server.bat" -ForegroundColor White
Write-Host "  2. Abra o Premiere Pro" -ForegroundColor White
Write-Host "  3. Menu: Window -> Extensions -> VSL B-Roll Generator" -ForegroundColor White
Write-Host "======================================" -ForegroundColor Green
