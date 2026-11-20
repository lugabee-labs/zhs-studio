# ZHS Studio 桌面端（Windows）

> **状态：已验证构建** —— 2026-11-20 在本机（Python 3.12.8 / PyInstaller 6.22.3 / pywebview 6.2.1）实际出包并双击运行通过：窗口加载出真实面板、`/api/health` 探活成功、关窗后后端与端口一起释放、`allow_lan` 能打印局域网地址。下文数字均为**实测**；源码或 spec 改动后需重新验证一遍。

一个 exe，双击就开一个**原生窗口**（WebView2），窗口里是 ZHS Studio 面板本体——
不经过浏览器、不占浏览器标签页、关掉窗口就把后台服务一起停干净。

三端定位（别搞混）：

| 端 | 是什么 | 能不能真刷课 |
| --- | --- | --- |
| 浏览器里的 `web/` 面板 | 调试/开发用 | 能（PC 上跑 `python run.py`） |
| **本目录的 Windows 桌面端** | 打包成 exe 的本体，起后端 + 开原生窗口 | **能**（就是 PC 上那套引擎） |
| Android 遥控端（规划中，本仓库当前**没有** `android/` 目录） | 手机上的遥控器 + 进度查看 | 不能——刷课依赖 PC 上的 Edge CDP + Python 引擎，APK 里装不下这套东西 |

桌面端和 `python run.py` 跑的是**同一个 `app.py`**，同一份 `config.json` 语义。
区别只有三点：窗口是 WebView2、端口会自动避让占用、日志写文件而不是控制台。

---

## 1. 目录里有什么

```
desktop/
├── app_desktop.py    启动器：后台线程起 uvicorn → 探活 /api/health → 开 pywebview 窗口
├── ZHStudio.spec     PyInstaller 打包配置（onedir）
├── build.ps1         一键构建：找 venv python → pyinstaller → 打印体积/耗时
└── README.md         本文件
```

构建产物（不入库）：

```
dist_exe\ZHStudio\
├── ZHStudio.exe          ← 双击这个（约 12 MB，只是个启动器）
├── config.example.json   ← 构建时自动复制过来的样例
└── _internal\            ← 依赖 + web/ 面板 + WebView2 托管 dll（约 44 MB）
```

> `dist_exe/` 已经在仓库根的 `.gitignore` 里。exe 与 `_internal/` 一律不入库，
> 仓库里只留源码与构建脚本。

---

## 2. 前置条件

| 项 | 要求 | 说明 |
| --- | --- | --- |
| Windows | 10 1809+ / 11 | WebView2 的老家 |
| Python | 3.10+（实测 3.12.8） | 只用于**构建**；最终用户不需要装 Python |
| WebView2 Runtime | 已装（Win11 一般自带，实测 157） | 缺了会降到系统浏览器打开，并弹窗提示装 |
| 构建依赖 | `pyinstaller`、`pywebview` | 见下面两条命令 |

```powershell
# 在仓库根执行；依赖必须装进同一个解释器（本例用已有的 fuckZHS venv）
$py = "D:\tools\fuckZHS\fuckZHS-master\.venv\Scripts\python.exe"
& $py -m pip install -r .\requirements.txt
& $py -m pip install pyinstaller pywebview
```

`requirements.txt` 里的 openai / tiktoken / requests / pycryptodome / pillow 看着像“本项目用不到”，
但 `fucker.py` 在**模块顶层**就 import 了它们——引擎在运行期从 `engine_home` 挂进来时，
这些包必须在冻结后的解释器里存在，所以它们会被一起打进 exe（见 `ZHStudio.spec` 的 hiddenimports）。

---

## 3. 构建

```powershell
# 仓库根执行
.\desktop\build.ps1

# 指定解释器（不指定时按 ZHS_BUILD_PYTHON → .venv → fuckZHS venv → PATH 找）
.\desktop\build.ps1 -Python "D:\tools\fuckZHS\fuckZHS-master\.venv\Scripts\python.exe"

# 改完代码想快点出包，跳过 PyInstaller 缓存清理（改了 spec 就别跳）
.\desktop\build.ps1 -NoClean
```

