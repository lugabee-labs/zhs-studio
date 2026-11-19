# -*- coding: utf-8 -*-
"""
ZHS Studio — 智慧树（知到）共享课 可视化刷课面板

设计来源：
  * fuckZHS  —— API 层：课程/章节树、进度上报、弹题读服务端 result 直接作答、AI 考试
  * Autovisor —— 工程层：可停止的任务、限时、倍速、无人值守
  * 本机 CDP  —— 免扫码登录：从已登录的 Edge 直接抓 CASLOGC 会话

只在本机跑：python run.py     # 读 config.json 的 server.host / server.port（默认 127.0.0.1:8770）
"""
import json
import os
import re
import secrets
import html
import threading
import time
import urllib.parse
import urllib.request

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import zhs_config
from zhs_engine import load_engine

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = zhs_config.load(here=HERE)          # config.json（可选）+ 环境变量覆盖
COOKIE_FILE = CFG["cookie_file"]
LOCK = threading.RLock()
RUN_GATE = threading.Lock()   # /api/run 的 check-and-set 临界区

# ---------------------------------------------------------------- 引擎（外部依赖 fuckZHS）
# 引擎目录来自 config.json 的 engine_home（或环境变量 ZHS_ENGINE_HOME），
# 找不到时 zhs_engine 会抛出带安装指引的 EngineNotFound。
# 以前这里硬编码 `os.chdir(FZ_HOME)`：进程级改 CWD 会让所有相对路径错位，
# 而引擎的 getRealPath() 本来就按 __file__ 定位，不需要 chdir —— 已删除。
ENGINE = load_engine(CFG["engine_home"])
fzmod = ENGINE.module
Fucker = ENGINE.Fucker
CaptchaException = ENGINE.CaptchaException
TimeLimitExceeded = ENGINE.TimeLimitExceeded

# ---------------------------------------------------------------- 数据目录
DATA_DIR = CFG["data_dir"]
RUNS_DIR = os.path.join(DATA_DIR, "runs")
TASK_STATE_FILE = os.path.join(DATA_DIR, "task-state.json")
ANSWERS_LOG = os.path.join(DATA_DIR, "answers.jsonl")   # 弹题作答流水，一行一条，可审计
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")       # 每轮刷课一个档案目录


def _ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RUNS_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    # 登录态文件在 data/ 下（以前落在引擎目录里，升级/重装引擎会连带丢）
    os.makedirs(os.path.dirname(COOKIE_FILE) or DATA_DIR, exist_ok=True)


# ---------------------------------------------------------------- 运行档案（session）
# 目的：一轮刷课跑完之后，目录里留下能直接做分析的原始数据，而不是只有内存里的 3000 条事件。
# 结构：data/sessions/<run_id>/
#         meta.json        参数、起止、结果摘要、失败清单
#         events.jsonl     全量事件（play/progress/done/answer/error/system），一行一条
#         episodes.jsonl   每集一行（videoId/name/sec/elapsed/ok/answers/pct_start/pct_end）
#         answers.jsonl    本轮弹题明细
#         progress.csv     进度采样时序（节流：≥10s 或 ≥5% 才采一次）
SESSION = {
    "id": None,
    "dir": None,
    "lock": threading.Lock(),
    "last_sample_ts": 0.0,
    "last_sample_pct": -100.0,
    "episodes_ok": 0,
    "episodes_fail": 0,
}


def _append_text(path: str, line: str):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")
    except Exception:
        pass


def _append_jsonl(path: str, obj: dict):
    try:
        _append_text(path, json.dumps(obj, ensure_ascii=False))
    except Exception:
        pass


def session_file(name: str) -> str | None:
    d = SESSION.get("dir")
    return os.path.join(d, name) if d else None


