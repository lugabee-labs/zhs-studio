#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZHS Studio —— Windows 桌面端启动器（PyInstaller + pywebview / WebView2）。

它做的事情只有三件，顺序固定：

  1. 在**后台线程**里起 uvicorn，跑的是上层 `app.py` 里那个同一个 FastAPI 实例
     （默认绑 127.0.0.1 上的一个可用端口，配置里开了 allow_lan 才绑 0.0.0.0）；
  2. 轮询 `GET /api/health`，确认后端真的活了、而且真的是 ZHS Studio，再开窗口；
  3. 用 pywebview 开原生窗口（WebView2），标题 `ZHS Studio`，1400x900，最小 1100x700。
     窗口关掉 → 优雅停掉 uvicorn（should_exit + join，超时再 force_exit）。

为什么不是「先开窗口再加载」：
  后端没起来时窗口会先白一下再报错，看起来像崩了。先探活、再开窗，
  失败时开的是一个**带说明的本地页面**（不是 traceback，也不是空白）。

打包形态是 onedir（见 ZHStudio.spec）：
  * onefile 每次启动都要把上百 MB 解压到 %TEMP%\\_MEIxxxx，冷启动多 2~4 秒，
    而且 `sys._MEIPASS` 是临时目录 —— app.py 按 `__file__` 解析出的 data/ 会落在临时目录里，
    关掉程序就被清了（登录态 cookies.json 会跟着没）；
  * onedir 的 `_MEIPASS` 是 `<exe目录>/_internal`，持久且启动快。桌面端选它。

资源路径（冻结后）：
  代码/网页资源在 `sys._MEIPASS`（web/、config.example.json 由 spec 的 datas 带进去），
  用户数据/配置在 **exe 旁边**。所以启动器先把「按 exe 目录解析过的绝对路径配置」写成一份
  临时 config，再用 ZHS_CONFIG 指给 app.py —— 否则 app.py 会拿 _MEIPASS 当仓库根，
  data/ 就跑到 _internal 里去了。

