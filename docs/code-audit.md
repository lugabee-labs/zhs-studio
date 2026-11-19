# ZHS Studio 代码审计：硬编码、敏感信息与开源改造点

> 审计范围：`<repo>`（只读，未改动任何代码）与其外部依赖 `<engine_home>`
> 审计时间：2026-11-19
> 审计方式：逐行读源码 + ripgrep 式关键词扫描 + 实际读取 venv 的 `pip list` + 实际读取上游 `LICENSE`/`README`/`meta.json` + GitHub API 核对上游仓库元数据
> 行数勘误：任务书写 app.py「826 行」、web/index.html「756 行」，**实际分别是 928 行 / 782 行**（以本机文件为准，下文行号均按实际文件）
> **发布版说明**：本机绝对路径已替换为占位符（`<repo>` = 本项目目录，`<engine_home>` = 上游 fuckZHS 目录，`<browser-install-path>` = 浏览器安装目录），结论与数字未改动。§3.1 / §6 是**改造建议**，实际落地形态见 [`architecture.md`](./architecture.md)。


---

## 0. 结论速览（给封装/发布同学的执行清单）

| 类别 | 数量 | 是否阻塞开源 |
|---|---|---|
| 必须改：外部源码路径绑定（FZ_HOME / import / chdir） | 1 组（app.py L27-35） | **阻塞** |
| 必须改：venv 绝对路径绑定（2 个 .ps1 + README） | 3 处 | **阻塞** |
| 必须改：Windows 专有启动链（Edge 路径 / PowerShell / Stop-Process） | 2 个脚本 | 阻塞（可降级为「可选平台支持」） |
| 必须改：凭据文件落在上游目录、且无 .gitignore | 1 处设计问题 | **阻塞** |
| 必须改：真实课程 rac 出现在文档里 | docs/acceptance.md 2 行 | **阻塞** |
| 必须改：缺失 LICENSE | 仓库根无 LICENSE | **阻塞** |
| 建议改：本地 API 无鉴权 + 全量 uuid 下发 | app.py L741 | 建议（安全） |
| 建议改：monkeypatch 上游模块函数 | app.py L462-463 / L552-553 | 建议（可维护性） |
| 硬编码：端口 8770 / 9222 / CDP 地址 | 5 个文件 15 行命中（另有 4 行 CDP 地址字面量） | 不阻塞，但需配置化 |
| 硬编码：课程 rac / cookie uuid | **源码中无**；仅数据与验收文档有 | 数据侧阻塞，代码侧无需改 |
| 凭证关键词 | 6 个受审文件**无凭证值命中**（仅 `CASLOGC` 这个字段名出现，无值） | 通过 |

**一句话**：代码本身没有写死的密码/密钥/课程号，真正的开源阻塞点是「绑死本机 fuckZHS 目录 + 绑死某一个 venv 的 python.exe + 两个只认 Windows/Edge 的启动脚本」，以及数据目录里那一堆等同账号凭证的文件。

---

## 1. 硬编码清单

### 1.1 绝对路径（最高优先级）

| # | 文件:行 | 硬编码内容 | 影响 | 配置化建议 |
|---|---|---|---|---|
| P1 | `app.py:27` | `FZ_HOME = os.environ.get("FZ_HOME", r"<engine_home>")` | 非本机一定启动失败；且默认值把「另一个项目」的私有部署路径固化进开源代码 | 改为 `config.json` 的 `engine_home`，优先级：`--engine-home` 参数 > 环境变量 `ZHS_ENGINE_HOME` > 配置文件 > 报错并给出「如何安装上游」的提示文案；默认值不要指向某个人的 D 盘 |
| P2 | `app.py:28` | `sys.path.insert(0, FZ_HOME)` | 把外部目录塞进 `sys.path` 首位，模块名 `fucker`/`utils`/`logger` 极易与其他包冲突（`utils`、`logger` 是超通用名） | 改为 `importlib.util.spec_from_file_location()` 按绝对文件加载，或以「可选 pip 依赖 / git submodule」方式提供引擎；至少把 `sys.path` 插入改为「校验 + 一次性 + 失败即报错」 |
| P3 | `app.py:29` | `os.chdir(FZ_HOME)` | 进程级 CWD 被改掉：后续任何相对路径都相对上游目录解析；`uvicorn --reload`、相对配置、相对日志路径都会错位；也无法同时服务两个引擎目录 | **删除 chdir**。上游 `getRealPath()` 是按 `__file__` 定位的（见 `utils.py:66-78`），本来就不依赖 CWD；本项目的 `HERE` 也已经是绝对路径（`app.py:34`） |
| P4 | `app.py:35` | `COOKIE_FILE = os.path.join(FZ_HOME, "cookies.json")` | 把「本项目要写的凭证文件」写进**别人的源码目录**：升级/重装上游会连带丢失，或反向污染上游仓库工作区 | 迁到本项目 `data/cookies.json`（已被 `.gitignore` 覆盖），并支持 `cookie_file` 配置项；首次启动自动 `os.makedirs` |
| P5 | `start.ps1:11` | `$py = '<engine_home>\.venv\Scripts\python.exe'` | 强绑某个 venv 的具体盘符路径，换机器/换用户即废 | 改为顺序探测：`$env:ZHS_PYTHON` → `<root>\.venv\Scripts\python.exe` → `py -3` → `python`；找不到再抛错 |
| P6 | `tools/shot.ps1:13` | 同上（同一串 venv 路径） | 同上 | 同上，或直接改为 `python tools/shot.py`（让用户自己在激活后的环境里跑） |
| P7 | `README.md:25`、`README.md:32`、`README.md:140-141` | 三处写死 `<engine_home>`（含 `.venv\Scripts\python.exe` 与 `cookies.json` 落点） | 文档直接教用户往错误位置放东西 | README 改为「把上游 fuckZHS 放在任意目录，配置 `engine_home` 指过去」；示例路径用 `<你的路径>` 占位 |
| P8 | `start-edge-debug.ps1:16` | `$edge = "<browser-install-path>\msedge.exe"` | 只认 64 位 Windows 上 Edge 的默认安装位置；Edge 装在 `Program Files`、或用户用 Chrome/Brave，即失败 | 改为候选列表探测（`Program Files (x86)`、`Program Files`、注册表 `App Paths\msedge.exe`），并支持 `-Browser chrome|edge|brave` |
| P9 | `start-edge-debug.ps1:18` | `Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\User Data'` | 同上，且「用用户默认 profile」本身有风险（会把正常浏览的 Edge 全部关掉，见 L28-43） | 改为 `--user-data-dir=<本项目 data/browser-profile>` 的**独立 profile**，不碰用户日常 Edge；这样也无需杀进程（同 profile 抢锁是被忽略参数的根因） |
| P10 | `tools/shot.py:79` | 截图输出目录固定为 `<repo>/shots` | 尚可（相对位置），但 `shots/` 属于「绝不入库」目录 | 保持相对路径，但加 `--out` 参数并在 `.gitignore` 覆盖 |

