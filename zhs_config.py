# -*- coding: utf-8 -*-
"""配置加载：内置默认值 + config.json + 环境变量覆盖。

优先级（后者覆盖前者）

    内置默认值  <  config.json  <  环境变量

配置文件位置

    1) 显式传入的 path
    2) 环境变量 ZHS_CONFIG 指向的文件
    3) 仓库根的 config.json（可选，不存在就全部用默认值 → 此时 engine_home 为空时无法启动，
       由 zhs_engine 给出「怎么装上游引擎」的指引）

环境变量

    ZHS_CONFIG        配置文件路径
    ZHS_ENGINE_HOME   引擎目录（旧名 FZ_HOME 仍兼容）
    ZHS_DATA_DIR      数据目录
    ZHS_HOST / ZHS_PORT 监听地址 / 端口

路径解析

    engine_home / data_dir / cookie_file / shots_dir / browser.profile_dir 里的相对路径
    一律相对**仓库根**（本文件所在目录）解析，这样从任何子目录启动都不会解析到别处。
    仓库根的绝对路径由调用方通过 here= 传入（默认取本文件所在目录）。
"""
import copy
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    # 外部引擎（VermiIIi0n/fuckZHS）的安装目录；留空 = 未配置
    "engine_home": "",
    "server": {"host": "127.0.0.1", "port": 8770},
    "cdp": {
        "host": "127.0.0.1",
        "port": 9222,
        "timeout_sec": 20,
        # 只抓这些域名的 cookie；学校自建站点/多品牌课程可追加
        "cookie_domain_filter": ["zhihuishu"],
    },
    "cookie_file": "data/cookies.json",
    "data_dir": "data",
    "shots_dir": "shots",
    "bus": {"capacity": 3000},
    "task": {
        "default_speed": 1.5,          # 上游 fucker 的知到默认倍速，>1.5 有风控风险
        "retry_delays": [2, 5],        # 每集最多 1 + 2 次尝试
        "sample_interval_sec": 10,     # 进度采样节流：≥10s 或 ≥5% 采一次
        "sample_interval_pct": 5,
    },
    "site": {
        "course_url_template": "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId={rac}",
        "home_url": "https://studyvideoh5.zhihuishu.com/",
    },
    "browser": {
        "kind": "edge",                       # edge | chrome | brave | 空=自动探测
        "path": "",                           # 留空=自动探测
        "profile_dir": "data/browser-profile",  # 独立 profile，不碰用户日常浏览器
        "profile_directory": "Default",
    },
}

# 需要按「相对仓库根」解析的路径键（用点号表示嵌套）
_PATH_KEYS = (
    "engine_home",
    "data_dir",
    "cookie_file",
    "shots_dir",
    "browser.profile_dir",
)


def _deep_merge(base: dict, patch: dict) -> dict:
    """把 patch 合并进 base 的副本（dict 递归合并，其余类型直接覆盖）。"""
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _get(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set(d: dict, dotted: str, value):
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _resolve(d: dict, dotted: str, here: str, *, may_be_empty: bool = True):
    v = _get(d, dotted)
    if v is None:
        return
    v = str(v).strip()
    if not v:
        if not may_be_empty:
            raise ValueError(f"配置项 {dotted} 不能为空")
        return
    v = os.path.expanduser(v)
    if not os.path.isabs(v):
        v = os.path.normpath(os.path.join(here, v))
    _set(d, dotted, v)


def find_config_file(here: str, path: str | None = None) -> str | None:
    if path:
        return os.path.abspath(os.path.expanduser(path))
    env = os.environ.get("ZHS_CONFIG", "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    cand = os.path.join(here, "config.json")
    return cand if os.path.isfile(cand) else None


def load(here: str | None = None, path: str | None = None) -> dict:
    """读出最终配置（dict）。任何未知键都会原样保留，方便自己加字段。"""
    here = os.path.abspath(here or HERE)
    cfg = copy.deepcopy(DEFAULTS)

    f = find_config_file(here, path)
    if f:
        if not os.path.isfile(f):
            raise FileNotFoundError(f"配置文件不存在：{f}")
        with open(f, encoding="utf-8") as fh:
            raw = json.load(fh) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"配置文件顶层必须是 JSON 对象：{f}")
        cfg = _deep_merge(cfg, raw)
        cfg["_config_file"] = f
    else:
        cfg["_config_file"] = None

    # ---- 环境变量覆盖 ----
    env_engine = (os.environ.get("ZHS_ENGINE_HOME")
                  or os.environ.get("FZ_HOME")     # 旧版变量名，保持向后兼容
                  or "").strip()
    if env_engine:
        cfg["engine_home"] = env_engine
    env_data = os.environ.get("ZHS_DATA_DIR", "").strip()
    if env_data:
        cfg["data_dir"] = env_data
    env_host = os.environ.get("ZHS_HOST", "").strip()
    if env_host:
        cfg["server"]["host"] = env_host
    env_port = os.environ.get("ZHS_PORT", "").strip()
    if env_port:
        cfg["server"]["port"] = int(env_port)

    # ---- 路径归一化 ----
    for k in _PATH_KEYS:
        _resolve(cfg, k, here)

    # ---- 类型兜底（配置文件是手写的，别让字符串把代码搞崩）----
    cfg["server"]["port"] = int(cfg["server"]["port"])
    cfg["cdp"]["port"] = int(cfg["cdp"]["port"])
    cfg["cdp"]["timeout_sec"] = float(cfg["cdp"]["timeout_sec"])
    cfg["bus"]["capacity"] = int(cfg["bus"]["capacity"])
    cfg["task"]["default_speed"] = float(cfg["task"]["default_speed"])
    cfg["task"]["sample_interval_sec"] = float(cfg["task"]["sample_interval_sec"])
    cfg["task"]["sample_interval_pct"] = float(cfg["task"]["sample_interval_pct"])
    cfg["task"]["retry_delays"] = [float(x) for x in (cfg["task"]["retry_delays"] or [])]

    return cfg


def describe(cfg: dict) -> str:
    """人类可读的一行摘要（启动时打印，方便确认读的是哪份配置）。"""
    src = cfg.get("_config_file") or "（无 config.json，使用默认值）"
    return (f"config: {src} | engine_home: {cfg.get('engine_home') or '未配置'} | "
            f"data_dir: {cfg.get('data_dir')} | "
            f"server: {cfg['server']['host']}:{cfg['server']['port']}")
