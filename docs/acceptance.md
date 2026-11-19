# ZHS Studio 端到端验收报告（t3）
> **发布版说明**：本文件是历史验收记录（结论原样保留）。文中的本机绝对路径已替换为占位符；另外开源版的 `/api/state` 已把 `cookie.uuid` 收窄为 `cookie.uuid_prefix`（只回传前 6 位），下文对该接口的字段描述按当时版本记录。


- **验收时间**：2026-11-17 15:17–15:26（Asia/Shanghai）
- **被测服务**：127.0.0.1:8770（FastAPI，`<repo>\app.py` + 复用 `<engine_home>\fucker.py`）
- **测试课程**：rac=`<REDACTED_RAC>`（大学生心理健康，78 集）
- **测试方式**：Invoke-WebRequest / pwsh，全程未用浏览器自动化；cookies.json 只读、内容未打印
- **注意**：测试期间后端成员实时修改代码并于 **15:22:23 重启了服务**。旧构建（15:17–15:22）与重启后新构建（15:22–15:26，含失败重试机制）均已复测，结论一致
- **验收结论**：**不通过** —— 核心刷课链路存在 1 个阻塞级缺陷（0/73 集成功），其余接口契约、停止/恢复链路全部通过

---

## 一、通过的

| # | 验收项 | 结果 | 证据 |
|---|--------|------|------|
| 1 | `GET /api/state` 200，字段齐全 | ✅ | 返回 `status/rac/course/speed/skip_done/limit_min/started_at/finished_at/video/pct/done_count/fail_count/answer_count/error/cookie{ok,count,uuid}/now/bus_seq`，前端所需字段全覆盖 |
| 2 | `GET /api/courses` 200，字段齐全 | ✅ | `{ok, courses[]}`，4 门课；每项含 `rac/name/teacher/school/img/progress_text/last_lesson/status` |
| 3 | `GET /api/course/{rac}` 200，字段齐全 | ✅ | `{ok, tree}`；tree 含 `rac/name/courseId/chapters[]/stats`；chapters 含 `id/name/isPass/exam/videos[]`；videos 含 `videoId/lessonId/name/lesson/sec/done/pct/done_flag` |
| 4 | `GET /api/events?since=0` 200，字段齐全 | ✅ | `{seq, items[]}`；item 含 `id/t/kind/msg`（play 类另有 `videoId`）；增量拉取按 `since` 生效 |
| 5 | `GET /api/course/notexist` 返回 4xx | ✅ | 返回 **400**，body `{"ok":false,"error":"..."}`，并正确写入一条 `拉课程详情失败` error 事件 |
| 6 | running 时重复 `POST /api/run` 返回 409 | ✅* | 6 并发请求中 4 个返回 **409**（body `{"ok":false,"error":"已有任务在跑"}`）；*并发到达的 2 个请求双双 200，见问题 2 |
| 7 | 短任务真跑出现 play 事件 | ✅ | `POST /api/run {"rac":...,"speed":1.5,"skip_done":true,"limit_min":2}` → 事件流出现 `[1/73] 创造性思维` 等 play 事件，格式带序号与 videoId |
| 8 | `POST /api/stop` 状态流转 running→stopping→done | ✅ | stop 后 300ms 轮询观察到 `stopping → done`（1.2s 内完成），无僵死，全程 `/api/state` 正常响应 |
| 9 | stop 后立即再 run 可重启（无 409 死锁） | ✅ | stop(200) → 立即 `POST /api/run` → 200 且 status=running；也可在 done 态直接 run |
| 10 | `logs\uvicorn.err.log` 无异常堆栈 | ✅ | 0 字节，多轮任务后复查仍为空；uvicorn.log 同为空 |
| 11 | 单集进度展示 | ✅ | 新构建下 state.pct 正确显示当前集进度（59.3 = 创造性思维的已看比例） |
| 12 | 服务持续可用 | ✅ | 测试收尾 `POST /api/stop` 后服务 200 正常（15:22:23 的重启经核实为后端成员主动重启换版，非崩溃） |

## 二、发现的问题（按严重度排序）

### 问题 1【阻塞】全部剧集播放失败：`'dict' object has no attribute 'studyTotalTime'`，成功 0/73，核心功能不可用

- **现象**：`POST /api/run` 后每一集都走 play → error，错误恒为 `'dict' object has no attribute 'studyTotalTime'`。旧构建 73/73 全失败、error 堆积 146+（含双 worker 竞态的 2 轮）；新构建（15:22:23 重启后）加了重试机制（每集重试 2 次、退避 2s/5s），**同一错误依旧**，3/3 复现。`done_count` 恒为 0，总体进度 pct 无法递增，「无 error 堆积」验收项不成立
- **复现步骤**：
  1. `POST http://127.0.0.1:8770/api/run`，body `{"rac":"<REDACTED_RAC>","speed":1.5,"skip_done":true,"limit_min":2}`
  2. `GET /api/events?since=0`，观察到 `[play] [1/73] 创造性思维` 紧跟 `[error] ... 'dict' object has no attribute 'studyTotalTime'`（新构建为「第 N 次失败 + 重试 N/2」三连）
