# 以 CDP 调试端口启动浏览器 —— 让面板的「抓登录态」能取到 cookie
#
# 背景：面板靠 CDP（--remote-debugging-port，默认 9222）从浏览器里读登录态（含 HttpOnly）。
# 浏览器平时不带这个端口，而同一个 profile 已有实例在跑时，再带参数启动会被忽略。
#
# 旧版做法是「把用户所有 Edge 窗口关掉再重启」——会丢表单/丢会话，很危险。
# 现在改成**独立 profile**：用 --user-data-dir 指向 data/browser-profile，
# 与用户日常浏览器完全隔离，不需要杀任何进程。
#
#   .\start-edge-debug.ps1                  自动探测浏览器，用独立 profile 带 9222 启动
#   .\start-edge-debug.ps1 -Port 9223       换端口
#   .\start-edge-debug.ps1 -Browser chrome  指定浏览器（edge|chrome|brave）
#   .\start-edge-debug.ps1 -ProfileDirectory "Profile 1"   多账号时指定 profile 子目录
#
# 注意：这是**新的空白 profile**，第一次用要在这个窗口里手动登录一次智慧树，
# 之后面板才能抓到登录态。
param(
    [int]$Port = 0,
    [string]$Browser = '',
    [string]$ProfileDirectory = ''
)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---------------------------------------------------------------- 配置
$cfg = $null
$cfgPath = Join-Path $root 'config.json'
if (Test-Path $cfgPath) {
    try { $cfg = Get-Content -Path $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
}
$siteUrl = 'https://studyvideoh5.zhihuishu.com/'
$profileDir = $null
if ($cfg) {
    if ($Port -le 0 -and $cfg.cdp -and $cfg.cdp.port) { $Port = [int]$cfg.cdp.port }
    if ($cfg.site -and $cfg.site.home_url) { $siteUrl = [string]$cfg.site.home_url }
    if ($cfg.browser) {
        if (-not $Browser -and $cfg.browser.kind) { $Browser = [string]$cfg.browser.kind }
        if (-not $ProfileDirectory -and $cfg.browser.profile_directory) { $ProfileDirectory = [string]$cfg.browser.profile_directory }
        if ($cfg.browser.profile_dir) {
            $pd = [string]$cfg.browser.profile_dir
            if ([System.IO.Path]::IsPathRooted($pd)) { $profileDir = $pd } else { $profileDir = Join-Path $root $pd }
        }
    }
}
if ($Port -le 0) { $Port = 9222 }
if (-not $ProfileDirectory) { $ProfileDirectory = 'Default' }
if (-not $profileDir) { $profileDir = Join-Path $root 'data\browser-profile' }

# ---------------------------------------------------------------- 已经带着端口在跑？
try {
    $v = (Invoke-WebRequest "http://127.0.0.1:$Port/json/version" -UseBasicParsing -TimeoutSec 3).Content | ConvertFrom-Json
    Write-Host "调试端口已就绪：$($v.Browser)" -ForegroundColor Green
    Write-Host "直接回面板点「抓登录态」即可。" -ForegroundColor DarkGray
    return
} catch { }

# ---------------------------------------------------------------- 找浏览器
function Find-ByRegistry([string]$exeName) {
    $keys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$exeName",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$exeName",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\$exeName"
    )
    foreach ($k in $keys) {
        try {
            $item = Get-ItemProperty -Path $k -ErrorAction Stop
            $p = $item.'(default)'
            if ($p -and (Test-Path $p)) { return $p }
        } catch { }
    }
    return $null
}

$candidates = @()
switch ($Browser.ToLower()) {
    'edge'   { $candidates = @(@('msedge.exe', @("$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"))) }
    'chrome' { $candidates = @(@('chrome.exe', @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"))) }
    'brave'  { $candidates = @(@('brave.exe', @("$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe"))) }
    default  {
        $candidates = @(
            @('msedge.exe',  @("$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe")),
            @('chrome.exe',  @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe")),
            @('brave.exe',   @("$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe"))
        )
    }
}

$exe = $null
if ($cfg -and $cfg.browser -and $cfg.browser.path -and (Test-Path $cfg.browser.path)) {
    $exe = [string]$cfg.browser.path
}
while (-not $exe -and $candidates.Count -gt 0) {
    $group = $candidates[0]
    $candidates = @($candidates | Select-Object -Skip 1)
    $name = $group[0]
    $paths = $group[1]
    $exe = Find-ByRegistry $name
    if (-not $exe) {
        foreach ($p in $paths) { if ($p -and (Test-Path $p)) { $exe = $p; break } }
    }
}
if (-not $exe) {
    Write-Host "没找到 Chromium 系浏览器。用 -Browser edge|chrome|brave 指定，或在 config.json 的 browser.path 里写绝对路径。" -ForegroundColor Red
    exit 1
}
Write-Host "浏览器：$exe" -ForegroundColor DarkGray

# ---------------------------------------------------------------- 独立 profile 启动
New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
Write-Host "独立 profile：$profileDir（与日常浏览器隔离，不需要关闭任何窗口）" -ForegroundColor DarkGray

Start-Process -FilePath $exe -ArgumentList @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$profileDir",
    "--profile-directory=$ProfileDirectory",
    "--no-first-run",
    "--no-default-browser-check",
    $siteUrl
) | Out-Null

for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    try {
        $v = (Invoke-WebRequest "http://127.0.0.1:$Port/json/version" -UseBasicParsing -TimeoutSec 2).Content | ConvertFrom-Json
        Write-Host "调试端口就绪：$($v.Browser) (端口 $Port)" -ForegroundColor Green
        Write-Host "下一步：在这个新窗口里登录一次智慧树，然后回面板点「抓登录态」。" -ForegroundColor Green
        return
    } catch { }
}
Write-Host "端口 $Port 未就绪。常见原因：同名 profile 已被另一个调试端口占用。" -ForegroundColor Red
Write-Host "排查：换一个 -Port，或删掉 $profileDir 后重跑本脚本。" -ForegroundColor DarkGray
