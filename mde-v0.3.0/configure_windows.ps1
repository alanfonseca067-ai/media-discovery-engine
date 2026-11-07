param(
    [switch]$Force,
    [string]$PrivateHosts = ""
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$EnvFile = Join-Path $Root ".env"

if ((Test-Path $EnvFile) -and -not $Force) {
    Write-Host "O arquivo .env já existe. Nada foi alterado." -ForegroundColor Yellow
    Write-Host "Para substituí-lo, execute: .\configure_windows.ps1 -Force"
    exit 0
}

$username = Read-Host "Usuário inicial do NX [nx-teste]"
if ([string]::IsNullOrWhiteSpace($username)) { $username = "nx-teste" }
if ($username -notmatch '^[A-Za-z0-9_.-]{3,64}$') {
    throw "O usuário precisa ter de 3 a 64 caracteres: letras, números, ponto, hífen ou sublinhado."
}

$securePassword = Read-Host "Digite uma senha com pelo menos 12 letras ou números" -AsSecureString
$passwordPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPtr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPtr)
}

if ($password.Length -lt 12 -or $password -notmatch '^[A-Za-z0-9]+$') {
    throw "Use pelo menos 12 caracteres e somente letras ou números para facilitar a configuração local."
}

$adminKey = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
$lines = @(
    "MDE_DATABASE_PATH=mde-v030.sqlite3",
    "MDE_BOOTSTRAP_USER=$username",
    "MDE_BOOTSTRAP_PASSWORD=$password",
    "MDE_ADMIN_KEY=$adminKey",
    "MDE_PROXY_TOKEN_TTL_SECONDS=21600",
    "MDE_UPSTREAM_TIMEOUT_SECONDS=20",
    "MDE_PLAYBACK_SESSION_TTL_SECONDS=120",
    "MDE_COLLECTION_POLL_SECONDS=5",
    "MDE_MAX_COLLECTION_BYTES=10485760",
    "MDE_MAX_PLAYLIST_BYTES=2097152",
    "MDE_ALLOWED_PRIVATE_HOSTS=$PrivateHosts"
)
Set-Content -Path $EnvFile -Value $lines -Encoding ascii

Write-Host ""
Write-Host "Configuração local salva em .env (arquivo ignorado pelo Git)." -ForegroundColor Green
Write-Host "Usuário inicial: $username"
Write-Host "Senha inicial: $password"
Write-Host "Chave administrativa: $adminKey"
if ($PrivateHosts) { Write-Host "Hosts privados liberados somente para laboratório: $PrivateHosts" }
Write-Host ""
Write-Host "Guarde a senha e a chave administrativa. Não publique nem compartilhe o arquivo .env."