引擎（fuckZHS）**不打包**：体积 + 许可都不合适。运行时按 config.json 的 engine_home 去
sys.path 里挂。缺引擎时窗口里显示可操作的提示页（告诉你 config.json 该放哪、怎么生成）。
"""
import argparse
import ctypes
import html
import json
import os
import socket
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser

APP_TITLE = "ZHS Studio"
WINDOW_SIZE = (1400, 900)
MIN_SIZE = (1100, 700)
HEALTH_TIMEOUT_SEC = 45.0     # 首次启动要 import 引擎（openai/tiktoken 都要几百 ms），给宽一点
HEALTH_INTERVAL_SEC = 0.25

_PROC_START = time.time()
_LOG_LOCK = threading.RLock()      # RLock：日志路径允许重入，别用 Lock（踩过死锁）
_LOG_FILE = None


# ---------------------------------------------------------------- 日志
def _file_line(text: str) -> str:
    return f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] +{time.time() - _PROC_START:6.2f}s {text}"


def _write_file(text: str):
    """只写文件，绝不回头调用 print/log —— 否则 windowed 模式下会递归死锁。

    （踩过的坑：console=False 时 sys.stdout 是 None，被换成 _NullWriter；如果 _NullWriter.write
     再调 log()，而 log() 最后又 print() → 无限递归，且第一次就卡在 _LOG_LOCK 上，
     进程永远停在日志第一行、窗口再也开不出来。所以这里把「写盘」和「打印」彻底分开。）
    """
    with _LOG_LOCK:
        if not _LOG_FILE:
            return
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass


class _NullWriter:
    """windowed 模式（console=False）下 sys.stdout/stderr 是 None，print() 会炸。

    第三方库（uvicorn / pywebview / pythonnet）都会往 stdout 写东西，必须有个落点。
    这里收到什么就按行丢进日志文件 —— 不再调用 log()，避免递归。
    """

    def write(self, s):
        if s and s.strip():
            _write_file(_file_line(s.rstrip()))
        return len(s or "")

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        raise OSError("no fileno (windowed mode)")


def _ensure_std_streams():
    if sys.stdout is None:
        sys.stdout = _NullWriter()
    if sys.stderr is None:
        sys.stderr = _NullWriter()


def log(msg: str):
    """写日志文件；只有存在真正的控制台时才同时打印。"""
    line = _file_line(msg)
    _write_file(line)
    out = sys.stdout
    if isinstance(out, _NullWriter) or out is None:
        return
    try:
        print(line, flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------- 路径
def resolve_paths():
    """返回 (home, resources)。

    home      —— 用户看得见的目录：config.json / data / shots / logs 都在这里。
                 冻结后 = exe 所在目录；源码运行 = 仓库根。
    resources —— 代码与网页资源所在：app.py / zhs_config.py / web/ / config.example.json。
                 冻结后 = sys._MEIPASS；源码运行 = 仓库根。
    """
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        home = os.path.dirname(exe)
        res = getattr(sys, "_MEIPASS", None) or home
        return home, os.path.abspath(res)
    here = os.path.dirname(os.path.abspath(__file__))          # .../desktop
    root = os.path.dirname(here)                               # 仓库根
    return root, root


def prepare_environment(home: str, res: str, cli_config: str | None):
    """把 sys.path / cwd / ZHS_CONFIG 都摆好，返回 (cfg, user_config_path)。

    关键一步：按 **home** 解析配置（相对路径相对 exe 目录），再把这份「绝对路径已就位」的
    配置写进临时文件交给 app.py。app.py 自己是用 `dirname(app.py)` 当仓库根的 —— 冻结后
    那是 _MEIPASS，会把 data/ 指进 _internal，所以必须在这里把语义纠正过来。
    """
    sys.path.insert(0, res)
    try:
        os.chdir(home)
    except OSError:
        pass

    import zhs_config  # noqa: E402  （必须在 sys.path 就位之后）

    if cli_config:
        user_cfg = os.path.abspath(os.path.expanduser(cli_config))
        if not os.path.isfile(user_cfg):
            raise FileNotFoundError(f"--config 指向的文件不存在：{user_cfg}")
    else:
        cand = os.path.join(home, "config.json")
        user_cfg = cand if os.path.isfile(cand) else None

    cfg = zhs_config.load(here=home, path=user_cfg)

    # 归一化副本：去掉 _ 开头的内部键，路径全是绝对路径
    norm = {k: v for k, v in cfg.items() if not str(k).startswith("_")}
    tmpdir = tempfile.mkdtemp(prefix="zhs-studio-cfg-")
    tmpcfg = os.path.join(tmpdir, "config.json")
    with open(tmpcfg, "w", encoding="utf-8") as fh:
        json.dump(norm, fh, ensure_ascii=False, indent=2)
    os.environ["ZHS_CONFIG"] = tmpcfg
    return cfg, user_cfg


def _mkdirs_for_logs(home: str):
    global _LOG_FILE
    d = os.path.join(home, "logs")
    try:
        os.makedirs(d, exist_ok=True)
        _LOG_FILE = os.path.join(d, "desktop.log")
    except OSError:
        _LOG_FILE = None


# ---------------------------------------------------------------- 端口
def _port_free(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            # Windows 上 SO_REUSEADDR 会允许绑到已被占用的端口，这里千万别设
            pass
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _free_port(host: str) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def pick_bind(cfg: dict, cli_host: str | None, cli_port: int | None):
    """决定 uvicorn 绑哪。返回 (bind_host, port, probe_host)。

    * 默认（配置 host=127.0.0.1）：绑回环，探活用 127.0.0.1 —— 桌面端一个人用，不必要暴露。
    * 配置开了 allow_lan / host=0.0.0.0：照配置绑 0.0.0.0，手机端才连得上；探活仍走 127.0.0.1
      （浏览器不认 http://0.0.0.0/）。
    * 端口优先用配置里那个（和 `python run.py` 保持一致，方便手机端记地址）；
      被占了才退到系统分配的可用端口，并把这个事实写进日志。
    """
    cfg_host = str(cfg["server"]["host"] or "127.0.0.1")
    want_lan = bool(cfg["server"].get("allow_lan")) or cfg_host not in ("127.0.0.1", "localhost", "::1", "")

    if cli_host:
        cfg_host = cli_host
        want_lan = cli_host not in ("127.0.0.1", "localhost", "::1")

    bind_host = "0.0.0.0" if want_lan else "127.0.0.1"
    probe_host = "127.0.0.1"

    want_port = int(cli_port or cfg["server"]["port"])
    if _port_free(bind_host, want_port):
        return bind_host, want_port, probe_host

    port = _free_port(bind_host)
    log(f"端口 {want_port} 已被占用，改用可用端口 {port}")
    return bind_host, port, probe_host


# ---------------------------------------------------------------- 后端
class Backend:
    """在后台线程里跑 uvicorn；提供 start()/wait_ready()/stop()。"""

    def __init__(self, app_obj, host: str, port: int):
        self.app_obj = app_obj
        self.host = host
        self.port = port
        self.server = None
        self.thread = None
        self.url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/"

    def start(self):
        import uvicorn

        # log_config=None：别让 uvicorn 的 dictConfig 把 root logger 抢走（冻结后日志要写文件）
        cfg = uvicorn.Config(
            self.app_obj,
            host=self.host,
            port=self.port,
            log_level="warning",
            log_config=None,
            access_log=False,
            server_header=False,
        )
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self._run, name="zhs-uvicorn", daemon=True)
        self.thread.start()

    def _run(self):
        try:
            self.server.run()
        except BaseException as e:  # noqa: BLE001 —— 线程里的异常必须自己兜住，否则静默死掉
            log(f"uvicorn 线程退出：{type(e).__name__}: {e}")
            log(traceback.format_exc().rstrip())

    def wait_ready(self, timeout: float = HEALTH_TIMEOUT_SEC):
        """轮询 /api/health 直到 200 且 {"ok": true}。返回 (ok, detail)。"""
        probe_host = "127.0.0.1" if self.host in ("0.0.0.0", "::", "") else self.host
        url = f"http://{probe_host}:{self.port}/api/health"
        deadline = time.time() + timeout
        last = "还没探到"
        while time.time() < deadline:
            if self.thread is not None and not self.thread.is_alive():
                return False, f"后端线程提前退出（{last}）"
            try:
                with urllib.request.urlopen(url, timeout=1.5) as r:
                    body = r.read().decode("utf-8", "replace")
                data = json.loads(body)
                if isinstance(data, dict) and data.get("ok") is True:
                    return True, data.get("version") or "?"
                last = f"返回不是 ZHS Studio：{body[:120]}"
            except urllib.error.URLError as e:
                last = f"{type(e).__name__}: {e}"
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
            time.sleep(HEALTH_INTERVAL_SEC)
        return False, f"{timeout:.0f}s 内没起来（最后一次：{last}）"

    def stop(self):
        if not self.server:
            return
        log("正在停止后端…")
        try:
            self.server.should_exit = True
        except Exception:
            pass
        if self.thread:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                log("后端 10s 内没停干净，强制退出")
                try:
                    self.server.force_exit = True
                except Exception:
                    pass
        log("后端已停止")


# ---------------------------------------------------------------- 提示页（引擎缺失 / 启动失败）
_PAGE_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;padding:44px 48px;background:#14161c;color:#e8ebf2;
 font:14px/1.72 "Microsoft YaHei UI","Segoe UI",system-ui,sans-serif}
h1{margin:0 0 6px;font-size:23px;font-weight:600;letter-spacing:.2px}
.sub{color:#8b93a7;margin-bottom:26px;font-size:13px}
.card{background:#1b1f28;border:1px solid #2a3040;border-radius:10px;padding:18px 20px;margin-bottom:16px}
.card h2{margin:0 0 10px;font-size:14px;font-weight:600;color:#9fb4ff;letter-spacing:.4px}
code,.k{font-family:Consolas,"Cascadia Mono",monospace;background:#0f1218;border:1px solid #262c3a;
 border-radius:5px;padding:1px 6px;font-size:12.5px;word-break:break-all}
pre{white-space:pre-wrap;word-break:break-all;background:#0f1218;border:1px solid #262c3a;border-radius:8px;
 padding:12px 14px;font-size:12.5px;line-height:1.6;color:#c9d1e4;margin:10px 0 0;max-height:230px;overflow:auto}
ol{margin:10px 0 0;padding-left:22px}
li{margin:5px 0}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}
button{background:#2f5fd8;color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:13.5px;
 cursor:pointer;font-family:inherit}
button.ghost{background:#242a36;border:1px solid #333c4e;color:#c9d1e4}
button:hover{filter:brightness(1.12)}
.hint{color:#8b93a7;font-size:12.5px;margin-top:18px}
.ok{color:#5fd08a}
"""