实测（本机 Python 3.12.8 / PyInstaller 6.22.3 / pywebview 6.2.1）：

| 项 | 数值 |
| --- | --- |
| 首次构建（空缓存） | **约 96 s** |
| 增量构建（缓存热） | **约 26 s**（PowerShell 5.1 下 44 s） |
| `ZHStudio.exe` | 11.7 MB（启动器；大头在 `_internal\`） |
| 整包（201 个文件，干净的首次产物） | **55.7 MB** |
| 冷启动 → 后端就绪 | **1.2 ~ 2.1 s**（首次运行要先建 WebView2 profile，会到 3~8 s） |
| 冷启动 → 窗口出现 | **约 2 ~ 7 s**（慢的是引擎 import，不是窗口本身） |
| 常驻内存 | 约 145 MB（WebView2 渲染进程另算） |
| 关窗 → 后端停止 | **0.13 s**，进程 1~4 s 内退出，端口不再 LISTEN |

构建脚本会打印上面这些数字，结尾提示下一步。

> `ZHStudio.spec` 文件头注释里的「onedir 约 120~180 MB」是构建前的估算，**实测 55.7 MB**——以构建脚本打印的数字为准。
> 跑过一次之后目录会变大（`data\webview\` 是 WebView2 的 profile 缓存，实测多出 ~6 MB / 160 个文件），这是正常的，不是构建产物膨胀。

---

## 4. onedir 还是 onefile？

**选的是 onedir。** 理由不是体积，是路径语义：

| | onedir（本仓库） | onefile |
| --- | --- | --- |
| 启动 | 直接读本地文件，快 | 每次启动把 ~56 MB 解压到 `%TEMP%\_MEIxxxxx`，冷启动多 2~4 s |
| `sys._MEIPASS` | `<exe目录>\_internal`，**持久** | `%TEMP%\_MEIxxxxx`，**用完就清** |
| `data/`、`cookies.json`（登录态） | 落在 exe 旁边，重启还在 | 默认会落进临时目录，关掉就丢 |
| 分发 | 整个目录打 zip | 单个 exe |
| 杀软误报 | 相对低 | 相对高（自解压行为更像打包器） |

想要单文件分发：把 spec 结尾的 `COLLECT(...)` 整段删掉，并把 `EXE(...)` 改成把二进制与资源一起塞进去的形式：

```python
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="ZHStudio", console=False, upx=False,   # 其余参数与现在一致
)
# 不要 COLLECT
```

`EXE` 里不再写 `exclude_binaries=True`（那是 onedir 专用的），代价见上表后两行：冷启动多解压一轮、`data/` 落进临时目录（启动器已经把路径钉到 exe 目录，这一条影响会被启动器兜住，但每次启动仍会多一份解压开销）。

---

## 5. 首次运行：必须配 `config.json`

引擎（fuckZHS）**不打包进 exe**（体积 + 许可都不合适），运行时按配置去挂。所以第一次双击时会看到
一个说明页——不是崩溃，是“还差一步”：

```
缺少 fuckZHS 引擎
  ├─ 发生了什么： EngineNotFound: 配置项 engine_home 为空。
  ├─ 程序目录：   D:\...\dist_exe\ZHStudio
  └─ 怎么办（三分钟）： 1) 准备引擎目录 2) 建 config.json 填 engine_home 3) 重启
