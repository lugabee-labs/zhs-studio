# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    ZHS Studio 桌面端一键构建（PyInstaller + pywebview，onedir）。

.DESCRIPTION
    做的事只有四步：
      1. 找 Python 解释器（必须已经装好 pyinstaller 和 pywebview）；
      2. 校验依赖能 import；
      3. 跑 pyinstaller desktop\ZHStudio.spec；
      4. 把 config.example.json 复制到 exe 旁边，打印体积与耗时。

    产出：<仓库根>\dist_exe\ZHStudio\ZHStudio.exe  ← 双击这个

.PARAMETER Python
    指定解释器全路径。不传时的查找顺序：
      $env:ZHS_BUILD_PYTHON → <仓库根>\.venv\Scripts\python.exe
      → D:\tools\fuckZHS\fuckZHS-master\.venv\Scripts\python.exe → PATH 里的 python
      （找到后仍会验证 pyinstaller / pywebview 是否可 import）

.PARAMETER NoClean
    不在构建前清 PyInstaller 缓存（改完代码想快一点时用；改了 spec 不建议）。

.EXAMPLE
    pwsh -NoProfile -File .\desktop\build.ps1

.EXAMPLE
    .\desktop\build.ps1 -Python "D:\tools\fuckZHS\fuckZHS-master\.venv\Scripts\python.exe"
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"

try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    [!!] $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 0. 路径
$DesktopDir = $PSScriptRoot
$Repo       = Split-Path -Parent $DesktopDir
$Spec       = Join-Path $DesktopDir "ZHStudio.spec"
$OutDir     = Join-Path $Repo "dist_exe"
$WorkDir    = Join-Path $Repo "build\pyinstaller-desktop"
$AppDir     = Join-Path $OutDir "ZHStudio"
$ExePath    = Join-Path $AppDir "ZHStudio.exe"

Write-Host "ZHS Studio 桌面端构建" -ForegroundColor Magenta
Write-Host "  仓库根 : $Repo"
Write-Host "  spec   : $Spec"
Write-Host "  输出   : $ExePath"

if (-not (Test-Path $Spec)) { throw "找不到 spec：$Spec" }

# ---------------------------------------------------------------- 1. 选解释器
Write-Step "查找 Python 解释器"

$candidates = @()
if ($Python) { $candidates += $Python }
if ($env:ZHS_BUILD_PYTHON) { $candidates += $env:ZHS_BUILD_PYTHON }
$candidates += (Join-Path $Repo ".venv\Scripts\python.exe")
$candidates += "D:\tools\fuckZHS\fuckZHS-master\.venv\Scripts\python.exe"

$py = $null
foreach ($c in $candidates) {
    if ($c -and (Test-Path $c)) { $py = $c; break }
}
if (-not $py) {
    # 最后退到 PATH
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source }
}
if (-not $py) {
    throw "没找到 Python。请先建 venv 并装依赖（见 desktop\README.md），或用 -Python 指定解释器。"
}
Write-Ok "解释器：$py"

# ---------------------------------------------------------------- 2. 校验依赖
Write-Step "校验构建依赖"

$probe = "import sys, PyInstaller, webview, uvicorn, fastapi; from importlib.metadata import version; print(sys.version.split()[0], PyInstaller.__version__, version('pywebview'))"
$probeOut = & $py -c $probe 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host $probeOut
    throw @"
依赖不全。请在同一个解释器里装齐：
    & "$py" -m pip install -r "$Repo\requirements.txt"
    & "$py" -m pip install pyinstaller pywebview
（pywebview 在 Windows 上走 pythonnet + WebView2；WebView2 Runtime 见 desktop\README.md）
"@
}
$parts = ($probeOut | Out-String).Trim() -split "\s+"
Write-Ok "python $($parts[0]) · pyinstaller $($parts[1]) · pywebview $($parts[2])"

# ---------------------------------------------------------------- 3. 构建
Write-Step "运行 PyInstaller（onedir，首次约 1~3 分钟）"

$pyArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--distpath", $OutDir,
    "--workpath", $WorkDir,
    "--log-level", "WARN"
)
if (-not $NoClean) { $pyArgs += "--clean" }
$pyArgs += $Spec

$sw = [Diagnostics.Stopwatch]::StartNew()
& $py @pyArgs
$code = $LASTEXITCODE
$sw.Stop()

if ($code -ne 0) { throw "PyInstaller 失败，退出码 $code。看上面的 WARN/ERROR 输出，或 $WorkDir\ZHStudio\warn-ZHStudio.txt" }
if (-not (Test-Path $ExePath)) { throw "构建结束但没看到 $ExePath" }

# ---------------------------------------------------------------- 4. 收尾：配置样例 + 体积
Write-Step "收尾"

$example = Join-Path $Repo "config.example.json"
$exampleDst = Join-Path $AppDir "config.example.json"
if ((Test-Path $example) -and -not (Test-Path $exampleDst)) {
    Copy-Item $example $exampleDst -Force
    Write-Ok "已复制 config.example.json 到 exe 旁边"
}

$files = Get-ChildItem $AppDir -Recurse -File
$totalBytes = ($files | Measure-Object -Sum Length).Sum
$exeBytes = (Get-Item $ExePath).Length
$totalMB = [math]::Round($totalBytes / 1MB, 1)
$exeMB = [math]::Round($exeBytes / 1KB, 0)

Write-Host ""
Write-Host "构建完成" -ForegroundColor Green
Write-Host "  exe      : $ExePath"
Write-Host "  启动器   : $exeMB KB（真正的大头在 _internal\）"
Write-Host "  整包     : $totalMB MB / $($files.Count) 个文件"
Write-Host "  耗时     : $([math]::Round($sw.Elapsed.TotalSeconds,1)) 秒"
Write-Host ""
Write-Host "下一步：" -ForegroundColor Yellow
Write-Host "  1) 首次运行前，把 $exampleDst 复制成 config.json，填好 engine_home（引擎不打包进 exe）"
Write-Host "  2) 双击 ZHStudio.exe（要整个 ZHStudio 目录一起搬走，别只拿 exe）"
Write-Host "  3) 日志在 $AppDir\logs\desktop.log"