_PAGE_JS = """
async function call(name, ...args){
  try{
    if(!window.pywebview || !window.pywebview.api) { alert('桥接还没就绪，请稍等一秒再点'); return; }
    const r = await window.pywebview.api[name](...args);
    if(r && r.message) { document.getElementById('msg').textContent = r.message; }
  }catch(e){ document.getElementById('msg').textContent = '调用失败：' + e; }
}
"""


def build_error_page(kind: str, title: str, detail: str, home: str, user_cfg: str | None,
                     engine_home: str, cfg_path_shown: str, extra_html: str = "") -> str:
    """生成提示页。所有外部内容都过 html.escape —— 路径里带 & < > 也不会破坏页面。"""
    def esc(s):
        return html.escape(str(s or ""), quote=True)

    steps = ""
    if kind == "engine":
        steps = f"""
<div class="card">
  <h2>怎么办（三分钟）</h2>
  <ol>
    <li>把 fuckZHS 引擎放到任意目录（或在已有目录里确认能看到 <code>fucker.py</code>）：
        <pre>git clone https://github.com/VermiIIi0n/fuckZHS.git D:\\tools\\fuckZHS</pre></li>
    <li>在程序目录下建 <code>config.json</code>（点下面「生成配置模板」会给你一份），
        把绝对路径填进 <code>engine_home</code>：
        <pre>{{ "engine_home": "D:\\\\tools\\\\fuckZHS" }}</pre></li>
    <li>关掉这个窗口，重新双击 <code>ZHStudio.exe</code>。</li>
  </ol>
  <div class="hint">当前生效的 engine_home：<code>{esc(engine_home) or '（空 —— 还没配）'}</code></div>
</div>"""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{esc(APP_TITLE)} — {esc(title)}</title><style>{_PAGE_CSS}</style></head>
<body>
<h1>{esc(title)}</h1>
<div class="sub">ZHS Studio 桌面端 v{esc(_app_version())} · 后端没有启动，所以这里是本地说明页，不是报错崩溃。</div>

