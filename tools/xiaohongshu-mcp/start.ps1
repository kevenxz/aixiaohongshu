param(
    [int]$Port = 18060,
    [string]$CookiesPath = "",
    [string]$TokenEnv = "XHS_MCP_AUTH_TOKEN"
)

$ErrorActionPreference = "Stop"
$envFile = Join-Path $PSScriptRoot "..\..\.env"
$tokenLine = $null
if (Test-Path $envFile) {
    $tokenLine = Get-Content $envFile -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^$([regex]::Escape($TokenEnv))=" } |
        Select-Object -First 1
}
if ($tokenLine) {
    $env:AUTH_TOKEN = $tokenLine.Substring($TokenEnv.Length + 1)
} else {
    $env:AUTH_TOKEN = ""
}

if ($CookiesPath) {
    $cookieDir = Split-Path -Parent $CookiesPath
    if ($cookieDir) {
        New-Item -ItemType Directory -Force -Path $cookieDir | Out-Null
    }
    $env:COOKIES_PATH = $CookiesPath
}

Set-Location $PSScriptRoot
.\xiaohongshu-mcp.exe -port ":$Port"