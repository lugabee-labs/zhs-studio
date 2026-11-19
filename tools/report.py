#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 data/sessions/ 下的运行档案汇总成一份可读报告，用于刷完之后做迭代。

用法：
    python tools/report.py                    # 打印到终端（markdown）
    python tools/report.py --json             # 输出 JSON（喂给别的脚本）
    python tools/report.py -o data/report.md  # 写文件

读取的档案结构（每轮一个目录）：
    data/sessions/<run_id>/meta.json        参数 / 起止 / 结果摘要 / 失败清单
                          events.jsonl      全量事件
                          episodes.jsonl    每集一行
                          answers.jsonl     弹题明细
                          progress.csv      进度采样时序
"""
import argparse
import json
import os
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import zhs_config  # noqa: E402  挂在仓库根的配置加载器

# 档案目录从 config.json 的 data_dir 推导（默认 <repo>/data/sessions）
SESSIONS = os.path.join(zhs_config.load(here=ROOT)["data_dir"], "sessions")


def read_jsonl(path, limit=None):
    rows = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    pass
    except Exception:
        pass
    if limit:
        rows = rows[-limit:]
    return rows


def read_meta(d):
    try:
        with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def read_progress(d):
    p = os.path.join(d, "progress.csv")
    pts = []
    if not os.path.exists(p):
        return pts
    try:
        with open(p, encoding="utf-8") as f:
            next(f, None)  # header
            for ln in f:
                parts = ln.strip().split(",")
                if len(parts) >= 3:
                    try:
                        pts.append({"ts": float(parts[0]), "videoId": parts[1], "pct": float(parts[2])})
                    except ValueError:
                        pass
    except Exception:
        pass
    return pts


def load_session(d):
    name = os.path.basename(d)
    meta = read_meta(d)
    episodes = read_jsonl(os.path.join(d, "episodes.jsonl"))
    answers = read_jsonl(os.path.join(d, "answers.jsonl"))
    events = read_jsonl(os.path.join(d, "events.jsonl"))
    progress = read_progress(d)

    started = meta.get("started_at")
    finished = meta.get("finished_at")
    dur = (finished - started) if (started and finished) else None

    ok_eps = [e for e in episodes if e.get("ok")]
    fail_eps = [e for e in episodes if not e.get("ok")]
    elapsed_total = sum(float(e.get("elapsed") or 0) for e in ok_eps)

    # 未完成的原因要分开看：真失败（error）才需要排查，
    # stopped/captcha/interrupted 是我方或风控主动停下的，不代表链路有问题
    def _reason(e):
        # 旧记录（reason 字段引入之前写的）不要默认当成真失败，
        # 否则「我主动停止的」会被读成「链路有 bug」
        return e.get("reason") or ("done" if e.get("ok") else "unknown")

    hard_fail = [e for e in fail_eps if _reason(e) == "error"]
    soft_stop = [e for e in fail_eps if _reason(e) != "error"]
    reason_counts = Counter(_reason(e) for e in episodes)

    # 进度采样按集聚合：跨集时 pct 会归零，直接取首末会得出「86% → 3%」这种误导性区间
    by_vid = {}
    for p in progress:
        by_vid.setdefault(str(p.get("videoId")), []).append(p["pct"])
    progress_by_video = {k: {"from": v[0], "to": v[-1], "points": len(v)}
                         for k, v in by_vid.items()}

    return {
        "run_id": name,
        "course": meta.get("course"),
        "rac": meta.get("rac"),
        "status": meta.get("status"),
        "started_at": started,
        "finished_at": finished,
        "duration": dur,
        "episodes_total": meta.get("episodes_total"),
        "pct_before": meta.get("pct_before"),
        "done_count": meta.get("done_count"),
        "fail_count": meta.get("fail_count"),
        "answer_count": meta.get("answer_count"),
        "episodes_ok": len(ok_eps),
        "episodes_fail": len(fail_eps),
        "hard_fail": len(hard_fail),
        "soft_stop": len(soft_stop),
        "reason_counts": dict(reason_counts),
        "avg_episode_sec": round(elapsed_total / len(ok_eps), 1) if ok_eps else None,
        "speed": (meta.get("params") or {}).get("speed"),
        "failed_list": meta.get("failed") or [],
        "answers": answers,
        "episodes": episodes,
        "events_count": len(events),
        "progress_points": len(progress),
        "progress_by_video": progress_by_video,
    }


def fmt_ts(v):
    if not v:
        return "—"
    return time.strftime("%m-%d %H:%M:%S", time.localtime(v))


def fmt_dur(s):
    if s is None:
        return "—"
    s = int(s)
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    return f"{h}h{m:02d}m" if h else (f"{m}m{x:02d}s" if m else f"{x}s")


def render(sessions):
    out = []
    out.append(f"# ZHS Studio 运行报告")
    out.append("")
    out.append(f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}　档案目录：`data/sessions/`")
    out.append("")

    total_ok = sum(s["episodes_ok"] for s in sessions)
    total_hard = sum(s["hard_fail"] for s in sessions)
    total_soft = sum(s["soft_stop"] for s in sessions)
    total_ans = sum(len(s["answers"]) for s in sessions)
    out.append(f"共 **{len(sessions)}** 轮运行，完成 **{total_ok}** 集、"
               f"真失败 **{total_hard}** 集、中断 **{total_soft}** 集、"
               f"弹题作答 **{total_ans}** 次。")
    out.append("")

    out.append("## 按轮汇总")
    out.append("")
    out.append("| 运行 | 课程 | 状态 | 开始 | 时长 | 完成 | 真失败 | 中断 | 弹题 | 平均每集 | 采样 |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for s in sessions:
        pgs = (f"{len(s['progress_by_video'])} 集 / {s['progress_points']} 点"
               if s["progress_points"] else "—")
        out.append(
            f"| `{s['run_id']}` | {s['course'] or '—'} | {s['status'] or '—'} | {fmt_ts(s['started_at'])} | "
            f"{fmt_dur(s['duration'])} | {s['episodes_ok']} | {s['hard_fail']} | {s['soft_stop']} | "
            f"{len(s['answers'])} | {s['avg_episode_sec'] or '—'}s | {pgs} |"
        )
    out.append("")

    # 未完成 / 失败归因
    REASON_LABEL = {
        "done": "完成",
        "stopped": "主动停止",
        "captcha": "撞验证码",
        "limit": "限时到点",
        "interrupted": "服务重启/崩溃",
        "error": "真失败",
        "unknown": "未标注（旧记录）",
    }
    all_fail = []
    for s in sessions:
        for e in s["episodes"]:
            if not e.get("ok"):
                all_fail.append((s["run_id"], e))
    if all_fail:
        out.append("## 未完成的课时（按原因）")
        out.append("")
        out.append("| 运行 | videoId | 课时 | 耗时 | 原因 |")
        out.append("|---|---|---|---|---|")
        for rid, e in all_fail[:40]:
            r = e.get("reason") or "error"
            out.append(f"| `{rid}` | {e.get('videoId')} | {e.get('name') or '—'} | "
                       f"{e.get('elapsed') or 0}s | {REASON_LABEL.get(r, r)} |")
        out.append("")

    # 弹题
    all_ans = [(s["run_id"], a) for s in sessions for a in s["answers"]]
    if all_ans:
        out.append("## 弹题作答明细")
        out.append("")
        out.append("| 运行 | 集 | 题号 | 题干 | 提交 | 是否选中正确项 |")
        out.append("|---|---|---|---|---|---|")
        for rid, a in all_ans[:60]:
            opts = a.get("options") or []
            picked = {x for x in str(a.get("chosen") or "").split(",") if x}
            right = {str(o.get("id")) for o in opts if o.get("correct")}
            verdict = "✔" if (picked and picked & right) else ("—" if not right else "✘")
            stem = (a.get("stem") or "（无题干）").replace("|", "\\|")[:60]
            out.append(f"| `{rid}` | {a.get('videoId')} | {a.get('questionId')} | {stem} | "
                       f"{a.get('chosen')} | {verdict} |")
        out.append("")

    # 每轮明细
    out.append("## 每轮明细")
    for s in sessions:
        out.append("")
        out.append(f"### `{s['run_id']}` — {s['course'] or '（未知课程）'}")
        out.append("")
        out.append(f"- 状态：{s['status']}　倍速：{s['speed']}　"
                   f"起止：{fmt_ts(s['started_at'])} → {fmt_ts(s['finished_at'])}（{fmt_dur(s['duration'])}）")
        out.append(f"- 完成 {s['episodes_ok']} / 失败 {s['episodes_fail']} / 弹题 {len(s['answers'])} / "
                   f"事件 {s['events_count']} 条 / 进度采样 {s['progress_points']} 点")
        if s["progress_by_video"]:
            seg = "，".join(f"{v['from']}%→{v['to']}%"
                            for v in list(s["progress_by_video"].values())[:6])
            out.append(f"- 各集进度采样：{seg}")
        if s["episodes"]:
            out.append("")
            out.append("| 课时 | videoId | 时长 | 耗时 | 结果 | 原因 | 弹题 |")
            out.append("|---|---|---|---|---|---|---|")
            for e in s["episodes"][:80]:
                r = e.get("reason") or ("done" if e.get("ok") else "error")
                out.append(f"| {e.get('name') or '—'} | {e.get('videoId')} | {e.get('sec')}s | "
                           f"{e.get('elapsed')}s | {'完成' if e.get('ok') else '未完成'} | "
                           f"{REASON_LABEL.get(r, r)} | {e.get('answers', 0)} |")

    out.append("")
    out.append("---")
    out.append("原始数据：`data/sessions/<run_id>/`（meta.json / events.jsonl / episodes.jsonl / "
               "answers.jsonl / progress.csv）")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", help="写文件而不是打印")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--limit", type=int, default=50, help="最多读几轮（默认 50）")
    a = ap.parse_args()

    if not os.path.isdir(SESSIONS):
        print(f"没有档案目录：{SESSIONS}", file=sys.stderr)
        return 2
    dirs = sorted([os.path.join(SESSIONS, d) for d in os.listdir(SESSIONS)
                   if os.path.isdir(os.path.join(SESSIONS, d))], reverse=True)[:a.limit]
    sessions = [load_session(d) for d in dirs]

    if a.json:
        text = json.dumps(sessions, ensure_ascii=False, indent=2)
    else:
        text = render(sessions)

    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(a.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
