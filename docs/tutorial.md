# 教程：从零开始到刷完一门课

> ⚠️ **先读 [`DISCLAIMER.md`](../DISCLAIMER.md)。** 本项目仅供个人学习研究，禁止代刷、代考、商业牟利。使用风险自担。

这篇教程假设你**从来没接触过这个项目**，从下载到看数据全部走一遍。

---

## 目录

- [第 0 步：选一条路](#第-0-步选一条路)
- [路线 A：用打包好的 exe（不装 Python）](#路线-a用打包好的-exe不装-python)
- [路线 B：从源码跑（可控，能改）](#路线-b从源码跑可控能改)
- [第 2 步：装引擎（两条路都要做）](#第-2-步装引擎两条路都要做)
- [第 3 步：抓登录态](#第-3-步抓登录态)
- [第 4 步：开始刷课](#第-4-步开始刷课)
- [第 5 步：看数据、做复盘](#第-5-步看数据做复盘)
- [常见问题](#常见问题)

---

## 第 0 步：选一条路

| | 路线 A：exe | 路线 B：源码 |
|---|---|---|
| 需要装 Python | ❌ 不用 | ✅ 要 3.10+ |
| 启动方式 | 双击 `ZHStudio.exe` | `python run.py` + 浏览器 |
| 能改代码 | ❌ | ✅ |
| 适合谁 | 只想用 | 想研究、想改、想二次开发 |

两条路的**功能和数据完全一样**——都调同一个 `app.py`，同一个引擎，同一套接口。桌面版只是把浏览器换成了独立窗口。

---

## 路线 A：用打包好的 exe（不装 Python）

### A1. 拿到 exe

从 [Releases](../../releases) 下载 `ZHStudio-v1.1.0-win64.zip`，解压到任意目录（**路径别带中文和空格**，省事）。

解压后目录长这样：

```
ZHStudio/
├── ZHStudio.exe          ← 双击它
├── config.json           ← 配置（第一次要改）
├── config.example.json
├── _internal/            ← 别动，Python 运行时
└── ...
```

也可以自己构建，见 [`desktop/README.md`](../desktop/README.md)。

### A2. 双击运行

第一次双击，窗口标题会是 **「ZHS Studio — 缺少 fuckZHS 引擎」**，页面上给三步指引。**这是正常的**——你还差第 2 步装引擎。

按页面指引配好引擎后，关掉窗口重新双击，就会进正常面板。

> **Defender 拦了怎么办？** PyInstaller 打的包常被误报。加排除：
> ```powershell
> Add-MpPreference -ExclusionPath "D:\你的路径\ZHStudio"
> ```
> 注意是 `ZHStudio` 这一层（含 `_internal` 的那层），不是上一级。

---

## 路线 B：从源码跑（可控，能改）

### B1. 拉代码

```powershell
git clone https://github.com/lugabee-labs/zhs-studio.git
cd zhs-studio
```

### B2. 建虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

要求 **Python 3.10+**（代码用了 `str | None` 语法）。实测 3.12.8。

> `openai` 和 `tiktoken` 是引擎的**顶层依赖**，就算你完全不用 AI 功能也必须装——否则 `import fucker` 直接失败。`requirements.txt` 里已经含了，别删。

### B3. 改配置

```powershell
Copy-Item config.example.json config.json
notepad config.json
```

最少改一个字段（见第 2 步）：

```json
{
  "engine_home": "D:\\tools\\fuckZHS\\fuckZHS-master"
}
```

### B4. 启动

```powershell
python run.py
```

或者 Windows 上一键（会自动开浏览器）：

```powershell
.\start.ps1
```

看到这个就是起来了：

```
ZHS Studio → http://127.0.0.1:8770/
```

浏览器打开这个地址就是面板。

---

## 第 2 步：装引擎（两条路都要做）

面板本身不会 HTTP 请求——真正的 API 调用（拿课程树、上报进度、读弹题答案）由独立的引擎项目 [`fuckZHS`](https://github.com/VermiIIi0n/fuckZHS) 完成。**必须单独装它。**

### 2.1 拉引擎

```powershell
cd D:\tools
git clone https://github.com/VermiIIi0n/fuckZHS.git
```

得到 `D:\tools\fuckZHS\fuckZHS-master\`，里面能看到 `fucker.py` 就对了。

### 2.2 装引擎的依赖

引擎有自己的 `requirements.txt`：

```powershell
cd D:\tools\fuckZHS\fuckZHS-master
pip install -r requirements.txt
```

> 用**同一个 Python 环境**装。如果你走路线 B，就在同一个 venv 里装。

### 2.3 告诉面板引擎在哪

改 `config.json` 的 `engine_home` 指向那个目录：

```json
{
  "engine_home": "D:\\tools\\fuckZHS\\fuckZHS-master"
}
```

**用双反斜杠或正斜杠**，别写单反斜杠（JSON 转义）。

也可以用环境变量覆盖：

```powershell
$env:ZHS_ENGINE_HOME = "D:\tools\fuckZHS\fuckZHS-master"
```

---

## 第 3 步：抓登录态

面板复用你**浏览器里已登录**的会话，不用在面板里输密码。

### 3.1 让 Edge 带调试端口启动

普通启动的 Edge 无法被读取 cookie。用附带脚本重启一次：

```powershell
.\start-edge-debug.ps1
```

它会：关闭现有 Edge → 用**同一个 profile** 带 `--remote-debugging-port=9222` 重启 → 打开课程页。

> 登录态在 profile 里，**不用重新登录**。但要先保存浏览器里没提交的表单。
>
> macOS / Linux 手动起：
> ```bash
> # macOS
> "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" --remote-debugging-port=9222
> ```

### 3.2 确认已登录

在 Edge 里打开 [studyvideoh5.zhihuishu.com](https://studyvideoh5.zhihuishu.com/)，看到自己的课程列表就说明登录着。

### 3.3 点「抓登录态」

回到面板 http://127.0.0.1:8770/ ，点右上角 **「抓登录态」**。

成功后顶部会变成：

```
● 已登录 · 103 cookie · EYwOJw***
```

### 3.4 抓不到的三种情况

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `1 cookie` / `uuid=None` | **抓太早了**，Edge 刚启动 cookie 还没从磁盘加载完 | 等 30 秒再点一次 |
| `连接被拒绝` | Edge 不是带调试端口启动的 | 重跑 `start-edge-debug.ps1` |
| 抓到了但拉课程 `401 / Cookies invalid` | 登录态本身过期了 | 在 Edge 里重新登录智慧树，再抓一次 |

登录态会因为**在手机 App 或别处再登录一次**而失效（单点登录）。这是平台机制，不是 bug。

---

## 第 4 步：开始刷课

### 4.1 选课

左栏点课程 → 中栏出现章节树（每章的测验状态、每集的完成度）。

### 4.2 设参数

右栏三个开关：

| 参数 | 建议 | 说明 |
|---|---|---|
| **播放倍速** | 1.5× | 平台能接受的上限。实测推进约 1.64×，设 1.8× 以上有风控风险 |
| **单课限时** | 0（不限） | 想跑一夜就填 0；想跑两小时就填 120 |
| **跳过已完成的课时** | 开 | 断点续刷靠它，重跑不会重播已完成的集 |

### 4.3 点「开始刷课」

然后可以关掉浏览器（**但不能关服务**）。任务在后台跑，页面上能看：

- **进度环**：本集播放百分比
- **事件流**：`play` / `progress` / `done` / `answer` / `error`，逐条实时
- **答题记录**：每道弹题问了什么、答了什么

### 4.4 中途会遇到的

**验证码**：跑几轮之后可能弹验证码（实测 14 轮里 2 轮），任务会自动停下，状态变 `captcha`，页面顶部出提示条。

处理：去 Edge 里手动过一次验证码 → 回面板继续。**这个必须人工**，绕不过。

**随机暂停**：引擎会以 0.25%/秒的概率触发一次 60 秒暂停（防检测）。这是**设计行为**，不是卡住了。实测平均每集 0.85 次。

**`-10 疑是多窗口观看`**：说明有另一个浏览器标签正开在同一个课程播放页，和面板抢同一个 watch 会话。关掉那个标签，等 90 秒再继续。

---

## 第 5 步：看数据、做复盘

每点一次「开始刷课」，就在 `data/sessions/<run_id>/` 下留一份档案：

| 文件 | 内容 |
|---|---|
| `meta.json` | 课程、参数、起止时间、结果摘要、失败清单 |
| `events.jsonl` | 全量事件（一行一条，文件里不丢） |
| `episodes.jsonl` | 每集一行：`videoId / name / sec / elapsed / ok / answers / reason` |
| `answers.jsonl` | 本轮弹题明细 |
| `progress.csv` | 进度采样时序 `ts,videoId,pct` |

汇总成报告：

```powershell
python tools/report.py
```

输出 `data/report.md`：每集多久、哪里失败、弹题答了什么、总体进度。

> **关于 91%**：「已完成」是指平台记账完成（引擎播到视频时长 91% 就停止上报），**不等于播满 100%**。这是引擎的默认行为，可以在代码里改 `end_thre`，但改到 1.0 以上会明显增加风控概率。

---

## 常见问题

**Q：面板打不开 / 一片空白**
A：先看服务是否在跑：`curl http://127.0.0.1:8770/api/health`。返回版本号说明服务正常，问题在页面——按 `Ctrl+F5` 硬刷新。如果 `/api/courses` 返回 401，是登录态过期，回第 3 步。

**Q：手机能看吗？**
A：可以。把 `config.json` 的 `server.allow_lan` 改成 `true`，重启服务，启动日志会打印 `http://192.168.x.x:8770/` 这样的地址，手机连同一个 WiFi 就能访问。
⚠️ **服务没有鉴权**，`/api/cookie/grab` 还会写登录态文件。只在可信网络里开，用完关掉。

**Q：能同时刷多门课吗？**
A：不能。同一账号多进程会触发平台的 `-10 疑是多窗口观看`。一门一门来。

**Q：为什么设了 1.5× 实际更快？**
A：实测推进约 1.64×（中位 1.637，样本 70 集），比设置值高约 9.1%。**差额原因现有数据无法判定**，不做猜测。

**Q：桌面版双击没反应**
A：看日志 `dist_exe\ZHStudio\logs\desktop.log`。最常见是引擎路径没配（窗口会显示「缺少 fuckZHS 引擎」并提供生成模板按钮）。

**Q：能刷 AI 角标的课吗？**
A：不能。那类课程的视频不是普通播放器，走的是另一套接口。

**Q：进度显示 100% 但平台说没完成**
A：面板按「平台记账完成」显示。以平台课程页的真实进度为准，两边口径不同。

---

## 最后

- 所有实测数据来自 **14 轮真实运行档案**（14,120 条事件 / 83 集 / 104 条弹题），不是估算，口径见 [`log-analysis.md`](log-analysis.md)。
- 遇到问题先看 [`architecture.md`](architecture.md) 的「踩坑记录」一节，大部分诡异现象都在那里有解释。
- 再读一遍 [`DISCLAIMER.md`](../DISCLAIMER.md)。真的。