> 说明：`app.py:34` 的 `HERE`、`app.py:40-44` 的数据目录、`tools/report.py:25-26` 的 `ROOT` 都是**从文件位置推导**的，写法正确，无需改动（这是好事，说明作者已经意识到不要写死）。

### 1.2 端口与 CDP 地址

| # | 文件:行 | 硬编码 | 说明 | 建议 |
|---|---|---|---|---|
| N1 | `app.py:10` | 文档串 `--port 8770` | 只是注释，但会被复制粘贴 | 注释改为「默认 8770，可用 `-Port` / `--port` 覆盖」 |
| N2 | `start.ps1:6` | `[int]$Port = 8770` | 已有参数化，仅默认值 | 保留；建议同时支持 `config.json` 的 `server.port`，避免两处真相 |
| N3 | `start.ps1:17,25,33` | `--host 127.0.0.1`、`http://127.0.0.1:$Port/api/state` | 绑定回环（安全默认，**合理**）；健康探测 URL 与端口参数已联动 | 保留；若开放局域网访问需显式 `-Host` 开关并在 README 写清风险 |
| N4 | `app.py:237` | `def grab_cookies(port: int = 9222)` | CDP 端口默认值 | 抽到配置 `cdp.port` |
| N5 | `app.py:241` | `f"http://127.0.0.1:{port}/json/version"` | CDP 地址内联拼接 | 抽 `cdp.host` + `cdp.port`，拼成 `cdp.base_url` |
| N6 | `app.py:774` | `def api_grab(port: int = 9222)` | 同上（FastAPI query 默认值） | 默认值从配置读 |
| N7 | `tools/shot.py:23` | `CDP = "http://127.0.0.1:9222"`（模块常量） | **与 L50 的 `--port` 参数脱节**：`--port` 解析了却从未使用（`a.port` 无引用） | 函数内改用 `f"http://127.0.0.1:{a.port}"`；顺手删掉「参数无效」的坑 |
| N8 | `tools/shot.py:49` | `--url` 默认 `http://127.0.0.1:8770/` | 与 start.ps1 端口可能不一致 | 默认值改为 `http://127.0.0.1:{server.port}/`（读配置） |
| N9 | `tools/shot.ps1:10` | `[string]$Url = "http://127.0.0.1:8770/"` | 同上 | 同上 |
| N10 | `start-edge-debug.ps1:11` | `[int]$Port = 9222` | 已有参数化 | 保留，读配置作默认 |
| N11 | `start-edge-debug.ps1:22,57` | `http://127.0.0.1:$Port/json/version` 探测 | 合理 | 保留 |
| N12 | `web/index.html` | **无**任何端口/主机硬编码 | 前端全部走相对路径 `fetch('/api/...')`（L357/L365/L441/L452/L494/L601/L660/L731/L750/L760） | 无需改（这是好设计） |

### 1.3 业务常量与耦合到上游内部细节的硬编码

这些不是「敏感信息」，但直接决定开源后别人能不能跑通，建议一并配置化或至少注释说明。

