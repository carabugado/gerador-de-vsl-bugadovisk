# Highlights Cutter — Instalacao Windows (PowerShell)
# Rode na pasta do projeto:
#   powershell -ExecutionPolicy Bypass -File .\install_highlights_win.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host "======================================"
Write-Host " Highlights Cutter - Instalacao Windows"
Write-Host "======================================"

# 1. Habilita o modo debug do CEP (extensoes nao-assinadas) via registro
Write-Host "[1/4] Habilitando modo debug do CEP..."
foreach ($v in 10, 11, 12) {
    $key = "HKCU:\Software\Adobe\CSXS.$v"
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -Path $key -Name "PlayerDebugMode" -Value "1" -PropertyType String -Force | Out-Null
}

# 2. Instala a extensao CEP em %APPDATA%\Adobe\CEP\extensions
$cepDir = Join-Path $env:APPDATA "Adobe\CEP\extensions\com.highlights.cutter"
Write-Host "[2/4] Instalando a extensao em: $cepDir"
if (Test-Path $cepDir) { Remove-Item $cepDir -Recurse -Force }
New-Item -ItemType Directory -Path $cepDir -Force | Out-Null
Copy-Item -Path (Join-Path $root "cep-highlights\*") -Destination $cepDir -Recurse -Force

# 3. Ambiente Python + dependencias
Write-Host "[3/4] Configurando o Python (pode demorar — baixa o Whisper/torch)..."
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "ERRO: Python 3 nao encontrado. Instale em https://www.python.org/downloads/"
    Write-Host "      (marque 'Add python.exe to PATH' no instalador) e rode de novo."
    exit 1
}
Push-Location (Join-Path $root "backend")
if (-not (Test-Path ".venv")) { python -m venv .venv }
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip -q
& ".\.venv\Scripts\pip.exe" install -r requirements.txt
Pop-Location

# 4. Checa o ffmpeg
Write-Host "[4/4] Verificando o ffmpeg..."
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "  AVISO: ffmpeg nao encontrado no PATH."
    Write-Host "  Instale com:  winget install Gyan.FFmpeg   (ou: choco install ffmpeg)"
    Write-Host "  e REABRA o terminal antes de usar."
}

Write-Host ""
Write-Host "======================================"
Write-Host "Instalacao concluida!"
Write-Host ""
Write-Host "PROXIMOS PASSOS:"
Write-Host "  1. Ligue o backend:  .\start_server.bat   (deixe a janela aberta)"
Write-Host "  2. REINICIE o Premiere"
Write-Host "  3. Menu: Janela > Extensoes > Highlights Cutter"
Write-Host "======================================"