```

说明页上有四个按钮，不用去翻资源管理器：

* **生成配置模板 config.json** —— 把内置的 `config.example.json` 复制成 `config.json`，
  路径类字段（`data_dir` / `shots_dir` / `cookie_file` / `browser.profile_dir`）已经填成
  相对 exe 目录的**绝对路径**，`engine_home` 被清空留给你填，然后自动用默认程序打开给你改；
* **打开程序目录** / **打开日志目录**；
* **退出**。

最小可用配置（放在 `ZHStudio.exe` 旁边）：

```json
{
  "engine_home": "D:\\tools\\fuckZHS\\fuckZHS-master",
  "server": { "host": "127.0.0.1", "port": 8770, "allow_lan": false }
}
```

要点：

* `engine_home` 必须指向**能看到 `fucker.py` 的那一层**（不是它的子目录），推荐写绝对路径；
* 其余键不写就用默认值，完整语义见仓库根 `config.example.json` 的 `_comment`；
* 也可以不给 exe 旁边这份，改用环境变量 `ZHS_ENGINE_HOME` / `ZHS_CONFIG`（与 `python run.py` 一致）；
* 配置改了要**重启程序**才生效（后端是进程内启动的）。

---

## 6. 双击运行 & 端口行为

* 双击 `ZHStudio.exe`：后台线程起 uvicorn（跑的就是 `app.py` 那个 FastAPI 实例），
  轮询 `GET /api/health` 确认活了，再开原生窗口；窗口标题 `ZHS Studio`
  （提示页时是 `ZHS Studio — 缺少 fuckZHS 引擎` / `ZHS Studio — 后端没能启动`），1400×900，最小 1100×700。
* **端口**：优先用配置里的 `server.port`（默认 8770）。被占了不会报错退出，而是自动换一个
  系统分配的可用端口——日志里会写 `端口 8770 已被占用，改用可用端口 57374`。
* **监听地址**：默认 `127.0.0.1`（只有本机）。要让手机连面板（遥控 + 看进度），把
  `server.allow_lan` 设成 `true`（等价于 `host: "0.0.0.0"`），重启后日志会打印
  `局域网可访问：http://192.168.x.x:8770/`，手机端填这个。
  ⚠️ 本服务没有账号鉴权、`/api/cookie/grab` 还会写出登录态文件，只在信得过的网络里开。
* **关窗口**：`WM_CLOSE` → `should_exit` → `join`（10 s 超时后退 `force_exit`）→ 端口释放。
  实测关窗后 0.13 s 后端停止、进程 1~4 s 内退出、端口不再 LISTEN。
* **想脚本化/排错**（不开窗口只看后端）：

  ```powershell
  .\ZHStudio.exe --no-window           # 只起后端并阻塞，Ctrl+C 结束
  .\ZHStudio.exe --no-window --port 8899
  .\ZHStudio.exe --version
  .\ZHStudio.exe --config D:\some\config.json
  Invoke-WebRequest http://127.0.0.1:8770/api/health | Select -Expand Content
  # {"ok":true,"version":"1.1.0","uptime_sec":12.3}
  ```

  窗口版是 `console=False`，**没有控制台**：`--version` 与 `--no-window` 的 stdout 都被重定向进
  `logs\desktop.log`（`--no-window` 就绪时会写 `READY http://127.0.0.1:<port>/`）。
  想直接看到输出，用源码跑：`python .\desktop\app_desktop.py --no-window`。

---

## 7. 数据与日志落在哪

全部在 **exe 旁边**（便携语义，卸载 = 删目录）：

```
dist_exe\ZHStudio\
├── config.json         你的配置（gitignored）
├── data\
│   ├── cookies.json    登录态（等于密码，别外传、别提交）
│   ├── runs\ sessions\ 运行档案
│   └── webview\        WebView2 的 profile（缓存/History，可随时删）
├── shots\              刷课过程的截图
└── logs\desktop.log    桌面端启动日志（UTF-8）
```

为什么不在 `_internal` 里：启动器会把配置里所有路径按 **exe 目录**解析成绝对路径，
再写一份临时归一化配置、用 `ZHS_CONFIG` 指给 `app.py`。
（`app.py` 自己是用 `dirname(app.py)` 当仓库根的——冻结后那是 `_internal`，不纠正的话
`data/` 会跑到 `_internal\data`，用户找不到，换机器也不会跟着走。）

