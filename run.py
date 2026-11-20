#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨平台启动入口（Windows / macOS / Linux 通用）。

    python run.py                 # 用 config.json（或默认值）里的 server.host/port 起服务
    python run.py --port 8780     # 临时换端口
    python run.py --host 0.0.0.0  # 临时允许局域网访问（手机/平板遥控用）
    python run.py --reload        # 开发模式（改代码自动重启）

想让手机/平板长期能连：在 config.json 里把 server.host 写成 0.0.0.0，
或者把 server.allow_lan 设成 true（等价于把 host 改成 0.0.0.0）。

Windows 上也可以直接用 start.ps1（它会读同一份 config.json，并在后台起服务 + 打开界面）。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import zhs_config  # noqa: E402

# 控制台里照旧用系统码页（中文 Windows 就是 GBK，交互式显示正常）；
# 但一旦 stdout 被重定向到文件/管道（start.ps1 的后台日志、桌面端启动器收集的日志），
# Python 3.12 仍按系统码页写盘，别的程序按 UTF-8 读就是一片乱码 —— 这里只在非 TTY 时改成 UTF-8。
if not sys.stdout.isatty():
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def main() -> int:
    cfg = zhs_config.load(here=HERE)

    ap = argparse.ArgumentParser(description="ZHS Studio 服务")
    ap.add_argument("--host", default=cfg["server"]["host"], help="监听地址（默认读配置：127.0.0.1）")
    ap.add_argument("--port", type=int, default=int(cfg["server"]["port"]), help="监听端口（默认读配置）")
    ap.add_argument("--reload", action="store_true", help="开发模式：代码改动自动重启")
    ap.add_argument("--log-level", default="warning", help="uvicorn 日志级别（默认 warning）")
    a = ap.parse_args()

    print(zhs_config.describe(cfg), flush=True)

    try:
        import uvicorn
    except ImportError:
        print("缺少 uvicorn：pip install -r requirements.txt", file=sys.stderr)
        return 2

    # 提示：服务默认只绑回环地址。**不要**把面板暴露到局域网/公网：
    # /api/cookie/grab 会写出登录态文件，且本地服务没有鉴权（见 docs/code-audit.md §2.3）。
    # 手机上只要「遥控 + 看进度」，够用；同网段不可信设备多的时候请把 allow_lan 关回去。
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"[警告] 正在监听非回环地址 {a.host}：本服务无鉴权，请确认网络环境可信。",
              file=sys.stderr)

    # 把「手机该填哪个地址」直接打出来 —— 让用户去 ipconfig 里找 IPv4 是没必要的仪式感
    urls = zhs_config.lan_urls(a.host, a.port)
    if urls:
        print("局域网可访问地址（手机端 / 平板端填这个）：", flush=True)
        for u in urls:
            print(f"  {u}", flush=True)
        if len(urls) > 1:
            # 装了虚拟机/WireGuard/VPN 的机器会多出几个网段，选错连不通
            print("  提示：多网段时选和手机同一个网段的那条（通常是 192.168.x.x）。", flush=True)
    else:
        print(f"仅本机可访问：http://127.0.0.1:{a.port}/", flush=True)
        print("（要用手机连：config.json 里 server.host 改 0.0.0.0，或设 server.allow_lan=true）",
              flush=True)

    try:
        uvicorn.run("app:app", host=a.host, port=a.port, reload=a.reload,
                    log_level=a.log_level, app_dir=HERE)
    except Exception as e:
        # 最常见的是引擎没配好（EngineNotFound），别把 traceback 埋掉
        print(f"启动失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
