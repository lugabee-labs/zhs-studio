#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨平台启动入口（Windows / macOS / Linux 通用）。

    python run.py                 # 用 config.json（或默认值）里的 server.host/port 起服务
    python run.py --port 8780     # 临时换端口
    python run.py --reload        # 开发模式（改代码自动重启）

Windows 上也可以直接用 start.ps1（它会读同一份 config.json，并在后台起服务 + 打开界面）。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import zhs_config  # noqa: E402


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
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"[警告] 正在监听非回环地址 {a.host}：本服务无鉴权，请确认网络环境可信。",
              file=sys.stderr)

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