| # | 文件:行 | 硬编码 | 风险/含义 | 建议 |
|---|---|---|---|---|
| B1 | `app.py:95` | `sid = 时间戳 + "_" + str(rac)[:8]` | 轮次目录名**带课程 rac 前 8 位**（形如 `20261119-112804_<rac前8位>`），rac 因此扩散到目录名、`/api/sessions` 响应、`events.jsonl` 首行 | 目录名只用时间戳 + 短 hash；rac 只写进 `meta.json`（数据目录反正不入库） |
| B2 | `app.py:146` | `Bus(cap: int = 3000)` | 内存事件环只留 3000 条（落盘不丢，见 L160-164） | 抽配置 `bus.capacity` |
| B3 | `app.py:253` | `"zhihuishu" in c["domain"]` | 只抓 `*.zhihuishu.com` 的 cookie（业务必需） | 抽 `cdp.cookie_domain_filter`，并允许追加（如学校自建域名） |
| B4 | `app.py:270` | `os.chmod(COOKIE_FILE, 0o600)` | **Windows 上是空操作**（只有只读位），给人一种「凭证已加固」的错觉 | 保留调用但注释写清；Windows 建议改 ACL（`icacls`）或至少不宣称 600 生效 |
| B5 | `app.py:462-463` | `_orig_progress = fzmod.progressBar` / `_orig_answer = Fucker.answerZhidao` | 依赖上游**模块级函数名**与**方法名**；上游改名/重构即静默失效（`_orig_progress` 实际上从未被调用，是死代码） | 改为 `class ZhsFucker(Fucker)` 覆写 `answerZhidao`；进度回调用显式注入，避免猴补丁 |
| B6 | `app.py:473` | `str(prefix).startswith("pause")` | 靠上游「防检测暂停复用了 progressBar」的内部约定来过滤；上游一改文案就漏判（前端 pct 会突跳） | 在适配层集中声明「上游内部约定」，并加单测/启动自检：若连续出现 1/60、31/60 这类值即告警 |
| B7 | `app.py:483-484` | 采样节流 `>=10s` 或 `>=5%` | 分析口径写死在代码里 | 抽 `session.sample_interval_sec` / `sample_interval_pct` |
| B8 | `app.py:519-521` | 题干字段候选名 `questionContent,name,content,…` | 兼容多版本的上游字段名，属于对上游 payload 的硬耦合 | 保留，但把候选列表提到模块级常量 + 注释「字段来源：answers.jsonl 的 q_keys 实测」 |
| B9 | `app.py:557` | `_RETRY_DELAYS = [2, 5]`（每集最多 3 次） | 重试策略写死 | 抽 `task.retry_delays` |
| B10 | `app.py:728` | `class RunReq: speed: float = 1.5` | 服务端默认倍速 1.5（与上游 `fucker.py:529` 的知到默认一致） | 抽 `task.default_speed`；注释写明「>1.5 有风控风险」 |
| B11 | `web/index.html:310` | `<input id="speed" … value="1.5">` | 前端默认倍速（与 B10 双写） | 改为从 `/api/state` 读默认值，避免两处真相 |
| B12 | `web/index.html:383` | `S = { …, speed:1.5, limit:0, skip:true }` | 同上 | 同上 |
| B13 | `web/index.html:357,365` | 默认超时 `12000` / `15000` ms | 各接口超时分散在调用点（另有 8000/15000/20000/60000/10000） | 抽 `API_TIMEOUT` 常量表 |
| B14 | `web/index.html:772-774` | 轮询 `1200ms` / `1000ms` / `4000ms` | 写死 | 抽常量；README 说明「3 个定时器」便于别人评估开销 |
| B15 | `web/index.html:578` | 自动滚动防抖 `10000ms` | 写死 | 抽常量 |
| B16 | `web/index.html:757` | `https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=${S.rac}` | 智慧树课程页 URL 模板（业务必需） | 抽 `site.course_url_template`，便于站点调整时只改一处 |
| B17 | `start-edge-debug.ps1:51` | `https://studyvideoh5.zhihuishu.com/` | 同上 | 同上 |
| B18 | `start-edge-debug.ps1:45-52` | `--profile-directory=Default`、`--no-first-run`、`--no-default-browser-check` | profile 目录名写死 `Default`（中文/多账号环境常不是 Default） | 抽 `-ProfileDirectory` 参数 |

### 1.4 明确「没有」的硬编码（避免下游误改）

- **课程 rac 未写死在代码里**：`app.py`、`web/index.html`、`tools/*.py`、`*.ps1` 全部 0 命中（已用「本机真实 rac 前缀」与 `rac` 字面量双向扫描；为避免二次扩散，本文件不记录该值）。
  rac 仅出现在：`docs/acceptance.md:5,35`（验收文档，见 §2.2）、`data/task-state.json`、`data/sessions/*/meta.json`（数据文件）。
- **cookie / uuid 未写死在代码里**：`uuid` 只作为运行时变量出现（`app.py:214-225`、`app.py:777`、`index.html:499`）。
- **无 apiKey / password / token 字面量**：6 个受审文件按 `apiKey|api_key|password|token|secret|PRIVATE KEY|Authorization` 扫描，仅命中 `CASLOGC` 这个**字段名**（`app.py:8,220`、`start-edge-debug.ps1:3`），无值。
- **前端无外部 CDN 依赖**：`web/index.html` 内无 `<script src>` / `<link href>` / `@import`，单文件自包含（离线可用，利于开源）。

---

## 2. 敏感信息清单

### 2.1 「绝不能进仓库」文件（凭据级）