---

## 8. 体积、签名与 Defender 误报

PyInstaller 产物被 Defender/SmartScreen 拦下是常态，尤其是：

* **未签名**（没有代码签名证书，任何新 exe 都是“未知发布者”）；
* **自解压/打包器行为**（onefile 更明显）；
* **新文件哈希没人给过信用**（刚构建出来的 exe 100% 命中启发式）。

本仓库已经做了能做的三件事：**onedir**（少自解压）、**不用 UPX**（UPX 压缩显著抬高误报）、
**不打系统级写操作**（所有数据都在自己目录里）。

给本机加排除（需要管理员 PowerShell）：

```powershell
# 推荐：只排除这个目录，别排除整个 D 盘
Add-MpPreference -ExclusionPath "D:\tools\zhs-studio\dist\dist_exe\ZHStudio"
# 查看现有排除项
Get-MpPreference | Select-Object -ExpandProperty ExclusionPath
# 撤销
Remove-MpPreference -ExclusionPath "D:\tools\zhs-studio\dist\dist_exe\ZHStudio"
```

> 上面的路径是本机仓库根 `D:\tools\zhs-studio\dist` 下的实际产物位置（`build.ps1` 把产物放在 `<仓库根>\dist_exe\ZHStudio`）。换机器就换成你自己的 `<仓库根>\dist_exe\ZHStudio`。

想彻底解决分发问题，正规路径是买 OV/EV 代码签名证书然后
`signtool sign /fd sha256 /a /f cert.pfx ZHStudio.exe`（EV 证书 + 累积声誉能显著减少
SmartScreen 拦截）。这是发布阶段的事，不进本仓库。

---

## 9. 已知限制（写清楚，别当 bug 修）

1. **不是单文件**：分发要带整个 `ZHStudio\` 目录，只拷 exe 会缺 `_internal`。
2. **相对路径按 exe 目录解析**，不是按 `config.json` 所在目录（`zhs_config.load(here=exe_dir)`）。
3. **只跑一个实例比较安全**：两个实例同时开，第二个会自动换端口；但它们共用同一个
   `data/`（含 `cookies.json`、`task-state.json`），并发写可能互相覆盖。要并行就别共用目录
   （复制一份 exe 目录或用 `--config` 指别的 data_dir）。
4. **没有自动更新、没有托盘、没有开机自启**：就是“双击—用—关窗口”。
5. **DPI**：窗口尺寸按逻辑像素给（1400×900），高 DPI 缩放下由 Windows 自己缩放。
6. **WebView2 缺失时降级**：弹窗提示 + 用系统默认浏览器打开同一个地址，后端继续跑；
   关浏览器不会停后端，需要用任务管理器结束进程。

---

## 10. 出问题时先看这三处

| 症状 | 先看 | 常见原因 |
| --- | --- | --- |
| 双击没反应 / 一闪就没 | `<exe目录>\logs\desktop.log` | 启动即失败会写 traceback；极早期崩溃看事件查看器 |
| 窗口里是“缺少 fuckZHS 引擎” | 日志里 `engine_home=` 那行 | 没配 `config.json` / 路径指到了子目录 / 引擎缺 `fucker.py` |
| 窗口里是“后端没能启动” | 页面里内嵌的日志尾巴 | 端口全被占、`app.py` 导入报错（如 openai 没装进包） |
| 面板能开但点啥都 404/500 | `Invoke-WebRequest http://127.0.0.1:<port>/api/state` | 后端版本与 `web/index.html` 不匹配（改完没重新构建） |
| 被 Defender 删了 exe | Windows 安全中心 → 保护历史记录 | 见上面第 8 节，加排除后重新构建 |

构建期排错：`build\pyinstaller-desktop\ZHStudio\warn-ZHStudio.txt` 里有完整的 hidden import
缺失清单（`ERROR: Hidden import 'xxx' not found` 是真问题，`pycparser.lextab` / `tzdata`
这类 WARNING 无害）。