<div class="card">
  <h2>发生了什么</h2>
  <pre>{esc(detail)}</pre>
</div>

<div class="card">
  <h2>程序目录</h2>
  <code>{esc(home)}</code>
  <div class="hint">配置文件应放在：<code>{esc(user_cfg or os.path.join(home, 'config.json'))}</code>
  {'（<span class="ok">已找到</span>）' if user_cfg else '（<span>尚未创建</span>）'}</div>
</div>
{steps}
{extra_html}

<div class="row">
  <button onclick="call('create_config')">生成配置模板 config.json</button>
  <button class="ghost" onclick="call('open_home')">打开程序目录</button>
  <button class="ghost" onclick="call('open_logs')">打开日志目录</button>
  <button class="ghost" onclick="call('quit')">退出</button>
</div>
<div class="hint" id="msg">改完配置后请关掉本窗口并重新启动程序。</div>

<script>{_PAGE_JS}</script>
</body></html>"""


_APP_VERSION_CACHE = {}


def _app_version() -> str:
    if "v" in _APP_VERSION_CACHE:
        return _APP_VERSION_CACHE["v"]
    v = "?"
    try:
        import zhs_config
        v = getattr(zhs_config, "APP_VERSION", "?")
    except Exception:
        pass
    _APP_VERSION_CACHE["v"] = v
    return v


class Bridge:
    """提示页上的按钮 ↔ Python。只在后端没起来时挂到窗口上。"""

    def __init__(self, home: str, res: str, example_cfg: str | None, user_cfg: str | None):
        self.home = home
        self.res = res
        self.example_cfg = example_cfg
        self.user_cfg = user_cfg
        self.window = None

    def _msg(self, text: str):
        return {"message": text}

    def open_home(self):
        try:
            os.startfile(self.home)  # type: ignore[attr-defined]
            return self._msg(f"已打开：{self.home}")
        except Exception as e:  # noqa: BLE001
            return self._msg(f"打不开目录：{e}")

    def open_logs(self):
        d = os.path.join(self.home, "logs")
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)  # type: ignore[attr-defined]
            return self._msg(f"已打开：{d}")
        except Exception as e:  # noqa: BLE001
            return self._msg(f"打不开日志目录：{e}")

    def create_config(self):
        target = self.user_cfg or os.path.join(self.home, "config.json")
        if os.path.isfile(target):
            try:
                os.startfile(target)  # type: ignore[attr-defined]
            except Exception:
                pass
            return self._msg(f"config.json 已存在，已尝试用默认程序打开：{target}")
        if not self.example_cfg or not os.path.isfile(self.example_cfg):
            return self._msg("找不到内置的 config.example.json，没法生成模板")
        try:
            with open(self.example_cfg, encoding="utf-8") as fh:
                data = json.load(fh)
            # 把路径类字段钉成绝对路径，省得用户再猜「相对哪个目录」
            data["engine_home"] = ""
            data["data_dir"] = os.path.join(self.home, "data")
            data["shots_dir"] = os.path.join(self.home, "shots")
            data["cookie_file"] = os.path.join(self.home, "data", "cookies.json")
            data.setdefault("browser", {})["profile_dir"] = os.path.join(self.home, "data", "browser-profile")
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            try:
                os.startfile(target)  # type: ignore[attr-defined]
            except Exception:
                pass
            self.user_cfg = target
            return self._msg(f"已生成：{target} —— 把 engine_home 填成引擎绝对路径，然后重启程序")
        except Exception as e:  # noqa: BLE001
            return self._msg(f"生成失败：{type(e).__name__}: {e}")

    def quit(self):
        log("提示页点了退出")
        try:
            if self.window is not None:
                self.window.destroy()
        except Exception:
            pass
        return self._msg("正在退出…")


def show_window(title: str, *, url: str | None = None, html_text: str | None = None,
                bridge: Bridge | None = None, storage_path: str | None = None):
    """开窗口并阻塞到窗口关闭。返回 (ok, err)。"""
    import webview

    kwargs = {
        "title": title,
        "width": WINDOW_SIZE[0],
        "height": WINDOW_SIZE[1],
        "min_size": MIN_SIZE,
        "text_select": True,
    }
    if bridge is not None:
        kwargs["js_api"] = bridge
    if url:
        kwargs["url"] = url
    else:
        kwargs["html"] = html_text or "<h1>ZHS Studio</h1>"

    window = webview.create_window(**kwargs)
    if bridge is not None:
        bridge.window = window

    start_kwargs = {"debug": False, "private_mode": True}
    if storage_path:
        start_kwargs["private_mode"] = False
        start_kwargs["storage_path"] = storage_path

    try:
        webview.start(gui="edgechromium", **start_kwargs)
        return True, None
    except Exception as e:  # noqa: BLE001 —— WebView2 运行时缺失会走到这里
        return False, f"{type(e).__name__}: {e}"


def _msgbox(text: str, title: str = APP_TITLE, icon: int = 0x10):
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, icon)  # type: ignore[attr-defined]
    except Exception:
        pass


# ---------------------------------------------------------------- 主流程
def run(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ZHStudio", description="ZHS Studio 桌面端")
    ap.add_argument("--config", default=None, help="指定 config.json（默认用程序目录下那份）")
    ap.add_argument("--host", default=None, help="覆盖监听地址（默认读配置）")
    ap.add_argument("--port", type=int, default=None, help="覆盖监听端口（默认读配置，被占则自动换）")
    ap.add_argument("--no-window", action="store_true", help="只起后端、不开窗口（排查用）")
    ap.add_argument("--version", action="store_true", help="打印版本号后退出")
    a = ap.parse_args(argv)

    _ensure_std_streams()
    home, res = resolve_paths()
    _mkdirs_for_logs(home)

    log("=" * 68)
    log(f"ZHS Studio 桌面端启动 · frozen={getattr(sys, 'frozen', False)}")
    log(f"home={home}")
    log(f"resources={res}")
    log(f"python={sys.version.split()[0]}  exe={sys.executable}")

    if a.version:
        print(f"ZHS Studio desktop · ZHS Studio v{_app_version()}")
        return 0

    # ---- 1. 环境 + 配置 ----
    try:
        cfg, user_cfg = prepare_environment(home, res, a.config)
    except FileNotFoundError as e:
        log(f"配置错误：{e}")
        _msgbox(f"{e}", title=f"{APP_TITLE} — 配置错误")
        return 3
    except Exception as e:  # noqa: BLE001
        log(f"加载配置失败：{type(e).__name__}: {e}")
        log(traceback.format_exc().rstrip())
        _msgbox(f"加载配置失败：\n{type(e).__name__}: {e}", title=f"{APP_TITLE} — 配置错误")
        return 3

    log(f"config={user_cfg or '（无 config.json，使用默认值）'}")
    log(f"engine_home={cfg.get('engine_home') or '（空）'}")
    log(f"data_dir={cfg.get('data_dir')}  shots_dir={cfg.get('shots_dir')}")

    example_cfg = os.path.join(res, "config.example.json")
    bridge = Bridge(home=home, res=res, example_cfg=example_cfg, user_cfg=user_cfg)

    # ---- 2. import 后端（引擎缺失会在这里抛 EngineNotFound，别让它崩进程）----
    try:
        t0 = time.time()
        import app as backend_app            # noqa: F401  app.py 里模块级就在 load_engine
        log(f"后端模块导入完成（{time.time() - t0:.2f}s）")
    except Exception as e:  # noqa: BLE001
        err_name = type(e).__name__
        detail = f"{err_name}: {e}"
        log(f"后端导入失败 → {detail}")
        log(traceback.format_exc().rstrip())

        if a.no_window:
            print(detail)
            return 4

        kind = "engine" if err_name == "EngineNotFound" else "error"
        page_title = "缺少 fuckZHS 引擎" if kind == "engine" else "后端没能启动"
        page = build_error_page(
            kind=kind,
            title=page_title,
            detail=detail,
            home=home,
            user_cfg=user_cfg,
            engine_home=str(cfg.get("engine_home") or ""),
            cfg_path_shown=os.path.join(home, "config.json"),
        )
        ok, werr = show_window(f"{APP_TITLE} — {page_title}", html_text=page, bridge=bridge,
                               storage_path=os.path.join(home, "data", "webview"))
        if not ok:
            log(f"窗口打开失败：{werr}")
            _msgbox(f"WebView2 窗口打不开，需要安装 Microsoft Edge WebView2 Runtime。\n\n{werr}\n\n"
                    f"下载：https://developer.microsoft.com/microsoft-edge/webview2/",
                    title=f"{APP_TITLE} — 缺少 WebView2")
        log("提示页已关闭，退出")
        return 0

    # ---- 3. 起后端 ----
    bind_host, port, probe_host = pick_bind(cfg, a.host, a.port)
    backend = Backend(backend_app.app, bind_host, port)
    log(f"监听 {bind_host}:{port}（探活走 {probe_host}）")
    backend.start()

    if a.no_window:
        ok, detail = backend.wait_ready()
        log(f"探活：{'OK v' + str(detail) if ok else 'FAIL ' + str(detail)}")
        if not ok:
            return 5
        print(f"READY {backend.url}")
        try:
            while backend.thread and backend.thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            backend.stop()
        return 0

    ok, detail = backend.wait_ready()
    if not ok:
        log(f"后端探活失败：{detail}")
        page = build_error_page(
            kind="error",
            title="后端没能启动",
            detail=str(detail),
            home=home,
            user_cfg=user_cfg,
            engine_home=str(cfg.get("engine_home") or ""),
            cfg_path_shown=os.path.join(home, "config.json"),
            extra_html=(f'<div class="card"><h2>日志</h2><pre>'
                        f'{html.escape(_tail(_LOG_FILE))}</pre></div>'),
        )
        backend.stop()
        ok, werr = show_window(f"{APP_TITLE} — 后端没能启动", html_text=page, bridge=bridge,
                               storage_path=os.path.join(home, "data", "webview"))
        if not ok:
            log(f"窗口打开失败：{werr}")
            _msgbox(f"WebView2 窗口打不开。\n\n{werr}", title=f"{APP_TITLE} — 缺少 WebView2")
        return 5

    log(f"后端就绪 · v{detail} · {backend.url} · 冷启动 {time.time() - _PROC_START:.2f}s")

    # 手机端要连的时候，把地址写进日志（窗口标题保持规格要求的那一个）
    try:
        import zhs_config
        for u in zhs_config.lan_urls(bind_host, port):
            log(f"局域网可访问：{u}")
    except Exception:
        pass

    # ---- 4. 开窗口（主线程阻塞）----
    try:
        ok, werr = show_window(APP_TITLE, url=backend.url,
                               storage_path=os.path.join(home, "data", "webview"))
        if not ok:
            log(f"窗口打开失败：{werr}")
            _msgbox(f"WebView2 窗口打不开，已改用系统浏览器打开面板。\n\n{werr}\n\n"
                    f"若不想降级，请安装 WebView2 Runtime：\n"
                    f"https://developer.microsoft.com/microsoft-edge/webview2/",
                    title=f"{APP_TITLE} — WebView2 不可用")
            webbrowser.open(backend.url)
            log("已用系统浏览器打开；关掉本窗口（或结束进程）即退出")
            while backend.thread and backend.thread.is_alive():
                time.sleep(1)
    except KeyboardInterrupt:
        log("收到 Ctrl+C")
    finally:
        backend.stop()
        log(f"退出 · 总运行 {time.time() - _PROC_START:.2f}s")
    return 0


def _tail(path: str | None, n: int = 40) -> str:
    if not path or not os.path.isfile(path):
        return "（没有日志）"
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-n:])
    except Exception:
        return "（日志读取失败）"


def main() -> int:
    try:
        return run()
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 —— 最外层兜底，绝不带 traceback 对话框崩掉
        log(f"未捕获异常：{type(e).__name__}: {e}")
        log(traceback.format_exc().rstrip())
        _msgbox(f"ZHS Studio 启动失败：\n{type(e).__name__}: {e}\n\n日志：{_LOG_FILE}",
                title=f"{APP_TITLE} — 启动失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
