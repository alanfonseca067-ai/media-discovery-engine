$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Verificando o launcher Python..."
py --version
if ($LASTEXITCODE -ne 0) {
    throw "Python Launcher (py) não encontrado. Instale Python 3.11 ou superior e tente novamente."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Criando ambiente virtual..."
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Não foi possível criar o ambiente virtual." }
}

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Write-Host "Atualizando pip..."
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Falha ao atualizar pip." }

Write-Host "Instalando MDE e dependências de teste..."
& $Python -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar as dependências do MDE." }

Write-Host "Executando a suíte automatizada..."
& $Python -m pytest -v
if ($LASTEXITCODE -ne 0) {
    throw "Os testes falharam. Não inicie o MDE até corrigir os erros."
}

Write-Host ""
Write-Host "Instalação e testes concluídos." -ForegroundColor Green
if (-not (Test-Path ".env")) {
    Write-Host "Próximo passo: .\configure_windows.ps1"
} else {
    Write-Host "Configuração .env encontrada."
}
Write-Host "Para iniciar o servidor: .\start_server_windows.ps1"
Write-Host "Para iniciar o worker em outro terminal: .\start_worker_windows.ps1"
