$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Test-Url([string]$Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri $Url
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Start-Mcp([int]$Port, [string]$LogName, [string[]]$ExtraArgs) {
    if (Test-Url "http://127.0.0.1:$Port/health") {
        return
    }
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $root "tools\xiaohongshu-mcp\start.ps1"),
        "-Port", "$Port"
    ) + $ExtraArgs
    Start-Process powershell -WindowStyle Hidden -ArgumentList $arguments `
        -RedirectStandardOutput (Join-Path $logDir "$LogName.log") `
        -RedirectStandardError (Join-Path $logDir "$LogName.err.log")
}

# AI 账号：沿用 tools/xiaohongshu-mcp/cookies.json
Start-Mcp -Port 18060 -LogName "mcp" -ExtraArgs @("-TokenEnv", "XHS_MCP_AUTH_TOKEN")

# 明星图集账号：独立 cookies 与端口
$starCookies = Join-Path $root "data\xhs-mcp-star\cookies.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $starCookies) | Out-Null
Start-Mcp -Port 18061 -LogName "mcp-star" -ExtraArgs @(
    "-CookiesPath", $starCookies,
    "-TokenEnv", "XHS_MCP_TOKEN_STAR"
)

if (-not (Test-Url "http://127.0.0.1:8000/health")) {
    Start-Process -FilePath (Join-Path $root ".venv\Scripts\python.exe") -WorkingDirectory $root -WindowStyle Hidden -ArgumentList @(
        "-m", "uvicorn", "xhs_workflow.main:app", "--host", "127.0.0.1", "--port", "8000"
    ) -RedirectStandardOutput (Join-Path $logDir "app.log") -RedirectStandardError (Join-Path $logDir "app.err.log")
}