param(
    [switch]$Once
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Ambiente virtual não encontrado. Execute primeiro: .\install_windows.ps1" }
if (-not (Test-Path ".env")) { throw "Configuração local não encontrada. Execute primeiro: .\configure_windows.ps1" }

Write-Host "Iniciando worker de coleta M3U autorizada..."
if ($Once) {
    & $Python -m mde_v030.worker --once
} else {
    & $Python -m mde_v030.worker
}
