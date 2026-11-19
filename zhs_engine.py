# -*- coding: utf-8 -*-
"""外部引擎（fuckZHS）加载器 —— 全仓库唯一与「引擎装在哪」耦合的地方。

为什么要单独一层

    上游 https://github.com/VermiIIi0n/fuckZHS （MIT, © VermiIIi0n）是**平铺的脚本集合**：
    没有 pyproject.toml / setup.py，模块之间用平铺名互相 import（`utils` / `logger` /
    `zd_utils` / `ObjDict`）。因此只能把引擎目录放进 sys.path 再 import——这一步以前硬写在
    app.py 里、路径写死成某个人的 D 盘目录，开源后必挂。现在收敛到这里：

    * 目录由 config.json 的 `engine_home`（或环境变量 ZHS_ENGINE_HOME）指定；
    * 启动前校验必备文件，缺什么都直接说清楚；
    * 上游换版本/换目录，只改这个文件（或只改配置），app.py 不用动。

注意

    本仓库**不包含**上游源码（不 vendor），用户按 README 自行 clone。这样本项目的 MIT
    许可与上游的 MIT 许可互不干扰，也不需要再分发第三方代码。
"""
import importlib
import os
import sys
from dataclasses import dataclass

# 上游根目录应当同时具备这些文件（缺一个基本就是路径指错了）
REQUIRED_FILES = ("fucker.py", "utils.py", "logger.py", "zd_utils.py", "ObjDict.py")

INSTALL_HINT = (
    "安装引擎（二选一）：\n"
    "  A. git clone https://github.com/VermiIIi0n/fuckZHS.git <目录>\n"
    "  B. 下载 zip 解压到任意目录（目录里应能看到 fucker.py / utils.py …）\n"
    "然后把该目录填进 config.json 的 engine_home（绝对路径），"
    "或设置环境变量 ZHS_ENGINE_HOME=<该目录>。"
)


class EngineNotFound(RuntimeError):
    """引擎目录未配置 / 不存在 / 不完整。"""


@dataclass
class Engine:
    """上游暴露给本项目的最小接口面。"""
    module: object              # fucker 模块本身（app.py 用它做 progressBar 注入）
    Fucker: object              # class Fucker
    CaptchaException: type
    TimeLimitExceeded: type
    home: str                   # 解析后的绝对路径


def _missing_files(home: str) -> list:
    return [f for f in REQUIRED_FILES if not os.path.isfile(os.path.join(home, f))]


def load_engine(engine_home: str | None) -> Engine:
    """定位并 import 上游引擎；失败时抛带指引的 EngineNotFound。"""
    if not engine_home:
        raise EngineNotFound("配置项 engine_home 为空。\n" + INSTALL_HINT)

    home = os.path.abspath(os.path.expanduser(str(engine_home)))
    if not os.path.isdir(home):
        raise EngineNotFound(f"engine_home 不是目录：{home}\n" + INSTALL_HINT)

    miss = _missing_files(home)
    if miss:
        raise EngineNotFound(
            f"引擎目录不完整，缺少 {', '.join(miss)}：{home}\n"
            "（engine_home 应该指向 fuckZHS 仓库的根目录，而不是它的子目录）\n" + INSTALL_HINT
        )

    # 上游模块用平铺名互相 import，必须把引擎目录加到 sys.path 最前面。
    # `utils` / `logger` 是很通用的名字：若你的环境里已经装了同名包，务必用独立 venv
    # 跑本项目（README「安装」一节有说明）。
    if home not in sys.path:
        sys.path.insert(0, home)

    try:
        fzmod = importlib.import_module("fucker")
    except Exception as e:      # 引擎自己的 import 错误（例如缺 requests/openai）原样抛
        raise EngineNotFound(
            f"import 引擎失败（{home}）：{type(e).__name__}: {e}\n"
            "常见原因：依赖没装全 —— pip install -r requirements.txt"
        ) from e

    for attr in ("Fucker", "CaptchaException", "TimeLimitExceeded"):
        if not hasattr(fzmod, attr):
            raise EngineNotFound(
                f"引擎版本不兼容：fucker 模块里没有 {attr}（{home}）。\n"
                "本项目按 fuckZHS 2.6.x 的接口编写，请换用该版本。"
            )

    return Engine(
        module=fzmod,
        Fucker=fzmod.Fucker,
        CaptchaException=fzmod.CaptchaException,
        TimeLimitExceeded=fzmod.TimeLimitExceeded,
        home=home,
    )
