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
$allowLan = $false
$cfgPath = Join-Path $root 'config.json'
if (Test-Path $cfgPath) {
    try {
        $cfg = Get-Content -Path $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.server -and $cfg.server.host) { $hostAddr = [string]$cfg.server.host }
        if ($cfg.server -and $null -ne $cfg.server.allow_lan) {
            $al = $cfg.server.allow_lan
            $allowLan = ($al -eq $true) -or ("$al" -match '^(?i)(1|true|yes|on|y)$')
        }
    } catch { }
}
# allow_lan=true 且 host 仍是回环地址 → 等价于绑 0.0.0.0。
# 规则与 zhs_config.py 的 load() 保持一致：这里是 Windows 快捷入口，
# 若只读 host 不认 allow_lan，用户翻了配置里的开关却毫无效果。
if ($allowLan -and @('127.0.0.1', 'localhost', '::1', '') -contains $hostAddr) { $hostAddr = '0.0.0.0' }
# 绑 0.0.0.0 时，浏览器与探活都走回环地址（浏览器不一定认 http://0.0.0.0/）
$openHost = if (@('0.0.0.0', '::') -contains $hostAddr) { '127.0.0.1' } else { $hostAddr }

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
$apiState = "http://$openHost`:$Port/api/state"
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

$url = "http://$openHost`:$Port/"
Write-Host "ZHS Studio → $url" -ForegroundColor Green

# 手机/平板端要填的地址：只在真的绑了 0.0.0.0 时才有意义
if ($hostAddr -in @('0.0.0.0', '::')) {
    $ips = @()
    try {
        $ips = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
            Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
            ForEach-Object { $_.IPAddressToString } |
            Where-Object { -not $_.StartsWith('127.') -and -not $_.StartsWith('169.254.') } |
            Select-Object -Unique
    } catch { }
    if ($ips) {
        Write-Host "局域网地址（手机端 / 平板端填这个）：" -ForegroundColor Cyan
        $ips | ForEach-Object { Write-Host "  http://$($_):$Port/" -ForegroundColor Cyan }
        if ($ips.Count -gt 1) {
            Write-Host "  提示：多网段时选和手机同一个网段的那条（通常是 192.168.x.x）。" -ForegroundColor DarkGray
        }
    } else {
        Write-Host "没探测到非回环 IPv4，手机端可能连不上（检查网卡是否已连上局域网）。" -ForegroundColor Yellow
    }
}
if (-not $NoBrowser) { Start-Process $url }