- **定位线索**（只读代码审查）：异常发生于 `fucker.py` 的 `fuckZhidaoVideo`（约 L497-498：`video = ctx.videos[video_id]` 后 `played_time = video.studyTotalTime`）。此处 `video` 是**裸 dict**而非 ObjDict：app.py `build_tree` 把 `videoId` 转成 `str` 传入（`"videoId": str(v.videoId)`），而 `ctx.videos` 的键是 API 原始类型的 `v.videoId`；键不匹配时 ObjDict 按默认值回退出**空 dict**，随后属性读取抛出该标准 AttributeError。旁证：`fucker.py` 约 L500 的 `if not video: raise ValueError` 防御判空**写在 L498 读属性之后**，永远起不到防护作用。`/api/course/{rac}` 不受影响（build_tree 直接遍历 chapters，不查 `ctx.videos`）
- **修复建议**：① 统一键类型——app.py 传参与 `ctx.videos` 键统一 `str()`（或查不到时再试 int）；② 把 `fucker.py` 的判空上移到属性读取之前；③ 加一条针对性单测：`ctx.videos[str(videoId)]` 查找命中
- **严重度**：**阻塞**（核心刷课 0% 成功，下游前端收尾/交付不应继续）

### 问题 2【中】`/api/run` 启动竞态：并发请求可双开 worker，同一课程被并行刷两轮

- **现象**：`run()` 用 `TASK["status"] in ("running","stopping")` 做 409 判断，但 `status="running"` 是 worker 线程**进入 `_run` 后**才设置的；handler 内 `t.start()` 前没有同步占位。6 并发实测 2 个请求都拿到 200（T1/T2），事件流出现 **2 次「任务启动」**、两条 play 流交错、`fail_count` 累加 146（=73×2）共享 TASK 计数器互相污染
- **复现步骤**：
  1. 用 6 线程同时 `POST /api/run`（同 body）
  2. `GET /api/events?since=<before>`：`任务启动` 出现 2 次（或更多），多个请求返回 200
  3. `GET /api/state`：done_count/fail_count 为多轮累加值
- **修复建议**：`run()` 内用 `threading.Lock` 做 check-and-set（检查与置 running 在同一临界区同步完成），或在 handler 里先同步 `TASK["status"]="running"` 再起线程
- **严重度**：**中**（双 worker 会对智慧树 API 双倍请求；计数器污染导致面板数字失真）

### 问题 3【低】失败无退避时快速打满上游接口（旧构建）；新构建已有重试退避，观察项

- **现象**：旧构建每集失败即时跳过，73 集在 ~2.3s 内全部打完（对上游是密集请求）；新构建已加 2 次重试 + 2s/5s 退避（改进确认 ✅），但重试全部命中同一必现错误，白等 7s/集
- **复现步骤**：旧构建 `POST /api/run` 后 10s 内即见 300 条事件（73 集全烧完）
- **修复建议**：问题 1 修复后回归观察；对必现型 AttributeError 这类编程错误不值得重试，建议区分「网络类可重试」与「编程错误直接失败」
- **严重度**：**低**

## 三、待实现

| # | 项 | 说明 |
|---|----|------|
| 1 | `data\runs\` 运行统计文件 | `data\` 目录当前不存在，backend 成员开发中 → 按契约记「待实现」，不计失败。落盘后需补验：每次 run 生成统计文件、内容含成功/失败/耗时 |
| 2 | 「pct 递增」验收项 | 因问题 1 无法达成（done=0，总体进度不动）；问题 1 修复后需回归复验「跑 2 分钟 pct 实际递增、error 零堆积」 |

---

## 附：测试数据摘要

| 轮次 | 构建 | 操作 | 结果 |
|------|------|------|------|
| 1 | 旧（15:17） | run(limit_min=2) + 10s×10 轮询 | play 146 / error 147 / done 0（含双 worker 2 轮） |
| 2 | 旧 | 6 并发 run | 2×200 + 4×409，双 worker 竞态坐实 |
| 3 | 旧 | stop → 立即 run → stop | stopping→done 1.2s；重启无死锁 ✅ |
| 4 | 新（15:22:23 重启后） | run 复验 + stop | 重试机制生效，但同一错误 3/3 复现，done 0/3 集 |

*报告生成：qa（AgentTeams t3，attempt 75fb0627-bafa-45f4-ab8f-9246140ebf84）*

---

## 回归（captain 修复后）

- **回归时间**：2026-11-17 15:28–15:43（Asia/Shanghai）
- **回归对象**：captain 修复后的 app.py（键类型归一化 call_id/反查真实键、RUN_GATE 并发闸门、编程错误不重试）
- **测试条件说明**：测试中途（~15:36）服务被再次重启一次（err.log 仅启动横幅、无堆栈，属干净重启换版，非崩溃），首轮 run A 在 ~75% 处被截断；随后补跑完成该集，不影响结论
- **回归结论**：**通过** —— A–E 全部达标，F1 已修复无回归，run 竞态已收敛

### A. 短任务复跑（limit_min=2）——通过

| 验收点 | 结果 | 证据 |
|--------|------|------|
| pct 单调递增 | ✅ | 10s×6 采样 6.9→8.8→10.9→12.8→14.8→16.9 严格递增；补跑 75.9→98.3→done；fucking 前缀 progress 事件 142 条 pct 5.9→74.8 单调，唯一回退 29.9→28.8（-1.1%，防检测暂停回卷，见 R2） |
| fail_count 保持 0 | ✅ | 全程 0 |
| error 事件 0 或个别 | ✅ | 全程 **0 条** error 事件 |
| done_count 播完 +1 | ✅ | `evt#61 [done] 完成 创造性人格与创造性环境`，done 0→1（67926289，sec=860，实播 145.98s） |
| （附加）弹题流 | ✅ | `evt#30 [answer] 弹题已作答`，answer_count=1，写盘 answers=1 |
| （附加）F1 根因 | ✅ | 全程 0 次 `'dict' object has no attribute 'studyTotalTime'`；待刷序列正确排除已完成集（72→71） |