def _session_open(rac: str, course: str | None, speed: float,
                  skip_done: bool, limit_min: int) -> str:
    _ensure_dirs()
    # 目录名只放时间戳 + 随机短串。课程 rac 属于账号线索，不进目录名：
    # 旧版是 str(rac)[:8]，会把 rac 前 8 位扩散到 /api/sessions 响应、events 首行与报告里。
    sid = time.strftime("%Y%m%d-%H%M%S") + "_" + secrets.token_hex(4)
    d = os.path.join(SESSIONS_DIR, sid)
    os.makedirs(d, exist_ok=True)
    with SESSION["lock"]:
        SESSION.update(id=sid, dir=d, last_sample_ts=0.0,
                       last_sample_pct=-100.0, episodes_ok=0, episodes_fail=0)
    _append_text(os.path.join(d, "progress.csv"), "ts,videoId,pct")
    try:
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({
                "run_id": sid,
                "rac": rac,
                "course": course,
                "params": {"speed": speed, "skip_done": skip_done, "limit_min": limit_min},
                "started_at": time.time(),
                "status": "running",
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return sid


def _session_close(status: str, extra: dict | None = None):
    d = SESSION.get("dir")
    if not d:
        return
    meta = {
        "run_id": SESSION.get("id"),
        "rac": TASK.get("rac"),
        "course": TASK.get("course"),
        "status": status,
        "started_at": TASK.get("started_at"),
        "finished_at": time.time(),
        "done_count": TASK.get("done_count"),
        "fail_count": TASK.get("fail_count"),
        "answer_count": TASK.get("answer_count"),
        "episodes_ok": SESSION.get("episodes_ok"),
        "episodes_fail": SESSION.get("episodes_fail"),
        "failed": ROUND.get("failed_list") or [],
        "completed_video_ids": sorted(ROUND.get("completed") or []),
    }
    if extra:
        meta.update(extra)
    try:
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ---------------------------------------------------------------- 事件总线
class Bus:
    def __init__(self, cap: int | None = None):
        self.items: list[dict] = []
        self.seq = 0
        self.cap = int(cap if cap is not None else CFG["bus"]["capacity"])
        self.lock = threading.Lock()

    def push(self, kind: str, msg: str, **extra) -> dict:
        with self.lock:
            self.seq += 1
            ev = {"id": self.seq, "t": time.time(), "kind": kind, "msg": msg}
            ev.update(extra)
            self.items.append(ev)
            if len(self.items) > self.cap:
                self.items = self.items[-self.cap:]
        # 归档到本轮 session（放锁外，别让文件 IO 卡住事件总线）
        p = session_file("events.jsonl")
        if p:
            _append_jsonl(p, ev)
        return ev

    def since(self, sid: int) -> list[dict]:
        with self.lock:
            return [e for e in self.items if e["id"] > sid]


BUS = Bus()

# ---------------------------------------------------------------- 任务状态
TASK = {
    "status": "idle",          # idle | running | stopping | done | error | captcha
    "rac": None,
    "course": None,
    "speed": CFG["task"]["default_speed"],
    "skip_done": True,
    "limit_min": 0,
    "stop": threading.Event(),
    "thread": None,
    "started_at": None,
    "finished_at": None,
    "video": None,             # {videoId,name,idx,total}
    "pct": 0.0,
    "done_count": 0,
    "fail_count": 0,
    "answer_count": 0,
    "error": None,
    "run_id": None,            # 本轮档案 id（data/sessions/<run_id>）
}

# 本轮追踪（跨 run 保持，直到新课程启动才重置）
ROUND = {
    "rac": None,
    "completed": set(),          # videoId → 本轮已完成
    "fail_counts": {},           # videoId → 本轮失败次数
    "failed_list": [],           # [{videoId, name, error, fail_count}]
}

# ---------------------------------------------------------------- 工具
def load_cookies() -> list | None:
    if not os.path.exists(COOKIE_FILE):
        return None
    try:
        with open(COOKIE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data or None
    except Exception:
        return None


def cookie_info() -> dict:
    """登录态摘要。**只回传 uuid 前 6 位**：完整 uuid 等同账号标识，前端只用它做展示。"""
    ck = load_cookies()
    if not ck:
        return {"ok": False, "count": 0, "uuid_prefix": None}
    uuid = None
    for c in ck:
        if c.get("name") == "CASLOGC":
            try:
                uuid = json.loads(urllib.parse.unquote_plus(c["value"]))["uuid"]
            except Exception:
                uuid = None
    return {"ok": uuid is not None, "count": len(ck),
            "uuid_prefix": (str(uuid)[:6] if uuid else None)}


def make_fucker(speed: float | None = None, limit: int = 0) -> Fucker:
    f = Fucker(speed=speed, limit=limit, tree_view=False, progressbar_view=False)
    ck = load_cookies()
    if not ck:
        raise RuntimeError("没有 cookies.json，先抓一次登录态")
    f.cookies = ck
    return f


def grab_cookies(port: int | None = None) -> dict:
    """从本机带 --remote-debugging-port 的浏览器直接取全部 cookie（含 HttpOnly）

    CDP 地址/端口、抓取超时、域名过滤都来自 config.json 的 cdp 段。
    """
    import websocket

    cdp = CFG["cdp"]
    port = int(port or cdp["port"])
    host = cdp.get("host") or "127.0.0.1"
    timeout = float(cdp.get("timeout_sec") or 20)
    filters = [str(x).lower() for x in (cdp.get("cookie_domain_filter") or [])]

    ver = json.load(urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=5))
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=timeout)
    ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
    resp = None
    for _ in range(200):
        m = json.loads(ws.recv())
        if m.get("id") == 1:
            resp = m
            break
    ws.close()
    if not resp or "result" not in resp:
        raise RuntimeError(f"Storage.getCookies 失败: {resp}")
    raw = [c for c in resp["result"]["cookies"]
           if any(f in c.get("domain", "").lower() for f in filters)] if filters \
        else list(resp["result"]["cookies"])
    if not raw:
        raise RuntimeError("浏览器里没有目标站点的 cookie —— 先在那个浏览器登录一次")
    out = []
    for c in raw:
        out.append({
            "name": c["name"], "value": c["value"], "domain": c.get("domain", ""),
            "port": None, "path": c.get("path", "/"),
            "expires": None if c.get("session") else int(c.get("expires", 0)),
            "secure": bool(c.get("secure")),
            "rest": {"HttpOnly": True} if c.get("httpOnly") else {},
            "version": 0, "comment": None, "comment_url": None,
            "rfc2109": False, "discard": bool(c.get("session")),
        })
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    try:
        # 注意：POSIX 上这是 0600（只有自己能读）；**Windows 上只改只读位，等于空操作**。
        # Windows 想真的收紧权限得用 ACL：
        #   icacls data\cookies.json /inheritance:r /grant:r "%USERNAME%":F
        os.chmod(COOKIE_FILE, 0o600)
    except Exception:
        pass
    return cookie_info()


# ---------------------------------------------------------------- 状态持久化
def save_task_state():
    """将当前任务状态写入 data/task-state.json，实现断点续刷。"""
    try:
        _ensure_dirs()
        state = {
            "rac": TASK["rac"],
            "course": TASK["course"],
            "status": TASK["status"],
            "started_at": TASK["started_at"],
            "finished_at": TASK["finished_at"],
            "completed_video_ids": sorted(ROUND["completed"]),
            "failed": ROUND["failed_list"],
            "answer_count": TASK["answer_count"],
            "done_count": TASK["done_count"],
            "fail_count": TASK["fail_count"],
        }
        with open(TASK_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_task_state() -> dict | None:
    try:
        if os.path.exists(TASK_STATE_FILE):
            with open(TASK_STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def write_episode_stat(video_id: str, name: str, sec: int, elapsed: float,
                       ok: bool, answers: int,
                       pct_start: float | None = None, pct_end: float | None = None,
                       reason: str | None = None):
    """每集落两份：data/runs/*.json（单集文件）+ 本轮 sessions/<id>/episodes.jsonl（汇总行）

    reason 说明这一集为什么没跑完：
      done        正常播完
      stopped     用户点了「停止」（或 stop_event 触发）
      captcha     撞验证码停的
      limit       单课限时到点（理论上不写集记录，仅兜底）
      error       真失败（接口报错、数据异常）
      interrupted 服务进程被杀 / 重启，没有机会写记录时由启动恢复补的标记
    """
    try:
        _ensure_dirs()
        ts = time.strftime("%Y%m%d-%H%M%S")
        fn = f"{ts}_{video_id}.json"
        stat = {
            "videoId": video_id,
            "name": name,
            "sec": sec,
            "elapsed": round(elapsed, 2),
            "ok": ok,
            "answers": answers,
            "reason": reason or ("done" if ok else "error"),
            "pct_start": pct_start,
            "pct_end": pct_end,
            "at": time.time(),
        }
        with open(os.path.join(RUNS_DIR, fn), "w", encoding="utf-8") as f:
            json.dump(stat, f, ensure_ascii=False, indent=2)
        # 汇总到本轮档案
        ep = session_file("episodes.jsonl")
        if ep:
            row = dict(stat)
            row["course"] = TASK.get("course")
            _append_jsonl(ep, row)
            with SESSION["lock"]:
                if ok:
                    SESSION["episodes_ok"] += 1
                else:
                    SESSION["episodes_fail"] += 1
    except Exception:
        pass


# 进程重启后把持久化进度回填到内存态，否则 /api/course 的 round_done 会全是 false，
# 界面上「本轮已完成」的绿标就丢了（服务端 done_flag 仍在，不受影响）。
def _restore_round():
    p = load_task_state()
    if not p or not p.get("rac"):
        return
    ROUND.update(
        rac=p["rac"],
        completed=set(str(v) for v in p.get("completed_video_ids", [])),
        fail_counts={i["videoId"]: i.get("fail_count", 1)
                     for i in p.get("failed", []) if i.get("videoId")},
        failed_list=list(p.get("failed", [])),
    )


_restore_round()


def _mark_interrupted_sessions():
    """启动时把上次没正常收尾的档案标成 interrupted。

    进程被 kill / 崩溃时来不及写 meta，只能在这里补标记——否则复盘报告会把
    「服务重启打断」误判成业务失败（这两件事的处理优先级完全不同）。
    """
    try:
        _ensure_dirs()
        for name in os.listdir(SESSIONS_DIR):
            d = os.path.join(SESSIONS_DIR, name)
            mp = os.path.join(d, "meta.json")
            if not (os.path.isdir(d) and os.path.exists(mp)):
                continue
            try:
                with open(mp, encoding="utf-8") as f:
                    m = json.load(f)
            except Exception:
                continue
            if m.get("status") != "running":
                continue
            m["status"] = "interrupted"
            m["interrupted_at"] = time.time()
            m["closed_by"] = "startup-recovery"
            m["note"] = "服务进程在任务结束前退出（重启或崩溃），这一轮不是业务失败"
            try:
                with open(mp, "w", encoding="utf-8") as f:
                    json.dump(m, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
    except Exception:
        pass


_mark_interrupted_sessions()


def build_tree(f: Fucker, rac: str, force: bool = False) -> dict:
    ctx = f.getZhidaoContext(rac, force=force)
    ci = ctx.course.courseInfo
    out = {
        "rac": rac,
        "name": ci.get("name") or ci.get("enName") or rac,
        "courseId": ctx.chapters.courseId,
        "chapters": [],
    }
    total_sec = done_sec = 0
    _round_match = ROUND["rac"] == rac
    for ch in ctx.chapters.videoChapterDtos:
        c = {
            "id": str(ch.id),
            "name": ch.name,
            "isPass": int(ch.isPass or 0),
            "exam": bool(ch.get("studentExamDto")),
            "videos": [],
        }
        for les in ch.videoLessons:
            for v in les.videoSmallLessons:
                sec = int(v.videoSec or 0)
                done = int(v.studyTotalTime or 0)
                total_sec += sec
                done_sec += min(done, sec) if int(v.watchState or 0) == 1 else 0
                _vid = str(v.videoId)
                c["videos"].append({
                    "videoId": _vid,
                    "call_id": v.videoId,   # API 原始类型（int），调用 fuckZhidaoVideo 必须用它
                    "lessonId": str(les.id),
                    "name": v.name,
                    "lesson": les.name,
                    "sec": sec,
                    "done": done,
                    "pct": round(min(100.0, done / sec * 100), 1) if sec else 0.0,
                    "done_flag": int(v.watchState or 0) == 1,
                    "round_done": _round_match and _vid in ROUND["completed"],
                    "fail_count": ROUND["fail_counts"].get(_vid, 0) if _round_match else 0,
                })
        out["chapters"].append(c)
    vids = [v for c in out["chapters"] for v in c["videos"]]
    out["stats"] = {
        "videos": len(vids),
        "finished": sum(1 for v in vids if v["done_flag"]),
        "total_sec": total_sec,
        "done_sec": done_sec,
        "pct": round(done_sec / total_sec * 100, 1) if total_sec else 0.0,
    }
    return out


# ---------------------------------------------------------------- 运行时钩子
# 说明：这里仍然用「替换上游函数引用」的方式接管进度与作答（上游没有提供回调注入点）。
# 已知脆点：靠模块级 progressBar 与 Fucker.answerZhidao 这两个名字，上游改名即静默失效。
# 彻底做法是在 zhs_engine 里做 ZhsFucker 子类 + 显式回调注入（见 docs/code-audit.md §3.1）。
_orig_answer = Fucker.answerZhidao


def _hooked_progress(iteration, total, prefix="", suffix="", decimals=1,
                    length=None, fill="#", progressbar_view=True):
    if TASK["stop"].is_set():
        raise KeyboardInterrupt("user stop")
    try:
        # fucker 的防检测暂停也走 progressBar（s=60-pause, e=60，prefix="pause a minute"），
        # 那是倒计时不是学习进度，混进来会让前端 pct 从 1.7 突跳到 100。
        if str(prefix or "").startswith("pause"):
            return None
        pct = round(iteration / total * 100, 1) if total else 0.0
        if abs(pct - TASK["pct"]) >= 0.4:
            TASK["pct"] = pct
            BUS.push("progress", prefix or "", pct=pct)
        # 进度采样：给后续分析留时间序列（原始事件每秒一条太多，这里节流；
        # 阈值来自 config.json 的 task.sample_interval_sec / sample_interval_pct）
        d = SESSION.get("dir")
        if d:
            now = time.time()
            if (now - SESSION["last_sample_ts"] >= CFG["task"]["sample_interval_sec"]
                    or abs(pct - SESSION["last_sample_pct"]) >= CFG["task"]["sample_interval_pct"]):
                SESSION["last_sample_ts"] = now
                SESSION["last_sample_pct"] = pct
                vid = (TASK.get("video") or {}).get("videoId")
                _append_text(os.path.join(d, "progress.csv"), f"{round(now, 1)},{vid},{pct}")
    except Exception:
        pass
    return None  # 不再往 stdout 打进度条


def _append_answer_log(rec: dict):
    try:
        _ensure_dirs()
        _append_jsonl(ANSWERS_LOG, rec)
        # 同一轮里再存一份到本轮档案，方便按 run 归因
        p = session_file("answers.jsonl")
        if p:
            _append_jsonl(p, rec)
    except Exception:
        pass


def _hooked_answer(self, q):
    result = _orig_answer(self, q)
    try:
        opts = []
        for o in (q.questionOptions or []):
            opts.append({
                "id": str(o.get("id")),
                "text": str(o.get("content") or "").strip()[:160],
                "correct": o.get("result") == "1",
            })
        # 题目字段名各版本不一致，按候选顺序取第一个非空的
        # 实测共享课弹题的题干在 `name`（`questionContent` 是空串），所以 name 必须在内
        stem = ""
        for k in ("questionContent", "name", "content", "questionName",
                  "questionTitle", "topicContent", "title", "question"):
            v = q.get(k)
            if v:
                stem = str(v).strip()[:240]
                break
        # 题干里带 <br/> <div> &nbsp; 这类标签/实体，落盘前洗干净
        # 注意 html.unescape("&nbsp;") 得到的是 U+00A0，要再统一成普通空格并压掉多余空白
        if stem:
            stem = html.unescape(re.sub(r"<[^>]+>", "", stem))
            stem = re.sub(r"\s+", " ", stem.replace("\xa0", " ")).strip()[:240]
        chosen_ids = {x for x in str(result or "").split(",") if x}
        vid = (TASK.get("video") or {}).get("videoId")
        rec = {
            "t": time.time(),
            "videoId": vid,
            "questionId": str(q.get("questionId") or q.get("id") or ""),
            "stem": stem,
            "options": opts,
            "chosen": str(result or ""),
            "picked": [o["text"] for o in opts if o["id"] in chosen_ids],
            "source": "server_result",
            "q_keys": sorted(list(q.keys()))[:24],   # 首次触发时用来确认字段名
        }
        TASK["answer_count"] += 1
        _append_answer_log(rec)
        BUS.push("answer", stem or "弹题已作答", options=opts, chosen=rec["chosen"],
                 picked=rec["picked"], questionId=rec["questionId"])
    except Exception:
        pass
    return result


fzmod.progressBar = _hooked_progress
Fucker.answerZhidao = _hooked_answer


# ---------------------------------------------------------------- 单集重试
# 每集最多 1 + len(delays) 次尝试；间隔来自 config.json 的 task.retry_delays
_RETRY_DELAYS = list(CFG["task"]["retry_delays"])


def _play_with_retry(f: Fucker, rac: str, video_id: str, name: str) -> bool:
    """播一集，失败自动重试 2 次（间隔 2s / 5s），仍失败抛异常。

    KeyboardInterrupt / CaptchaException / TimeLimitExceeded 不重试、直接向上传播。
    """
    last_err: Exception | None = None
    total_attempts = 1 + len(_RETRY_DELAYS)
    for attempt in range(total_attempts):
        if attempt > 0:
            delay = _RETRY_DELAYS[attempt - 1]
            BUS.push("error", f"[重试 {attempt}/{len(_RETRY_DELAYS)}] {name}，等待 {delay}s")
            time.sleep(delay)
            if TASK["stop"].is_set():
                raise KeyboardInterrupt("user stop")
        try:
            with LOCK:
                f.fuckZhidaoVideo(rac, video_id)
            return True
        except (KeyboardInterrupt, CaptchaException, TimeLimitExceeded):
            raise
        except (AttributeError, TypeError, KeyError, ValueError) as e:
            # 数据/参数类错误重试无意义，直接失败，别让 78 集 ×3 次白等
            BUS.push("error", f"{name} 参数或数据错误，不重试: {e}")
            raise
        except Exception as e:
            last_err = e
            if attempt < len(_RETRY_DELAYS):
                BUS.push("error", f"{name} 第 {attempt + 1} 次失败: {e}")
            else:
                BUS.push("error", f"{name} 重试 {len(_RETRY_DELAYS)} 次后仍失败: {e}")
    raise last_err  # type: ignore[misc]


# ---------------------------------------------------------------- 任务主体
def _run(rac: str, speed: float, skip_done: bool, limit_min: int):
    # ---- 初始化本轮状态（同课程续跑则保留已有进度，否则重置） ----
    if ROUND["rac"] != rac:
        ROUND.update(rac=rac, completed=set(), fail_counts={}, failed_list=[])
        # 从持久化恢复上次中断的进度
        persisted = load_task_state()
        if persisted and persisted.get("rac") == rac:
            for vid in persisted.get("completed_video_ids", []):
                ROUND["completed"].add(vid)
            for item in persisted.get("failed", []):
                vid = item.get("videoId")
                if vid:
                    ROUND["fail_counts"][vid] = item.get("fail_count", 1)
            ROUND["failed_list"] = list(persisted.get("failed", []))
            BUS.push("system", f"从上次中断恢复：已完成 {len(ROUND['completed'])} 集")

    TASK.update(status="running", rac=rac, speed=speed, skip_done=skip_done,
                limit_min=limit_min, started_at=time.time(), finished_at=None,
                pct=0.0, done_count=0, fail_count=0, answer_count=0, error=None)
    run_id = _session_open(rac, None, speed, skip_done, limit_min)
    TASK["run_id"] = run_id
    BUS.push("system", f"任务启动（档案 {run_id}）")
    save_task_state()
    try:
        with LOCK:
            f = make_fucker(speed=speed, limit=limit_min)
            tree = build_tree(f, rac, force=True)
        TASK["course"] = tree["name"]
        # 课程名要到这步才知道，把 meta 补全（否则档案里 course 一直是 null）
        if SESSION.get("dir"):
            try:
                mp = os.path.join(SESSION["dir"], "meta.json")
                with open(mp, encoding="utf-8") as mf:
                    _m = json.load(mf)
                _m["course"] = tree["name"]
                _m["course_id"] = tree.get("courseId")
                _m["episodes_total"] = tree["stats"]["videos"]
                _m["pct_before"] = tree["stats"]["pct"]
                with open(mp, "w", encoding="utf-8") as mf:
                    json.dump(_m, mf, ensure_ascii=False, indent=2)
            except Exception:
                pass
        vids = [v for c in tree["chapters"] for v in c["videos"]]
        if skip_done:
            vids = [v for v in vids if not v["done_flag"] and v["videoId"] not in ROUND["completed"]]
        else:
            vids = [v for v in vids if v["videoId"] not in ROUND["completed"]]
        BUS.push("system", f"待刷 {len(vids)} 集 / 全课程 {tree['stats']['videos']} 集")
        # ctx.videos 的键是 API 原始类型（int），面板传的是 str。
        # 不做归一化的话 fuckZhidaoVideo 里 ctx.videos[video_id] 会回退成空 dict，
        # 接着读 .studyTotalTime 就抛 'dict' object has no attribute 'studyTotalTime'。
        with LOCK:
            _ctx = f.getZhidaoContext(rac)
        _keymap = {str(k): k for k in _ctx.videos.keys()}
        for i, v in enumerate(vids, 1):
            if TASK["stop"].is_set():
                break
            _vid = v["videoId"]
            TASK["video"] = {"videoId": _vid, "name": v["name"], "idx": i, "total": len(vids)}
            TASK["pct"] = v["pct"]
            BUS.push("play", f"[{i}/{len(vids)}] {v['name']}", videoId=_vid)
            _answers_before = TASK["answer_count"]
            _t0 = time.time()
            try:
                _call_id = _keymap.get(str(_vid), v.get("call_id", _vid))
                _play_with_retry(f, rac, _call_id, v["name"])
                _elapsed = time.time() - _t0
                _ans = TASK["answer_count"] - _answers_before
                TASK["done_count"] += 1
                ROUND["completed"].add(_vid)
                BUS.push("done", f"完成 {v['name']}", videoId=_vid)
                write_episode_stat(_vid, v["name"], v["sec"], _elapsed, True, _ans,
                                   pct_start=v.get("pct"), pct_end=100.0, reason="done")
                save_task_state()
            except KeyboardInterrupt:
                BUS.push("system", "已按要求停止")
                _elapsed = time.time() - _t0
                _ans = TASK["answer_count"] - _answers_before
                write_episode_stat(_vid, v["name"], v["sec"], _elapsed, False, _ans,
                                   pct_start=v.get("pct"), pct_end=TASK.get("pct"),
                                   reason="stopped")
                save_task_state()
                break
            except CaptchaException:
                TASK["status"] = "captcha"
                BUS.push("error", "触发验证码，任务暂停：去浏览器过一下验证再重开")
                write_episode_stat(_vid, v["name"], v["sec"], time.time() - _t0, False,
                                   TASK["answer_count"] - _answers_before,
                                   pct_start=v.get("pct"), pct_end=TASK.get("pct"),
                                   reason="captcha")
                save_task_state()
                return
            except TimeLimitExceeded:
                BUS.push("system", f"达到单课限时 {limit_min} 分钟，收工")
                # 这一集是被 _checkTimeLimit 在开播前挡下的，还没真正播过，
                # 写 ok=false 统计会污染 data/runs（R3）。只落任务级状态。
                save_task_state()
                break
            except Exception as e:
                _elapsed = time.time() - _t0
                _ans = TASK["answer_count"] - _answers_before
                TASK["fail_count"] += 1
                ROUND["fail_counts"][_vid] = ROUND["fail_counts"].get(_vid, 0) + 1
                ROUND["failed_list"] = [
                    x for x in ROUND["failed_list"] if x.get("videoId") != _vid
                ]
                ROUND["failed_list"].append({
                    "videoId": _vid, "name": v["name"],
                    "error": str(e), "fail_count": ROUND["fail_counts"][_vid],
                })
                BUS.push("error", f"{v['name']} 最终失败: {e}")
                write_episode_stat(_vid, v["name"], v["sec"], _elapsed, False, _ans,
                                   pct_start=v.get("pct"), pct_end=TASK.get("pct"),
                                   reason="error")
                save_task_state()
        TASK["status"] = "done"
        BUS.push("system", f"收工：成功 {TASK['done_count']} / 失败 {TASK['fail_count']} / 弹题 {TASK['answer_count']}")
    except Exception as e:
        TASK["status"] = "error"
        TASK["error"] = str(e)
        BUS.push("error", f"任务异常: {e}")
    finally:
        TASK["finished_at"] = time.time()
        TASK["video"] = None
        _session_close(TASK.get("status") or "unknown")
        save_task_state()


# ---------------------------------------------------------------- API
app = FastAPI(title="ZHS Studio")


class RunReq(BaseModel):
    rac: str
    # 留空（null）表示用 config.json 的 task.default_speed
    speed: float | None = None
    skip_done: bool = True
    limit_min: int = 0


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "web", "index.html"))


@app.get("/api/state")
def state():
    st = {k: v for k, v in TASK.items() if k not in ("stop", "thread")}
    st["cookie"] = cookie_info()
    st["now"] = time.time()
    st["bus_seq"] = BUS.seq
    st["persisted"] = load_task_state()
    # 站点地址与默认倍速由后端下发，前端不要各写一份（旧版 index.html 写死了两处 1.5 与站点 URL）
    st["site"] = dict(CFG["site"])
    st["defaults"] = {"speed": CFG["task"]["default_speed"]}
    return st


@app.get("/api/events")
def events(since: int = 0):
    return {"seq": BUS.seq, "items": BUS.since(since)}


@app.get("/api/answers")
def answers(limit: int = 50):
    """弹题作答流水（data/answers.jsonl），最新在前"""
    out: list[dict] = []
    try:
        if os.path.exists(ANSWERS_LOG):
            with open(ANSWERS_LOG, encoding="utf-8") as f:
                for ln in f.readlines()[-max(1, min(limit, 500)):]:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        out.append(json.loads(ln))
                    except Exception:
                        pass
    except Exception:
        pass
    return {"ok": True, "total": len(out), "items": out[::-1]}


@app.post("/api/cookie/grab")
def api_grab(port: int | None = None):
    """抓登录态并写盘。port 留空则用 config.json 的 cdp.port。

    安全提示：本接口**没有鉴权**，只应在本机回环地址上使用（默认 127.0.0.1）。
    """
    try:
        info = grab_cookies(port)
        # 注意：cookie_info() 只回传 uuid_prefix（完整 uuid 不出服务端），这里别再取 info["uuid"]
        BUS.push("system", f"已抓取登录态：{info['count']} 条 cookie，uuid={str(info.get('uuid_prefix') or '')[:6]}***")
        return {"ok": True, "cookie": info}
    except Exception as e:
        BUS.push("error", f"抓 cookie 失败: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.get("/api/courses")
def courses(refresh: bool = True):
    try:
        # 不加 LOCK：只读接口每次新建独立 Fucker，与 run 循环无共享状态。
        # 加 LOCK 会被 fuckZhidaoVideo 长时间持锁卡死（前端实测 15s+ 超时）。
        f = make_fucker()
        if refresh:
            f.courses.zhidao = None
        ls = f.getZhidaoList()
        out = []
        for c in ls:
            out.append({
                "rac": c.secret,
                "name": c.courseName,
                "teacher": c.get("teacherName"),
                "school": c.get("schoolName"),
                "img": c.get("courseImg"),
                "progress_text": c.get("progress"),
                "last_lesson": c.get("lessonName"),
                "status": c.get("courseStatus"),
            })
        return {"ok": True, "courses": out}
    except Exception as e:
        BUS.push("error", f"拉课程列表失败: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.get("/api/course/{rac}")
def course(rac: str, refresh: bool = True):
    try:
        # 不加 LOCK：同 /api/courses，独立 Fucker 实例，避免被 run 循环锁卡死
        f = make_fucker()
        tree = build_tree(f, rac, force=refresh)
        return {"ok": True, "tree": tree}
    except Exception as e:
        BUS.push("error", f"拉课程详情失败: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


def _count_lines(path: str) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


@app.get("/api/sessions")
def sessions(limit: int = 30):
    """历史运行档案（data/sessions/），最新在前"""
    out = []
    try:
        _ensure_dirs()
        names = sorted([d for d in os.listdir(SESSIONS_DIR)
                        if os.path.isdir(os.path.join(SESSIONS_DIR, d))],
                       reverse=True)[:max(1, min(limit, 200))]
        for name in names:
            d = os.path.join(SESSIONS_DIR, name)
            meta = {}
            try:
                with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                pass
            ev = os.path.join(d, "events.jsonl")
            ep = os.path.join(d, "episodes.jsonl")
            out.append({
                "run_id": name,
                "course": meta.get("course"),
                "rac": meta.get("rac"),
                "status": meta.get("status"),
                "started_at": meta.get("started_at"),
                "finished_at": meta.get("finished_at"),
                "done_count": meta.get("done_count"),
                "fail_count": meta.get("fail_count"),
                "answer_count": meta.get("answer_count"),
                "episodes_ok": meta.get("episodes_ok"),
                "episodes_fail": meta.get("episodes_fail"),
                "events_lines": _count_lines(ev),
                "episodes_lines": _count_lines(ep),
            })
        return {"ok": True, "sessions": out, "current": SESSION.get("id")}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.get("/api/session/{run_id}")
def session_detail(run_id: str, events: int = 200, episodes: int = 500):
    d = os.path.join(SESSIONS_DIR, os.path.basename(run_id))
    if not os.path.isdir(d):
        return JSONResponse({"ok": False, "error": "no such run"}, status_code=404)

    def _read(name: str, n: int | None = None) -> list:
        p = os.path.join(d, name)
        rows: list = []
        if not os.path.exists(p):
            return rows
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()
        if n:
            lines = lines[-max(1, n):]
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
        return rows

    meta = {}
    try:
        with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        pass
    return {"ok": True, "meta": meta,
            "episodes": _read("episodes.jsonl", episodes),
            "events": _read("events.jsonl", events),
            "answers": _read("answers.jsonl")}


@app.post("/api/run")
def run(req: RunReq):
    with RUN_GATE:
        if TASK["status"] in ("running", "stopping"):
            return JSONResponse({"ok": False, "error": "已有任务在跑"}, status_code=409)
        _speed = float(req.speed) if req.speed is not None else float(CFG["task"]["default_speed"])
        # 先同步占位再起线程，否则并发请求会在 worker 置 running 之前都通过检查（TOCTOU）
        TASK["status"] = "running"
        TASK["stop"].clear()
        t = threading.Thread(target=_run, args=(req.rac, _speed, req.skip_done, req.limit_min),
                             daemon=True, name="zhs-worker")
        TASK["thread"] = t
        t.start()
    return {"ok": True}


@app.post("/api/stop")
def stop():
    TASK["stop"].set()
    if TASK["status"] == "running":
        TASK["status"] = "stopping"
    BUS.push("system", "收到停止请求")
    return {"ok": True}