| # | 文件（本机路径） | 实测内容 | 泄露后果 | 处理 |
|---|---|---|---|---|
| S1 | `<engine_home>\cookies.json` | **95 条** cookie，全部 `*.zhihuishu.com`：`CASLOGC`（373 字符，URL 解码后含 `uuid`）、`CASTGC`（85）、`jt-cas`（391）、12 个站点的 `JSESSIONID`、3 个 `SESSION`、5 个 `route-ingress`、`gdxidpyhxdE`、`acw_tc` 等 | **等同账号密码**：可冒充登录态调接口、看课程与成绩。其中还有一个 cookie 名里直接内嵌账号 uuid 前缀（`exitRecod_<uuid前8位>`） | 加 `.gitignore`；README 写明「本文凭文件在 `data/` 下，永不入库」；发布前扫描关键词 `CASLOGC`/`CASTGC`/`jt-cas` |
| S2 | `<engine_home>\config.json` | 上游配置：`bark.token` **非空（29 字符，形如推送 token，本审计未打印内容）**；`pushplus.token` 空；`ai.openai.api_key`/`ai.ppt_processing.moonShot.api_key` 为 3 字符占位；`username`/`password` 空 | 推送 token 泄露可被滥用推送；AI key 若被真实填写则泄露付费额度 | 本项目**不要复制**该文件；`config.example.json` 只放本项目需要的字段；上游 config 由用户自己在引擎目录维护 |
| S3 | `<repo>\data\exam-answers.json`（21,893 B） | 「智慧树单元测试答案库」：`_readme`、`updated_at`、`courses`（courseId `<courseId>` + 教师名 + `exams[]`），每条带 `onlineexamh5new.zhihuishu.com/...#/webExamList/dohomework/<id>/<token>/<token>/…` 与答案/置信度 | 考试答案 + 带 token 的考试 URL：既是**答案泄漏**，也是**账号线索**（考试 id 属于某班级） | **绝不入库**；`.gitignore` 显式点名；如需保留能力，只提交 schema 示例（字段名 + 空值） |
| S4 | `<repo>\data\sessions\**`（15 个目录 / 1,547 KB） | 每轮 `meta.json`（真实 rac、课程名、失败清单里带课时名）、`events.jsonl`（含弹题题干与选项、课程进度）、`episodes.jsonl`、`answers.jsonl`、`progress.csv` | 暴露：上过什么课、进度、弹题原题与答案、课程 rac | **绝不入库**；`.gitignore` 覆盖 `data/` |
| S5 | `<repo>\data\answers.jsonl`（51,353 B / 105 条） | 全局弹题流水：`t,videoId,questionId,stem,options[{id,text,correct}],chosen,picked,source,q_keys` —— **含正确答案标记** | 题目与答案库级泄漏 | **绝不入库** |
| S6 | `<repo>\data\task-state.json` + `task-state.json.bak-20261117-162657` | 真实 rac（两个不同课程）、课程名、已完成 videoId 列表 | 账号课程线索 | **绝不入库**（`data/` 整体忽略） |
| S7 | `<repo>\data\runs\*.json`（93 个） | 每集：`videoId/name/sec/elapsed/...` | 课时名 + 进度 | **绝不入库** |
| S8 | `<repo>\data\report.md`（21,647 B） | report.py 生成的复盘：课程名、逐集明细、弹题题干 | 同上 | **绝不入库** |
| S9 | `<repo>\shots\**`（112 个文件 + 8 个目录，共 36 个顶层条目） | 界面截图 | 截图里含**课程名、教师名、uuid 前 6 位**（UI 主动显示 `已登录 · N cookie · xxxxxx***`，`index.html:499`） | **绝不入库**；`.gitignore` 覆盖 `shots/` |
| S10 | `<repo>\logs\`（当前 2 个 0 字节文件） | uvicorn stdout/err | 后续可能记录请求路径、错误栈（含 rac） | 忽略 `logs/` |
| S11 | `<repo>\__pycache__\app.cpython-312.pyc`（51,172 B） | 字节码里**含 `<engine_home>\...` 绝对路径字符串**（已实测命中 `fuckZHS`） | 泄露本机目录结构（弱敏感） | 忽略 `__pycache__/`、`*.pyc` |

### 2.2 「含真实标识、发布前必须脱敏」文件

| # | 文件:行 | 内容 | 处理建议 |
|---|---|---|---|
| D1 | `docs/acceptance.md:5` | 「测试课程：rac=`<本机真实 rac>`（某共享课，78 集）」 | 换成 `rac=<课程 rac>`；课程名改为「示例共享课」 |
| D2 | `docs/acceptance.md:35` | 同上 rac 作为请求体示例 | 同上 |
| D3 | `docs/acceptance.md:76` | 内部 attempt id（AgentTeams 流水号） | 可保留（无敏感），或统一改成 `t3` |
| D4 | `docs/acceptance.md:22,37` | 课时名（如「创造性思维」）、`fucker.py` 行号分析 | 课时名建议替换为 `<课时名>` |
| D5 | `README.md:127` | `run_id` 示例 `20261117-172917_<rac前8位>`（含 rac 前 8 位） | 改为 `20261117-172917_<rac前8位>` 占位写法 |
| D6 | `README.md:108` | 「本账号 4 门课的章节测验全部已通过」 | 属于账号状态描述，改为「示例账号」 |
| D7 | `README.md:172-180` | 内部 AgentTeams 开发记录（成员/路由/模型名） | 发布版建议删掉或改写为「开发方式」，避免暴露内部流程与计费信息 |

### 2.3 运行时才会产生的敏感面（代码层）

| # | 位置 | 现象 | 建议 |
|---|---|---|---|
| R1 | `app.py:741` | `st["cookie"] = cookie_info()`，而 `cookie_info()`（L214-225）返回**完整 uuid**；`/api/state` 直接把完整 uuid 发给前端 | 改为只返回 `{"ok": bool, "count": int, "uuid_prefix": "xxxxxx"}`；完整 uuid 只留在服务端内存（前端只用前缀，见 `index.html:499`） |
| R2 | `app.py:777` | 事件流写入 `uuid=<前6位>***` | 保留（已是前缀），但这条事件会**落盘进 `sessions/*/events.jsonl`**，若有人误传 sessions 即泄露前缀 → 配合忽略规则即可 |
| R3 | `app.py:773-781` | `POST /api/cookie/grab` 无鉴权、无 CSRF 防护，任意本地页面/脚本可触发「抓 cookie 并写盘」 | 本地服务默认仅 `127.0.0.1`（`start.ps1`），风险可控；建议加「仅允许 `Origin` 为本机」或一次性 token，并在 README 写明「不要把服务暴露到局域网」 |
| R4 | `app.py:872` | `/api/session/{run_id}` 用 `os.path.basename(run_id)` 防穿越 | **写得好**，无需改（审计确认已防 `../`） |

---

## 3. 开源前必须改的点

### 3.1 对外部 fuckZHS 源码的 import 依赖

**现状（实测）**

```python
# app.py L26-35
FZ_HOME = os.environ.get("FZ_HOME", r"<engine_home>")
sys.path.insert(0, FZ_HOME)
os.chdir(FZ_HOME)
import fucker as fzmod
from fucker import CaptchaException, Fucker, TimeLimitExceeded
COOKIE_FILE = os.path.join(FZ_HOME, "cookies.json")
```

- 上游 (`fuckZHS-master`, meta.json `version: 2.6.3`) 是**平铺的脚本集合**，没有包结构、没有 `pyproject.toml`/`setup.py`，因此只能靠改 `sys.path` 才能 import。
- `app.py` 依赖的上游接口（含私有语义）：
  | 依赖点 | 上游位置 | 稳定性 |
  |---|---|---|
  | `Fucker(speed, limit, tree_view, progressbar_view)` + `.cookies` | `fucker.py:61-109` | 构造签名有默认值，较稳 |
  | `Fucker.getZhidaoList()` / `getZhidaoContext(rac, force)` | `fucker.py:342,363` | 稳 |
  | `Fucker.fuckZhidaoVideo(rac, video_id)`，且 **video_id 必须是 API 原始 int** | `fucker.py:488` | 脆（见 `app.py:642-647` 的反查修复） |
  | `Fucker.answerZhidao(q)` | `fucker.py:600-604` | 靠覆写实现，稳 |
  | 模块级 `progressBar` 被 `fzmod.progressBar = …` 替换 | `utils.py:96-123`（经 `fucker.py:2` 引入） | **脆**：上游把「暂停倒计时」也走这个通道 |
  | 异常类 `CaptchaException` / `TimeLimitExceeded` | `fucker.py:54-58` | 稳 |
  | 数据结构字段 `chapters.videoChapterDtos / videoLessons / videoSmallLessons / watchState / studyTotalTime` | 上游 API 层 | 脆（上游升级即可能变） |
  | `end_thre` 默认 **0.91**、知到默认倍速 **1.5** | `fucker.py:109,529` | 语义常量，需在 README 说明 |

**改造建议（按成本从低到高）**

1. **最小可用（推荐先做）**：抽出 `zhs_engine.py` 适配层
   - `load_engine(engine_home)`：用 `importlib.util.spec_from_file_location` 加载 `fucker.py`，不污染全局 `sys.path`；找不到时抛出带指引的 `EngineNotFound`（提示「去 GitHub 拉 fuckZHS，把路径填进 config.json 的 engine_home」）。
   - 全部上游调用收敛到 `class ZhidaoEngine` 的 6 个方法（`list_courses / course_tree / play_video / answer_hook / progress_hook / cookie_from_cdp`）。
   - `app.py` 只与适配层交互 → 上游换版本只需改一个文件。
2. **去猴补丁**：`class ZhsFucker(Fucker)` 覆写 `answerZhidao`；进度回调改为在适配层里包装 `progressBar` **函数引用**（传给上游前替换、结束后恢复），避免进程级全局篡改。
3. **确定依赖形态（三选一，必须在 README 写清）**
   - (a) **外部依赖**（当前形态）：仓库不含上游代码，只要求用户自行放置 → 与 MIT 兼容最省事（不构成再分发），但体验差；
   - (b) **git submodule / 安装脚本**：`scripts/install_engine.ps1` 拉取指定 commit（建议 pin `v2.6.3` 对应的 commit），README 写明；
   - (c) **vendor 进仓库**：把 `fucker.py`/`zd_utils.py`/`utils.py`/`logger.py`/`ObjDict.py`/`push.py`/`sign.py` 放进 `third_party/fuckZHS/`，**必须**同时带上上游 `LICENSE` 与版权声明（见 §4）。
   > 建议 (b)：既避免「让用户手动 clone 到某个 D 盘目录」的开源尴尬，又不把第三方代码与本项目许可搅在一起。

### 3.2 venv 路径绑定

| 位置 | 现状 | 改造建议 |
|---|---|---|
| `start.ps1:11-12` | 写死 `<engine_home>\.venv\Scripts\python.exe`，不存在直接 throw | ① 优先 `$env:ZHS_PYTHON`；② 本项目 `.venv\Scripts\python.exe`；③ `py -3 -V` 探测；④ 报错文案给出 `python -m venv .venv` + `pip install -r requirements.txt` 的完整命令 |
| `tools/shot.ps1:13` | 同上 | 同上；或干脆删掉这个包装脚本（`shot.py` 本身可跨平台） |
| `README.md:140-141` | 用 venv 绝对路径跑 `report.py` | 改为 `python tools/report.py -o data/report.md`（激活环境后） |
| 依赖装在哪 | 当前是**借用** fuckZHS 的 venv 装本项目依赖（fastapi/uvicorn 等都在那个 venv 里） | 开源版必须让本项目有自己的 venv 与 `requirements.txt`；引擎复用同一解释器即可（把 engine 目录加入该 venv 的 `sys.path`，而不是反过来） |

### 3.3 Windows 专有 API

| 位置 | 专有点 | 影响 | 建议 |
|---|---|---|---|
| `app.py:270` | `os.chmod(600)` | Windows 只改只读位，安全承诺落空 | 保留 + 注释；Windows 用 `icacls`（可选实现），README「安全说明」写实情 |
| `start.ps1`（整份） | PowerShell、`Start-Process -WindowStyle Hidden`、`Invoke-WebRequest`、`$MyInvocation` | macOS/Linux 不可用 | 新增跨平台入口 `run.py`（`uvicorn.run("app:app", host="127.0.0.1", port=cfg.port)`），`.ps1` 降级为 Windows 便捷脚本 |
| `start-edge-debug.ps1`（整份） | `Get-Process msedge` / `Stop-Process -Force` / `CloseMainWindow` / Edge 安装路径 / `%LOCALAPPDATA%` | 不可移植；且**会强杀用户全部 Edge 窗口**（数据丢失风险） | 改为「独立 `--user-data-dir`」方案后，不再需要杀进程；跨平台只保留「如何带 `--remote-debugging-port` 启动浏览器」的文档说明 + 可选 Python 实现（`psutil`/`subprocess`），浏览器路径候选表覆盖 Chrome/Edge/Brave，Win/macOS/Linux |
| `tools/shot.ps1`（整份） | 仅做参数转发 | 与 `shot.py` 功能重复 | 删除或标注「Windows only 便捷包装」 |

> 附：上游 `fucker.py`/`utils.py` 本身**没有** `winreg`/`ctypes`/`os.startfile` 之类硬绑定，只有两处 Windows 适配：
> - `main.py:135` `show_in_terminal = platform.system() == "Windows"`（CLI 二维码显示，本项目不用）
> - `utils.py:92,113` `except OSError` 注释写着 PyCharm on Windows（`get_terminal_size` 兜底）
> 也就是说：**跨平台的阻碍主要在本项目的 `.ps1`，不在引擎**。

### 3.4 其他「开源体面度」必改项

| # | 问题 | 位置 | 建议 |
|---|---|---|---|
| X1 | 无 `LICENSE` | 仓库根 | 加 MIT（与上游一致，见 §4），并在 README「许可」段说明上游与第三方归属 |
| X2 | 无 `.gitignore` | 仓库根 | 至少覆盖：`data/`、`cookies.json`、`shots/`、`logs/`、`__pycache__/`、`*.pyc`、`.venv/`、`dist/`、`*.local.json` |
| X3 | 无 `requirements.txt` | 仓库根 | 按 §5 生成；README 给出 Python 版本要求（**≥3.10**，因为 `app.py` 用了 `str \| None` 语法；实测运行环境 3.12.8） |
| X4 | 无配置样例 | 仓库根 | `config.example.json`（字段草案见 §6），且**不得**包含任何真实路径/端口以外的秘密 |
| X5 | 验收文档含真实 rac / 课时名 | `docs/acceptance.md` | 脱敏后再入库（§2.2 D1-D4） |
| X6 | README 内部研发信息 | `README.md:172-180`、`README.md:118`（`code: -12` 内部结论） | 发布版删减内部流程；保留技术结论 |
| X7 | 死代码 / 无效参数 | `app.py:462`（`_orig_progress` 未使用）、`tools/shot.py:50`（`--port` 未生效） | 顺手清理，减少 review 噪音 |
| X8 | 30 处 `except Exception: pass` | `app.py` 全篇 | 不阻塞；建议至少让审计关键路径（事件落盘、meta 写入）在失败时打一行 warn，否则线上排查全靠猜 |
| X9 | 合规声明 | README | 必须写：仅供个人学习研究、不得代考/刷学分/商业牟利；账号风险自负；与智慧树官方无关 |

---

## 4. 许可证兼容性

### 4.1 实测证据（不是推测）

| 项 | 结果 | 证据 |
|---|---|---|
| 上游仓库 | **公开、未归档** `VermiIIi0n/fuckZHS`，默认分支 `master`，2,724 stars / 235 forks，`pushed_at 2025-10-28` | `GET https://api.github.com/repos/VermiIIi0n/fuckZHS`（HTTP 200，2026-11-19 取） |
| 上游 LICENSE | **MIT License**，`Copyright (c) VermiIIi0n` | 本地文件 `<engine_home>\LICENSE`（全文为标准 MIT 模板） |
| GitHub 侧许可证识别 | `license.spdx_id = "MIT"` | 同上 API：`"license":{"key":"mit","name":"MIT License","spdx_id":"MIT"}` |
| 上游版本 | `2.6.3`（branch master，author VermiIIi0n） | 本地 `meta.json` |
| 上游 `pyproject.toml` / `setup.py` | **不存在**（本地目录只有 `requirements.txt`） | `<engine_home>` 目录清单 |
| 上游 README 的额外限制 | README 开头两段是**反倒卖**的表态（有人在 TG 倒卖并谎称获授权），**不是**许可条款；README 无 License 章节 | `README.md:10-14` |
| 本项目 LICENSE | **不存在**（仓库根无 `LICENSE`/`LICENSE.*`） | `Get-ChildItem -Filter 'LICENSE*'` 无输出 |

### 4.2 兼容性结论

- 上游是 **MIT**（宽松、允许商用/修改/再分发），因此：
  1. 本项目**可以**选用 MIT（或 Apache-2.0 等更严格但兼容的许可）；
  2. 若选择 §3.1 方案 (c) **vendor 上游代码**：必须保留 `Copyright (c) VermiIIi0n` + MIT 全文，建议放 `third_party/fuckZHS/LICENSE`，并在根 `LICENSE` 的「Third-party」段列出；
  3. 若选择方案 (a)/(b)（**不在仓库内分发上游代码**）：MIT 的署名义务不触发（未再分发），但**强烈建议**在 README 显著位置注明「API 层依赖 fuckZHS（MIT, © VermiIIi0n）」，这既是礼貌也是溯源；
  4. 上游 README 的反倒卖表态属于**道德期待而非许可条件**，MIT 未撤销；但公开发布时建议主动在 README 致谢上游、不做商业售卖宣传，避免作者不快。
- **注意**：GitHub API 只证明「此刻」仓库与许可证状态。作者在 README 里写过「可能会 404」（`README.md:5`），因此**发布时建议 pin 到具体 commit 或 tag，并在 README 记录 commit hash**，防止上游下线后不可复现。
- 与「本项目代码许可」不冲突：本项目为原创（FastAPI 后端 + 手写前端 + 运行档案/断点续刷等工程层），其实现不复制上游源码（只 import），因此可独立授权为 MIT。

> 未查证项（不猜，留给人工确认）：
> - 上游第三方依赖的许可（`requirements.txt` 5 项 + venv 里 30 余项）各自许可证未逐一核对，若发布**含二进制/锁定清单**的分发包，需人工过一遍（尤其 `tiktoken`、`pycryptodome` 等）。
> - 上游 `images/`、`decrypt/`、`qrcode/` 目录内素材的版权归属未核实（本项目不引用，仅在 vendor 时需要处理）。
> - 智慧树平台的服务条款/robots 政策未查证——这属于「合规风险」而非「许可证」，但发布前应人工评估（README 需有免责声明）。

---

## 5. 依赖清单

### 5.1 实测环境

```
解释器：<engine_home>\.venv\Scripts\python.exe  →  Python 3.12.8
采集方式：& $py -m pip list --format=freeze   （2026-11-19）
```

### 5.2 代码实际 import → 发行包映射（直接依赖）

| 代码位置 | import 语句 | PyPI 包 | venv 实测版本 |
|---|---|---|---|
| `app.py:22-23` | `from fastapi import FastAPI` / `fastapi.responses` | **fastapi** | `0.163.1` |
| `app.py:24` | `from pydantic import BaseModel` | **pydantic** | `2.14.3` |
| `app.py:239` | `import websocket` | **websocket-client** | `1.9.2` |
| `start.ps1:25` | `-m uvicorn` | **uvicorn** | `0.54.0`（+ 可选 `[standard]`：实测已装 `websockets 17.4`、`httptools 0.9.0`、`watchfiles 1.3.0`） |
| `fucker.py:20` | `import requests` | **requests** | `2.34.2` |
| `fucker.py:29` | `from openai import OpenAI` | **openai** | `3.52.0` |
| `fucker.py:34` | `import tiktoken` | **tiktoken** | `0.14.0` |
| `zd_utils.py:1` | `from Crypto.Cipher import AES` | **pycryptodome** | `4.5.0` |
| `utils.py:7` | `from PIL import Image, ImageOps` | **pillow** | `13.0.0` |

### 5.3 建议的 `requirements.txt`

```text
# ZHS Studio —— 直接依赖，版本取自本机实测 venv（Python 3.12.8）
fastapi==0.163.1
uvicorn==0.54.0
pydantic==2.14.3
websocket-client==1.9.2
requests==2.34.2
openai==3.52.0
tiktoken==0.14.0
pycryptodome==4.5.0
pillow==13.0.0
```

说明与注意：

1. **`openai` + `tiktoken` 是硬依赖**：`fucker.py:29,34` 在**模块顶层** import，即使不用 AI 考试也必须安装，否则 `import fucker` 直接失败 → 上游若日后改成懒加载，可把它们降级为 `[ai]` extras。
2. **`pydantic` 建议显式列出**（虽然由 fastapi 传递引入），因为 `app.py` 直接 `from pydantic import BaseModel`。
3. **引擎目录不会随 `pip install` 进来**：`fucker/zd_utils/utils/logger/ObjDict/push/sign` 是本地文件（见 §3.1），`requirements.txt` 只解决第三方包。
4. 版本号来自本机实测，其中 `openai==3.52.0`、以及 venv 里出现的 `httpx2==2.16.2` / `httpcore2==2.16.2` 与常见 PyPI 命名不一致（疑为本机环境的替代分发包），**打包时建议先 `pip index versions` / 干净环境 `pip install -r` 复验一次再定稿**。
5. 传递依赖（仅供参考，不必写进 requirements）：`starlette 1.10.0`、`anyio 4.16.0`、`annotated-types 0.8.0`、`pydantic_core 2.50.4`、`typing_extensions 4.16.0`、`urllib3 2.8.0`、`certifi 2026.10.23`、`charset-normalizer 3.5.2`、`idna 3.20`、`h11 0.16.0`、`click 8.5.1`、`jiter 0.19.0`、`regex 2026.11.15`、`sniffio 1.3.1`、`truststore 0.10.5`、`python-dotenv 1.2.4`、`PyYAML 6.0.3`、`opentelemetry-api 1.45.2`、`watchfiles 1.3.0`、`websockets 17.4`、`httptools 0.9.0`。
6. `requirements.txt` 与 `.gitignore`、`LICENSE`、`config.example.json` 都是**必须由封装阶段新建**的文件（当前仓库三者都没有）。

---

## 6. 配置化字段草案（供 `config.example.json` 直接采用）

```jsonc
{
  "engine_home": "<path-to>/fuckZHS-master",   // 或环境变量 ZHS_ENGINE_HOME
  "server":  { "host": "127.0.0.1", "port": 8770 },
  "cdp":     { "host": "127.0.0.1", "port": 9222,
               "cookie_domain_filter": ["zhihuishu"], "timeout_sec": 20 },
  "cookie_file": "data/cookies.json",
  "data_dir": "data",
  "bus":     { "capacity": 3000 },
  "task":    { "default_speed": 1.5, "retry_delays": [2, 5],
               "sample_interval_sec": 10, "sample_interval_pct": 5 },
  "site":    { "course_url_template":
               "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId={rac}",
               "home_url": "https://studyvideoh5.zhihuishu.com/" },
  "browser": { "kind": "edge", "path": "", "profile_dir": "data/browser-profile",
               "profile_directory": "Default" }
}
```

> 红线：`config.example.json` 只放占位符；**不得**出现真实 rac、uuid、cookie、token 与本机绝对路径。

---

## 7. 审计复现 / 发布前自检命令

```powershell
# 本节命令针对**发布仓库根目录**执行（原审计是在本机工作目录跑的，dist\ 前缀已去掉）。

# 1) 凭证关键词（发布前必须 0 命中）
Get-ChildItem . -Recurse -File | Select-String -Pattern 'apiKey|api_key|password|token|secret|PRIVATE KEY|CASLOGC|CASTGC|jt-cas'

# 2) 任意盘符绝对路径（应 0 命中）
#    用通用模式而不是写死某个盘符路径，避免「扫描串自己命中自己」
Get-ChildItem . -Recurse -File | Select-String -Pattern '[A-Za-z]:\\[A-Za-z]'

# 3) 真实 rac / uuid 痕迹（应 0 命中；模式在本机临时填入真实值后扫，勿把真实值写进仓库）
Get-ChildItem . -Recurse -File | Select-String -Pattern '<真实rac前缀>|<真实uuid前缀>'

# 4) 敏感目录/文件是否被排除
Test-Path .\cookies.json, .\data, .\shots, .\logs, .\__pycache__
Get-ChildItem . -Recurse -Include *.pyc, exam-answers.json, task-state.json*

# 5) 依赖可复现（干净环境）
python -m venv .venv-clean; .\.venv-clean\Scripts\pip install -r requirements.txt
```

---

## 8. 审计局限性（如实声明）

1. 本审计是**静态阅读 + 关键词扫描 + 实测 venv**，未做动态污点追踪；运行时才生成的敏感数据（事件流、弹题流水）按代码路径推断，未逐条核对全部 15 个 `sessions` 目录内容。
2. `web/index.html` 共 782 行，逐段核对了状态默认值、URL 模板、轮询与超时；样式（CSS）部分不属于审计目标，未逐行审。
3. `shots/**` 共 112 个文件，**未看图**——「截图含 uuid 前 6 位与课程名」是基于 `index.html:499` 的渲染逻辑推断的（该字符串确实在顶栏常驻），属**高置信推断**而非逐张确认。
4. 上游许可证结论同时基于本地 `LICENSE` 与 GitHub API（两者一致）；但上游**依赖树许可证**与**素材版权**未逐项核对，已在 §4 标为「未查证，需人工确认」。
5. 版本号 `openai==3.52.0` 等异常命名的分发包来源未溯源（见 §5.3 第 4 点），打包前需人工复验。