### B. 并发闸门（6 并发 POST /api/run）——通过

- **恰 1 个 200（T1）+ 5 个 409（T2–T6）** ✅
- **「任务启动」事件恰出现 1 次** ✅，无双 worker（对照 t3：2×200、fail_count=146=73×2 污染已消失）
- 附加佐证：补跑 A2 误撞既有任务拿到 409（运行窗口拦截在非并发场景同样生效）
- 结论：**B 无双开**，RUN_GATE check-and-set 有效

### C. data\runs 统计落盘——通过

- 共 8 个 json，字段 `videoId/name/sec/elapsed/ok/answers` 全齐 ✅
- ok=true 样本 `20261117-154011_67926289.json`：`{"videoId":"67926289","name":"创造性人格与创造性环境","sec":860,"elapsed":145.98,"ok":true,"answers":1}` ✅
- ok=false 样本 `20261117-154102_67925259.json`（手动 stop 中断，elapsed 50.51）✅
- 注：进程被强杀（服务重启）的在播集不落盘（写盘只在集末路径）——边缘行为，非缺陷

### D. persisted.completed_video_ids——通过

- 67926289 播完后 `GET /api/state` → `persisted.completed_video_ids = ["67926289"]` ✅
- **stop 后复查仍为 `["67926289"]`** ✅（stop 不清进度，断点续跑正确跳过已完成集）

### E. logs\uvicorn.err.log——通过

- 仅 4 行 uvicorn 启动 INFO 横幅，**0 行 Traceback / Error / Exception** ✅
- 注：t3 时为 0 字节，现含 INFO 启动横幅系启动方式变化（uvicorn 默认打到 stderr），非异常

### 回归发现的新问题（均不阻塞）

> **处理状态（2026-11-17，captain 反馈）**：R1 已修复——`_hooked_progress` 丢弃 `pause a minute` 倒计时前缀，不再写 TASK.pct（单测确认 pct 不被污染，服务已重启验证）；R3 已修复——TimeLimitExceeded 不再给未开播集写 ok=false 统计；R2 转前端处理——显示层 `Math.max(lastPct, pct)` 只增不减，底层数据保持真实。三项均待最终汇总时整体验收确认。

| # | 严重度 | 问题 | 复现 / 定位 | 建议 |
|---|--------|------|------------|------|
| R1 | 低（UI） | 随机暂停倒计时污染 state.pct：暂停期间面板 pct 显示 1.7→100 的倒计时值，恢复后跳回真实进度 | fucker.py:592 暂停时进度条参数为 `[60-pause, 60]`，_hooked_progress 据此写 TASK.pct；本轮实测 60 条 `pause a minute` 事件，采样命中 1.7 / 51.7（=1/60、31/60） | _hooked_progress 仅对 `fucking *` 前缀更新 TASK.pct，或暂停时冻结 pct 并置 paused 标志 |
| R2 | 低 | 防检测暂停结束后 played_time 回卷 last_submit，pct 可微幅回退（实测 29.9→28.8，-1.1%） | fucker.py:552（`played_time = last_submit`，上游防检测设计） | 前端对「进度小幅回退」做容错文案即可，不必改后端 |
| R3 | 信息 | TimeLimitExceeded 收工路径会给未开播的下一集写 ok=false 统计（elapsed≈0） | app.py:420-426 代码审查；本轮未直接触发该路径 | 限时收工时不落盘该集，或标记 skipped 以区别于真实失败 |

### 本轮未覆盖路径（后续补验建议）

- `limit_min` 限时自然收工（TimeLimitExceeded → 收工）未直接触发（两轮分别被服务重启截断 / 手动 stop）
- 验证码（captcha）暂停路径、跨课程切换

**总结**：A–E 全部通过；原「不通过」结论保留为历史（t3 时段有效），当前状态：**回归通过**，阻塞缺陷 F1 已修复，run 竞态已收敛。
