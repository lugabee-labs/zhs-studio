#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给 ZHS Studio 前端拍快照：连本机浏览器的 CDP（--remote-debugging-port，默认 9222），
打开/复用面板那个标签，按指定视口截图。

用法：
  python tools/shot.py                        # 默认 1680x1000，存到配置里的 shots_dir
  python tools/shot.py --width 1280 --height 900
  python tools/shot.py --port 9223 --tag after-fix
  python tools/shot.py --url http://127.0.0.1:8780/ --out D:/tmp/shots

CDP 地址/端口、面板地址、输出目录都从 config.json 读（cdp.host / cdp.port / server.port /
shots_dir），命令行参数优先。截图目录属于「不入库」目录，已在 .gitignore 覆盖。

输出：打印 PNG 的绝对路径（最后一行），供 read_image 直接读。
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import websocket  # noqa: E402

import zhs_config  # noqa: E402


def find_or_open(cdp_base: str, url: str) -> tuple:
    ver = json.load(urllib.request.urlopen(f"{cdp_base}/json/version", timeout=5))
    for t in json.load(urllib.request.urlopen(f"{cdp_base}/json/list", timeout=5)):
        if t.get("type") == "page" and url.split("://")[-1].split("/")[0] in t.get("url", ""):
            return t["webSocketDebuggerUrl"], t["id"]
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=20)
    ws.send(json.dumps({"id": 1, "method": "Target.createTarget", "params": {"url": url}}))
    tid = None
    for _ in range(40):
        m = json.loads(ws.recv())
        if m.get("id") == 1:
            tid = m["result"]["targetId"]
            break
    ws.close()
    time.sleep(3)
    for t in json.load(urllib.request.urlopen(f"{cdp_base}/json/list", timeout=5)):
        if t.get("id") == tid:
            return t["webSocketDebuggerUrl"], tid
    raise RuntimeError("无法创建标签页")


def main():
    cfg = zhs_config.load(here=str(ROOT))
    cdp_cfg = cfg["cdp"]
    default_url = f"http://{cfg['server']['host']}:{cfg['server']['port']}/"

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=default_url, help="面板地址（默认取 config.json 的 server）")
    # 旧版这里解析了 --port 却从未使用（CDP 常量写死 9222），是个真 bug —— 现在真的生效
    ap.add_argument("--port", type=int, default=int(cdp_cfg["port"]), help="CDP 调试端口")
    ap.add_argument("--cdp-host", default=cdp_cfg.get("host") or "127.0.0.1", help="CDP 主机")
    ap.add_argument("--width", type=int, default=1680)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--tag", default="")
    ap.add_argument("--wait", type=float, default=4.0)
    ap.add_argument("--out", default=cfg.get("shots_dir") or str(ROOT / "shots"),
                    help="输出目录（默认取 config.json 的 shots_dir）")
    a = ap.parse_args()

    cdp_base = f"http://{a.cdp_host}:{a.port}"
    wsurl, tid = find_or_open(cdp_base, a.url)
    p = websocket.create_connection(wsurl, timeout=30)
    mid = 0

    def call(method, **params):
        nonlocal mid
        mid += 1
        p.send(json.dumps({"id": mid, "method": method, "params": params}))
        for _ in range(200):
            m = json.loads(p.recv())
            if m.get("id") == mid:
                return m

    call("Page.enable")
    call("Emulation.setDeviceMetricsOverride", width=a.width, height=a.height,
         deviceScaleFactor=1, mobile=False)
    # 强制重载，保证拿到最新前端文件
    call("Page.reload", ignoreCache=True)
    time.sleep(a.wait)
    r = call("Page.captureScreenshot", format="png")
    p.close()

    out_dir = os.path.abspath(a.out)
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{a.width}x{a.height}" + (f"-{a.tag}" if a.tag else "") + ".png"
    out = os.path.join(out_dir, name)
    with open(out, "wb") as f:
        f.write(base64.b64decode(r["result"]["data"]))
    print(out)


if __name__ == "__main__":
    main()
