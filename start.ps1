# ZHS Studio 一键启动（Windows 便捷脚本；macOS/Linux 直接 `python run.py`）
#
#   .\start.ps1                启动服务并打开界面
#   .\start.ps1 -Port 8780     换端口（默认读 config.json 的 server.port，缺省 8770）
#   .\start.ps1 -NoBrowser     只起服务，不开浏览器
#
# 解释器探测顺序：$env:ZHS_PYTHON → <repo>\.venv → py -3 → python
# （旧版把某个 venv 的绝对路径写死了，换机器必挂；这里不再绑任何本机路径）
param(
    [int]$Port = 0,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---------------------------------------------------------------- 端口
if ($Port -le 0) {
    $Port = 8770
    $cfgPath = Join-Path $root 'config.json'
    if (Test-Path $cfgPath) {
        try {
            $cfg = Get-Content -Path $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($cfg.server -and $cfg.server.port) { $Port = [int]$cfg.server.port }
        } catch {
            Write-Host "config.json 解析失败，端口回落 8770：$($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}
$hostAddr = '127.0.0.1'
$cfgPath = Join-Path $root 'config.json'
if (Test-Path $cfgPath) {
    try {
        $cfg = Get-Content -Path $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.server -and $cfg.server.host) { $hostAddr = [string]$cfg.server.host }
    } catch { }
}

# ---------------------------------------------------------------- 解释器
$py = $null
$pyArgs = @()
if ($env:ZHS_PYTHON -and (Test-Path $env:ZHS_PYTHON)) {
    $py = $env:ZHS_PYTHON
}
if (-not $py) {
    foreach ($cand in @((Join-Path $root '.venv\Scripts\python.exe'), (Join-Path $root '.venv/bin/python'))) {
        if (Test-Path $cand) { $py = $cand; break }
    }
}
if (-not $py) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { $py = $launcher.Source; $pyArgs = @('-3') }
}
if (-not $py) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { $py = $python.Source }
}
if (-not $py) {
    throw @"
找不到 Python 解释器。请先安装 Python 3.10+，然后在本目录执行：
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
（也可以设置环境变量 ZHS_PYTHON 指向某个 python.exe）
"@
}
Write-Host "Python: $py $($pyArgs -join ' ')" -ForegroundColor DarkGray

# ---------------------------------------------------------------- 已启动就不重复起
$apiState = "http://$hostAddr`:$Port/api/state"
$alive = $false
try {
    $r = Invoke-WebRequest $apiState -UseBasicParsing -TimeoutSec 3
    $alive = ($r.StatusCode -eq 200)
} catch { $alive = $false }

if (-not $alive) {
    $logs = Join-Path $root 'logs'
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $outLog = Join-Path $logs 'uvicorn.log'
    $errLog = Join-Path $logs 'uvicorn.err.log'
    $uvArgs = @($pyArgs) + @('-u', '-m', 'uvicorn', 'app:app',
                            '--host', $hostAddr, '--port', "$Port", '--log-level', 'warning')
    $p = Start-Process -FilePath $py -ArgumentList $uvArgs `
        -WorkingDirectory $root `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -WindowStyle Hidden -PassThru
    Write-Host "服务启动中 (PID $($p.Id)) …" -ForegroundColor DarkGray

    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            if ((Invoke-WebRequest $apiState -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) {
        Write-Host "服务没起来。看日志：$errLog" -ForegroundColor Red
        if (Test-Path $errLog) { Get-Content $errLog -Tail 25 }
        Write-Host "最常见原因：config.json 还没建、或 engine_home 指向的 fuckZHS 目录不对。" -ForegroundColor Yellow
        exit 1
    }
}

$url = "http://$hostAddr`:$Port/"
Write-Host "ZHS Studio → $url" -ForegroundColor Green
if (-not $NoBrowser) { Start-Process $url }
