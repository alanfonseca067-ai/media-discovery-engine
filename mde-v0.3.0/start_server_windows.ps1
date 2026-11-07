param(
    [switch]$Lan,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Ambiente virtual não encontrado. Execute primeiro: .\install_windows.ps1" }
if (-not (Test-Path ".env")) { throw "Configuração local não encontrada. Execute primeiro: .\configure_windows.ps1" }
if ($Port -lt 1 -or $Port -gt 65535) { throw "Porta inválida." }

$HostAddress = "127.0.0.1"
if ($Lan) {
    $HostAddress = "0.0.0.0"
    Write-Host "Atenção: o servidor ficará acessível pela rede local. Não encaminhe a porta no roteador." -ForegroundColor Yellow
}

Write-Host ("Iniciando MDE em http://" + $HostAddress + ":" + $Port)
Write-Host "Painel: /admin | Saúde: /health | Swagger: /docs"
Write-Host "Logs de acesso HTTP desativados para reduzir exposição de credenciais Xtream."
& $Python -m uvicorn mde_v030.main:app --host $HostAddress --port $Port --no-access-log
