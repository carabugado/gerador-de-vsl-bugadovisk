# Traducao Simultanea — Instalacao Windows (PowerShell)
# Rode na pasta do projeto:
#   powershell -ExecutionPolicy Bypass -File .\install_translate_win.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host "======================================"
Write-Host " Traducao Simultanea - Instalacao Windows"
Write-Host "======================================"

# 1. Habilita o modo debug do CEP (extensoes nao-assinadas) via registro
Write-Host "[1/2] Habilitando modo debug do CEP..."
foreach ($v in 10, 11, 12) {
    $key = "HKCU:\Software\Adobe\CSXS.$v"
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -Path $key -Name "PlayerDebugMode" -Value "1" -PropertyType String -Force | Out-Null
}

# 2. Instala a extensao CEP em %APPDATA%\Adobe\CEP\extensions
$cepDir = Join-Path $env:APPDATA "Adobe\CEP\extensions\com.simultaneo.translate"
Write-Host "[2/2] Instalando a extensao em: $cepDir"
if (Test-Path $cepDir) { Remove-Item $cepDir -Recurse -Force }
New-Item -ItemType Directory -Path $cepDir -Force | Out-Null
Copy-Item -Path (Join-Path $root "cep-translate\*") -Destination $cepDir -Recurse -Force

Write-Host ""
Write-Host "======================================"
Write-Host "Instalacao concluida!"
Write-Host ""
Write-Host "PROXIMOS PASSOS:"
Write-Host "  (O backend e o Python ja sao os mesmos do painel VSL/Highlights.)"
Write-Host "  1. Ligue o backend:  .\start_server.bat   (deixe a janela aberta)"
Write-Host "  2. REINICIE o Premiere"
Write-Host "  3. Menu: Janela > Extensoes > Traducao Simultanea"
Write-Host "======================================"
