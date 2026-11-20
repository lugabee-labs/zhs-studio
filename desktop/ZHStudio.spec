# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— ZHS Studio 桌面端（onedir）。

用法（由 build.ps1 调用，也可以手跑）：

    pyinstaller --noconfirm --clean desktop\\ZHStudio.spec

产出：

    dist_exe\\ZHStudio\\ZHStudio.exe        ← 双击这个
    dist_exe\\ZHStudio\\_internal\\...       ← 依赖与 web/ 资源（别删、别单独搬 exe）

形态选择：onedir，不是 onefile
--------------------------------
* onefile 每次启动都把整个包解压到 %TEMP%\\_MEIxxxxx，冷启动多 2~4 秒，且第二次启动前
  要等上一份临时目录清理；onedir 是直接读本地文件，启动只受 Python 导入耗时影响。
* 更关键的是路径语义：app.py 按 `dirname(__file__)` 当仓库根，冻结后那就是 `sys._MEIPASS`。
  onefile 的 _MEIPASS 是**临时目录**，data/、shots/、data/cookies.json（登录态）会跟着临时目录
  一起消失；onedir 的 _MEIPASS 是 `<exe目录>/_internal`，持久可用。
  （启动器还会额外把配置里的路径钉成「相对 exe 目录」的绝对路径，见 app_desktop.py。）
* 代价：onedir 是一个目录，分发时要整个目录打包（zip），不能只发一个 exe。
  单文件分发的需求请改用 onefile，并接受上面两条。

体积参考：onedir 约 120~180 MB（openai + tiktoken + PIL + pywebview/WebView2 托管 dll 占大头）。
"""
import os

SPEC_DIR = os.path.abspath(SPECPATH)              # ...\\desktop
REPO = os.path.dirname(SPEC_DIR)                  # 仓库根

ENTRY = os.path.join(SPEC_DIR, "app_desktop.py")
WEB_DIR = os.path.join(REPO, "web")
CONFIG_EXAMPLE = os.path.join(REPO, "config.example.json")

# ---------------------------------------------------------------- datas
# web/ 是前端面板（app.py 用 FileResponse 回 index.html）；config.example.json 给「生成配置模板」用。
# 许可证文件一起发出去：MIT 要求随分发保留版权与许可声明。
datas = [
    (WEB_DIR, "web"),
    (CONFIG_EXAMPLE, "."),
]
for extra in ("LICENSE", "NOTICE", "README.md"):
    p = os.path.join(REPO, extra)
    if os.path.isfile(p):
        datas.append((p, "."))

# ---------------------------------------------------------------- hiddenimports
hiddenimports = [
    # 本仓库的模块：app.py 是被 importlib 之外的普通 import 拉进来的（启动器里 `import app`），
    # PyInstaller 能看到这条边；但 zhs_engine 会在运行期 import 外部引擎，这里显式列清楚更稳。
    "app",
    "zhs_config",
    "zhs_engine",

    # uvicorn 的 loop / protocol / lifespan 实现都是**运行期按字符串挑**的，静态分析看不到。
    # 少了 uvicorn.loops.auto 之类，冻结后会得到 "No module named uvicorn.loops.auto"。
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.websockets_sansio_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    # 注意：uvicorn 里没有 lifespan.auto 这个模块 —— config.py 把 "auto" 映射到 lifespan.on，
    # 之前在这写 uvicorn.lifespan.auto 会让 PyInstaller 报 ERROR: Hidden import not found。
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.middleware",
    "uvicorn.middleware.asgi2",
    "uvicorn.middleware.wsgi",

    # 下面这些是**外部引擎**（fuckZHS，运行期从 engine_home 挂进 sys.path）的依赖。
    # 引擎源码不进包，所以 PyInstaller 分析不到这些 import —— 必须手动钉进来，
    # 否则用户配好 engine_home 后仍然会卡在 "import 引擎失败: ModuleNotFoundError: openai"。
    "requests",
    "openai",
    "tiktoken",
    "websocket",            # websocket-client（上游 grab-cookies.py 用）
    "PIL",
    "PIL.Image",
    "PIL.ImageOps",
    "Crypto",
    "Crypto.Cipher",
    "Crypto.Cipher.AES",

    # pywebview 的 Windows 后端（pythonnet → clr）
    "clr",
    "webview.platforms.winforms",
]

# ---------------------------------------------------------------- excludes
# 只砍「确实用不上、又很大」的：GUI 工具箱、科学计算栈、开发工具。
# 注意别把 setuptools / pkg_resources 砍了 —— pythonnet 与部分包在运行期还会碰它。
excludes = [
    "tkinter",
    "_tkinter",
    "turtle",
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "IPython",
    "jupyter",
    "notebook",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    "pytest",
    "sphinx",
]

a = Analysis(
    [ENTRY],
    pathex=[REPO, SPEC_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ZHStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # UPX 会显著提高 Defender 误报率，别开
    console=False,                   # 双击不弹黑框；日志写 <exe目录>/logs/desktop.log
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                       # 想换图标：放 desktop/app.ico 并改成 icon="app.ico"
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ZHStudio",
)
