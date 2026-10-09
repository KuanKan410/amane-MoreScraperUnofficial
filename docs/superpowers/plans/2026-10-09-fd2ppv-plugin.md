# fd2ppv.cc 刮削插件（unofficialscraper.fd2ppv）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增插件 `unofficialscraper.fd2ppv`，把 fd2ppv.cc 作为 FC2 元数据源接入 Amane；顺手把 `.codegraph/`、`.playwright-mcp/` 等开发目录加进 `.gitignore`。

**架构：** 单文件插件（与仓库现有 5 个插件一致）：`unofficialscraper.fd2ppv/plugin.py`。结构照搬 `esl1.supfc2/plugin.py`——宿主 SDK 握手 + Pydantic 配置 + Provider + 逐字复制的 CDP 浏览器块。番号 → `/articles/<digits>` 直达详情页；普通 HTTP 优先，撞 Cloudflare 挑战时起本机浏览器兜底。**解析层用标准库 `re`**（站点是服务端渲染的静态 HTML，结构规整；本机 anaconda 无 lxml/cssselect → 无 parsel，用正则既零依赖又可单测）。

**技术栈：** Python 3.14（amane 内嵌解释器）、Pydantic v2、标准库 `re`/`json`/`asyncio`/`socket`/`subprocess`（手写 CDP WebSocket 客户端）。测试：pytest 9 + anaconda Python。

**规格：** 本计划自带设计依据（见下方「附录 A：站点实测结论」）。这是 brainstorming 技能里的一条 **Spike（探路）** 产出——结论已用 Playwright 在真实站点上逐字段验证，本文档即该结论的落地规格。

---

## 全局约束

- **插件 ID `unofficialscraper.fd2ppv`**，`content_types=frozenset({"fc2"})`，`rate_limit=1.0`，版本 `0.1.0`。
- **只允许标准库 + 宿主自带的 `pydantic`**；不得引入 parsel / lxml / httpx 等第三方包（插件跑在打包好的 amane 里）。
- **网络请求一律走 `context.http_client.get_html(url, headers=, cookies=)`**；浏览器只用于过 Cloudflare 挑战。
- 插件目录名、类名、方法名、配置字段名**用英文**；`Field(title=..., description=...)` 里的**文案用中文**（宿主的配置表单按 JSON Schema 渲染，插件专属字段没有内置 i18n 词条）。
- **未命中返回 `None`**，不算失败；网络/解析失败在 `report_misses=True` 时抛 `SourceError(FailureReason.NO_USABLE_METADATA, detail=...)`。
- 复制 CDP 块时**必须连它的 import 一起复制**（`esl1.supfc2/plugin.py:1376-1379` 有踩坑记录）。
- 提交信息用中文，前缀沿用仓库风格（`feat:` / `docs:` / `chore:`）。

---

## 附录 A：站点实测结论（2026-10-09，Playwright 真机验证）

**URL 与访问**

| 项 | 结论 |
| --- | --- |
| 详情页路由 | `https://fd2ppv.cc/articles/{digits}`，由番号可直接拼出，**不需要搜索** |
| 收录判定 | 未收录返回 **HTTP 404**（实测 `99999999` → 404） |
| 反爬 | 详情页在 **Cloudflare Managed Challenge** 后面（裸 HTTP → 403 挑战页）；真浏览器自动过 |
| 登录 | **不需要登录** |
| `cf_clearance` | **HttpOnly**（`document.cookie` 里看不到），只能靠 CDP `Network.getCookies` 导出 |
| 页面形态 | 服务端渲染的静态 HTML（非 Inertia / 非 JSON 接口） |

**字段映射（实测 6 个作品逐一核对）**

| 目标字段 | 来源 | 实测样例 |
| --- | --- | --- |
| `number` | `<h1 class="work-title">` 的文本 | `4989610` |
| `title` | **优先** `<script type="application/ld+json">` 的 `description`（已去前缀）；**兜底** `<meta name="description">` 去掉开头 `FC2 PPV <id> ` | `無修正、第２弾、黒髪清楚素人美女、…` |
| `release` | `.work-meta` 里标签「配信日」对应的值 | `2026-10-09`（可能为空串） |
| `runtime` | `.work-meta` 里「収録時間」的值，`H:MM:SS` → 分钟 | `00:58:46` → `58` |
| `studio` | `.work-meta` 里「販売者」的值（值为 `不詳` 时取 `None`） | `ひらめき無無剣` |
| `tags` | `.work-tags` 内的 `a[href*="/tags/"]` 文本（**必须限定在 `.work-tags` 内**，全页搜会命中导航链接「タグ」） | `中出し`, `コスプレ`, `体操服` |
| `actors` | `.artist-name` 文本；`不詳` 要过滤掉 | `元アイドルちゃん`, `来栖ユリ` |
| `poster_urls` / `thumb_urls` | `.work-photos` 里**含 `contents.fc2.com` 的那条**（官方封面） | `https://storage201000.contents.fc2.com/file/.../x.jpg` |
| `extrafanart` | `.work-photos` 里**其余**条目（`xximgs.cc` 剧照墙，2–11 张） | `https://xximgs.cc/uploads/2641/xxxx.webp` |

**真实标记样例（逐字摘自 4989610 页面）**

```html
<h1 class="work-title">4989610 <span class="cursor-copy" onclick="copyboard(this)" data-text="4989610" data-success="作品IDをコピーしました : 4989610"><i class="icon icon-copy"></i></span></h1>

<meta name="description" content="FC2 PPV 4989610 無修正、第２弾、黒髪清楚素人美女、…">

<script type="application/ld+json">{"@context":"https://schema.org","@type":"Movie","@id":"https://fd2ppv.cc/articles/4989610","name":"FC2-PPV-4989610","description":"無修正、第２弾、黒髪清楚素人美女、…","datePublished":"2026-10-09 08:34:19"}</script>

<div class="work-image-large work-photos hidden" data-id="alZ0ak9mSGliYnFsVFQ1dGFuQlF0dz09">https://xximgs.cc/uploads/2641/6ac8368b9cce3387.webp https://xximgs.cc/uploads/2641/6ac8368baddad831.webp … https://contents-thumbnail2.fc2.com/w276/storage202000.contents.fc2.com/file/385/38437260/1791463320.51.jpg</div>

<div class="work-meta">
  <div class="work-meta-label">カテゴリ</div> <div class="work-meta-value"><span class="color_free0"><i class="icon icon-cloud_play"></i> 未流出</span></div>
  <div class="work-meta-label">配信日</div> <div class="work-meta-value">2026-10-09</div>
  <div class="work-meta-label">収録時間</div> <div class="work-meta-value" id="duration">00:23:39</div>
  <div class="work-meta-label">配信元</div> <div class="work-meta-value">FC2</div>
  <!--seller--><div class="work-meta-label">販売者</div> <div class="work-meta-value"><a href="/channels/houkeikingjapan">包茎キングジャパン公式</a></div><!--seller-->
  <div class="work-meta-label">リンク</div> <div class="work-meta-value flex flex-top gap-m flex-wrap"><a href="https://adult.contents.fc2.com/article/4989638/" target="_blank">詳しくはこちら <i class="icon icon-launch"></i></a></div>
</div>

<div class="work-tags"><a href="/tags/articles/creampie" target="_blank"><i class="icon icon-tag"></i>中出し <i class="icon icon-launch"></i></a><a href="/tags/articles/cosplay" target="_blank"><i class="icon icon-tag"></i>コスプレ <i class="icon icon-launch"></i></a></div>

<h3 class="artist-name"><span>元アイドルちゃん</span> <span class="cursor-copy hidden" onclick="copyboard(this)" data-text="元アイドルちゃん"><i class="icon icon-copy"></i></span></h3>
```

**已核实的坑**

1. `.work-tags` 常常是**空 div**（如 4989588 / 4989527 无标签）；`.artist-name` 常为 `不詳`。两者都要容错。
2. `.artist-tags` 是**女优画像的标签**（如「剛毛風俗在籍…」），**不是作品标签**，别拿来当 `tags`。
3. `収録時間` 可能为 `00:00:00` → 视为无时长（返回 `None`）。
4. `販売者` 可能为 `不詳` → 视为无片商。
5. `.work-photos` 里的 FC2 官方封面**可能被 `contents-thumbnail2.fc2.com/w276/` 包裹**，需还原成真实 storage 域名（`esl1.supfc2` 的 `_canonical_image_url` 已处理这种包裹）。
6. 全页 `a[href*="/tags/"]` 会命中导航栏的 `/tags/articles/`（文本「タグ」），必须把范围限制在 `.work-tags` 内。
7. 页面广告很多（ExoClick / magsrv / marzaent），与解析无关，**不要**引入任何广告域名。

---

## 附录 B：复制的模板段落（源：`esl1.supfc2/plugin.py`）

| 用途 | 源行号 | 处理方式 |
| --- | --- | --- |
| 宿主 SDK 握手（`_SDK_API_NAMES` / `_API_PLAN` / `_load_host_api` / `_write_import_report` / 名字绑定） | `esl1.supfc2/plugin.py:113-217` | **逐字复制**，仅把 `Supfc2` 字样按需替换 |
| CDP 浏览器块（`_WIN_BROWSERS` … `browser_fetch`） | `esl1.supfc2/plugin.py:1365-1929` | **逐字复制**，含它自带的 import（1382-1389） |

`browser_fetch(...)` 的返回结构（已核实，`esl1.supfc2/plugin.py:1920-1929`）：

```python
{
    "html": str | None,      # 过挑战成功时的 document.documentElement.outerHTML；被拦时为 None
    "blocked": bool,
    "cookies": list[dict],   # CDP Network.getCookies 的原样结果（含 HttpOnly 的 cf_clearance）
    "cookie_header": str,    # "name=value; name=value"
    "user_agent": str,
    "url": str,
    "raw_html": str,         # 无论是否被拦都有
    "diag": list[str],
}
```

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `unofficialscraper.fd2ppv/plugin.py` | **新建。** 插件本体：SDK 握手 + 配置 + 纯解析函数 + Provider + Plugin 入口 + 复制的 CDP 块 |
| `tests/conftest.py` | **新建。** 打桩 `amane.*` 宿主 SDK，用 `importlib` 按路径加载 `unofficialscraper.fd2ppv/plugin.py`，导出 `PLUGIN` |
| `tests/test_fd2ppv_parse.py` | **新建。** 纯解析函数与 `_build_meta` 的单元测试 |
| `.gitignore` | **修改。** 加入开发/运行产物 |
| `README.md` | **修改。** 插件列表加一行 |

`plugin.py` 内部分区（自上而下）：模块 docstring → import → UA/请求头常量 → SDK 握手 → 站点常量与正则 → 配置类 → 纯函数 → Provider → `Plugin` → CDP 块。

---

### 任务 1：`.gitignore` 补齐开发目录

**文件：**
- 修改：`.gitignore`（当前只有 `__pycache__/` 和 `*.py[cod]` 两行）

- [ ] **步骤 1：把 `.gitignore` 换成下面这份**

```gitignore
# Python
__pycache__/
*.py[cod]
*.pyo
.pytest_cache/
.venv/
venv/

# 开发工具产生的目录（本地分析用，不进版本库）
.codegraph/
.playwright-mcp/
.claude/

# 插件运行时落在插件目录里的产物
import-report.txt
debug/
*.log
cf_session.json
browser-profile/
```

- [ ] **步骤 2：验证忽略规则生效**

运行：
```bash
git check-ignore -v .codegraph .playwright-mcp import-report.txt esl1.supfc2/debug/debug.log
```
预期：四行全部命中，各打印一条 `.gitignore:<行号>:<规则>	<路径>`。

- [ ] **步骤 3：确认工作区变干净**

运行：`git status --short`
预期：**不再出现** `?? .codegraph/` 与 `?? .playwright-mcp/`（只剩本任务对 `.gitignore` 自身的修改）。

- [ ] **步骤 4：Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore 忽略 .codegraph/.playwright-mcp 等开发与运行产物"
```

---

### 任务 2：插件骨架 + 测试脚手架

交付物：`unofficialscraper.fd2ppv/plugin.py` 能被 amane 加载（有正确的 `Plugin` 类与描述符），`fetch()` 暂时恒返回 `None`；本地 pytest 能导入它并校验描述符。

**文件：**
- 创建：`unofficialscraper.fd2ppv/plugin.py`
- 创建：`tests/conftest.py`
- 测试：`tests/test_fd2ppv_parse.py`

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_fd2ppv_parse.py`：

```python
"""unofficialscraper.fd2ppv 的单元测试（只测纯逻辑；浏览器相关靠实机验证）。"""


def test_descriptor_is_well_formed():
    desc = PLUGIN.Plugin.descriptor()
    assert desc.id == "unofficialscraper.fd2ppv"
    assert desc.content_types == frozenset({"fc2"})
    assert "film_metadata" in desc.capabilities or PLUGIN.SourceCapability.FILM_METADATA in desc.capabilities
    assert "title" in desc.metadata_fields
    assert "extrafanart" in desc.metadata_fields


def test_config_defaults_are_sane():
    cfg = PLUGIN.Fd2PpvConfig()
    assert cfg.base_url == "https://fd2ppv.cc"
    assert cfg.detail_template == "{base}/articles/{digits}"
    assert cfg.browser_fallback is True
    assert cfg.browser_headless is False
```

（`PLUGIN` 由 `conftest.py` 注入到测试模块命名空间——见步骤 2。）

- [ ] **步骤 2：创建 `tests/conftest.py`**

```python
"""把 amane 宿主 SDK 打桩，好让 unofficialscraper.fd2ppv/plugin.py 能在本地 pytest 里导入。

插件在 amane 里跑时，SDK 由宿主注入；本地没有 amane，所以造一组最小替身，
只为跑通纯解析逻辑的单元测试。真机行为仍以 amane 里的 debug.log 为准。
"""

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "unofficialscraper.fd2ppv" / "plugin.py"


def _install_amane_stubs() -> None:
    def module(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod
        return mod

    class FailureReason:
        NO_USABLE_METADATA = "no_usable_metadata"

    class SourceError(Exception):
        def __init__(self, reason=None, detail="", **kw):
            super().__init__(detail or reason)
            self.reason = reason
            self.detail = detail
            self.http_status = None
            self.__dict__.update(kw)

    class RequestError(SourceError):
        pass

    class SourceCapability:
        FILM_METADATA = "film_metadata"

    class SourceDescriptor:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class MediaMetadata:
        # 与宿主一致：用于「按字段名过滤 kwargs」
        model_fields = {
            "number": None, "title": None, "release": None, "runtime": None,
            "studio": None, "tags": None, "actors": None, "plot": None,
            "poster_urls": None, "thumb_urls": None, "extrafanart": None,
            "source_url": None, "external_id": None,
        }

        def __init__(self, **kw):
            self.__dict__.update(kw)

    class SearchQuery:
        def __init__(self, number="", title="", **kw):
            self.number = number
            self.title = title
            self.__dict__.update(kw)

    class FetchOptions:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class WebClient:
        ...

    class PluginContext:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class FilmSourceProvider:
        def __init__(self, *args, **kw):
            pass

    class FilmSourcePlugin:
        @classmethod
        def descriptor(cls):
            raise NotImplementedError

    module("amane", __path__=[])
    module("amane.plugins", __path__=[])
    module("amane.plugins.api",
           FilmSourcePlugin=FilmSourcePlugin, FilmSourceProvider=FilmSourceProvider,
           PluginContext=PluginContext)
    module("amane.plugins.models",
           SourceCapability=SourceCapability, SourceDescriptor=SourceDescriptor)
    module("amane.net", __path__=[])
    module("amane.net.errors",
           FailureReason=FailureReason, SourceError=SourceError, RequestError=RequestError)
    module("amane.net.http", WebClient=WebClient)
    module("amane.crawlers", __path__=[])
    module("amane.crawlers.models",
           MediaMetadata=MediaMetadata, SearchQuery=SearchQuery, FetchOptions=FetchOptions)
    module("amane.plugin",
           FilmSourcePlugin=FilmSourcePlugin, FilmSourceProvider=FilmSourceProvider,
           PluginContext=PluginContext, MediaMetadata=MediaMetadata,
           SearchQuery=SearchQuery, FetchOptions=FetchOptions,
           SourceCapability=SourceCapability, SourceDescriptor=SourceDescriptor,
           SourceError=SourceError, RequestError=RequestError,
           FailureReason=FailureReason, WebClient=WebClient)


_install_amane_stubs()


def _load_plugin():
    """按路径加载插件（目录名含点号，不能当包名 import）。"""
    spec = importlib.util.spec_from_file_location("fd2ppv_plugin", PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fd2ppv_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


PLUGIN = _load_plugin()


def pytest_configure(config):
    """让测试模块直接用全局名 PLUGIN。"""
    import builtins
    builtins.PLUGIN = PLUGIN
```

- [ ] **步骤 3：运行测试确认失败**

运行：`/c/ProgramData/anaconda3/python.exe -m pytest tests -v`（在仓库根目录）
预期：FAIL —— `FileNotFoundError` / `spec_from_file_location` 返回 None，因为 `unofficialscraper.fd2ppv/plugin.py` 还不存在。

- [ ] **步骤 4：创建 `unofficialscraper.fd2ppv/plugin.py` 骨架**

按下面的分区写。**`# ---- 宿主 SDK 握手 ----` 到 `WebClient = _API["WebClient"]` 之间，逐字复制 `esl1.supfc2/plugin.py:102-217`。**

```python
"""fd2ppv.cc —— FC2 作品的第三方元数据源。

站点：https://fd2ppv.cc
路由：详情页 ``/articles/{digits}``，可由 FC2 番号直接拼出，无需搜索。
反爬：详情页在 Cloudflare Managed Challenge 后面；真浏览器能自动过挑战，
      裸 HTTP 客户端会拿到 403 挑战页。cf_clearance 是 HttpOnly 的，
      只能用 CDP 的 Network.getCookies 导出。
形态：服务端渲染的静态 HTML（非 Inertia / 非 JSON 接口）。
"""

from __future__ import annotations

import asyncio
import html as _html
import importlib
import json
import re
import sys
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


# ---- 宿主 SDK 握手 ----  ← 逐字复制 esl1.supfc2/plugin.py:102-217
# （_SDK_API_NAMES / _API_PLAN / _load_host_api / _write_import_report /
#   _API, _IMPORT_NOTES, _MISSING_API = _load_host_api() / _write_import_report() /
#   if _MISSING_API: raise ImportError(...) / 十二个名字绑定）


# ---------- 站点常量 ----------

BASE = "https://fd2ppv.cc"
IMAGE_HOST = "https://contents.fc2.com"


class Fd2PpvConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(
        default=BASE,
        title="站点地址",
        description="fd2ppv.cc 的基址；换镜像站时改这里。",
    )
    detail_template: str = Field(
        default="{base}/articles/{digits}",
        title="详情页模板",
        description="由番号拼详情页 URL 的模板，占位符 {base} 与 {digits}。",
    )

    include_cover: bool = Field(
        default=True, title="抓取封面",
        description="把 FC2 官方封面写进 poster_urls / thumb_urls。",
    )
    include_gallery: bool = Field(
        default=True, title="抓取剧照",
        description="把详情页剧照墙（xximgs 图）写进 extrafanart。",
    )
    include_tags: bool = Field(
        default=True, title="抓取标签",
        description="把作品标签（中出し / コスプレ …）写进 tags。",
    )
    include_actresses: bool = Field(
        default=True, title="抓取女优",
        description="把 .artist-name 写进 actors；「不詳」会被自动过滤。",
    )
    max_tags: int = Field(
        default=0, title="标签上限",
        description="最多写入多少个标签；0 表示不限制。",
    )

    user_agent: str = Field(
        default="", title="User-Agent",
        description="留空用内置的桌面 Chrome UA；过 CF 时必须与 cf_clearance 配套。",
    )
    cookie: str = Field(
        default="", title="手工 Cookie",
        description="手工填 cf_clearance=...；一般留空，让插件自己过挑战。",
    )
    session_ttl: int = Field(
        default=3600, title="会话有效期(秒)",
        description="cf_clearance 会话缓存多久后重新获取。",
    )

    browser_fallback: bool = Field(
        default=True, title="浏览器过挑战",
        description="撞 Cloudflare 时自动起本机浏览器过挑战并直接取回详情页。",
    )
    browser_headless: bool = Field(
        default=False, title="无头模式",
        description="实测无头过不了 Cloudflare，保持关闭。",
    )
    browser_path: str = Field(
        default="", title="浏览器路径",
        description="留空自动探测 Chrome / Edge。",
    )
    browser_profile: str = Field(
        default="", title="浏览器 profile 目录",
        description="留空用数据目录下的 browser-profile。",
    )
    browser_proxy: str = Field(
        default="", title="浏览器代理",
        description="形如 http://127.0.0.1:7890；留空则用系统 / 环境代理。",
    )
    challenge_timeout: int = Field(
        default=45, title="过挑战超时(秒)",
        description="等待 Cloudflare 挑战通过的最长时间。",
    )

    request_retries: int = Field(
        default=2, title="重试次数",
        description="普通请求失败后的重试次数。",
    )
    request_retry_delay: float = Field(
        default=1.5, title="重试间隔(秒)",
        description="重试的起始间隔，逐次递增。",
    )

    report_misses: bool = Field(
        default=False, title="未命中报错",
        description="开启后未命中会抛 SourceError 让 Amane 记录，而不是静默返回 None。",
    )
    debug_dump: bool = Field(
        default=False, title="调试日志",
        description="把流程写入插件数据目录下的 debug/debug.log。",
    )
    debug_save_pages: bool = Field(
        default=False, title="保存页面",
        description="把抓到的 HTML 存进 debug/ 便于排查。",
    )


def _metadata_field_names() -> set[str]:
    """宿主 MediaMetadata 认得的字段名；拿不到就返回空集（不过滤）。"""
    try:
        fields = getattr(MediaMetadata, "model_fields", None)  # pydantic v2
        if callable(fields) or fields is None:
            fields = getattr(MediaMetadata, "__fields__", {})  # pydantic v1
        return set(fields.keys())
    except Exception:  # noqa: BLE001
        return set()


class Fd2PpvProvider(FilmSourceProvider):
    """番号 → /articles/<digits> 详情页。"""

    def __init__(self, context: PluginContext, config: Fd2PpvConfig) -> None:
        self._http = context.http_client
        self._web: WebClient = context.web_client
        self._config = config
        self._data_dir = Path(context.data_dir)
        self._debug_dir = self._data_dir / "debug"
        self._session: dict | None = None
        self._session_lock: asyncio.Lock | None = None

    async def fetch(self, query, options=None):
        return None


class Plugin(FilmSourcePlugin):
    """放在 ``{数据目录}/plugins/sources/unofficialscraper.fd2ppv/`` 下的 plugin.py 必须导出此类。"""

    config_model = Fd2PpvConfig

    @classmethod
    def descriptor(cls):
        return SourceDescriptor(
            id="unofficialscraper.fd2ppv",
            name="fd2ppv.cc (FC2 元数据)",
            version="0.1.0",
            capabilities=frozenset({SourceCapability.FILM_METADATA}),
            content_types=frozenset({"fc2"}),
            metadata_fields=frozenset(
                {
                    "title",
                    "release",
                    "runtime",
                    "studio",
                    "tags",
                    "actors",
                    "poster_urls",
                    "thumb_urls",
                    "extrafanart",
                    "source_url",
                    "external_id",
                }
            ),
            urls=(BASE, IMAGE_HOST),
            rate_limit=1.0,
        )

    def build(self, context, config):
        if not isinstance(config, Fd2PpvConfig):
            raise TypeError("unexpected config type")
        return Fd2PpvProvider(context, config)
```

- [ ] **步骤 5：运行测试确认通过**

运行：`/c/ProgramData/anaconda3/python.exe -m pytest tests -v`
预期：`2 passed`。

- [ ] **步骤 6：Commit**

```bash
git add unofficialscraper.fd2ppv/plugin.py tests/conftest.py tests/test_fd2ppv_parse.py
git commit -m "feat(fd2ppv): 插件骨架——SDK 握手 + 配置 + 描述符"
```

---

### 任务 3：纯解析函数（TDD）

交付物：`_extract_work_fields(html) -> dict` 能从真实标记里取出全部字段，零第三方依赖。

**文件：**
- 修改：`unofficialscraper.fd2ppv/plugin.py`（在「站点常量」之后、`Fd2PpvProvider` 之前插入）
- 测试：`tests/test_fd2ppv_parse.py`（追加）

- [ ] **步骤 1：追加失败的测试**

在 `tests/test_fd2ppv_parse.py` 末尾追加：

```python
DETAIL_HTML = """
<!doctype html><html><head>
<title>FC2 PPV 4989610 無修正、第２弾、黒髪清楚素人美女 | 作品 - FD2</title>
<meta name="description" content="FC2 PPV 4989610 無修正、第２弾、黒髪清楚素人美女、ひかりちゃん、可愛いブルマコスプレパイパン中出しハメ撮り動画、レビュー特典">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Movie","@id":"https://fd2ppv.cc/articles/4989610","name":"FC2-PPV-4989610","description":"無修正、第２弾、黒髪清楚素人美女、ひかりちゃん、可愛いブルマコスプレパイパン中出しハメ撮り動画、レビュー特典","datePublished":"2026-10-09 08:34:19"}</script>
</head><body>
<h1 class="work-title">4989610 <span class="cursor-copy" onclick="copyboard(this)" data-text="4989610" data-success="作品IDをコピーしました : 4989610"><i class="icon icon-copy"></i></span></h1>
<div class="work-image-large work-photos hidden" data-id="AAA">https://xximgs.cc/uploads/2641/6ac8368b9cce3387.webp https://xximgs.cc/uploads/2641/6ac8368baddad831.webp https://contents-thumbnail2.fc2.com/w276/storage202000.contents.fc2.com/file/385/38437260/1791463320.51.jpg</div>
<div class="work-meta">
  <div class="work-meta-label">カテゴリ</div> <div class="work-meta-value"><span class="color_free0"><i class="icon icon-cloud_play"></i> 未流出</span></div>
  <div class="work-meta-label">配信日</div> <div class="work-meta-value">2026-10-09</div>
  <div class="work-meta-label">収録時間</div> <div class="work-meta-value" id="duration">00:58:46</div>
  <div class="work-meta-label">配信元</div> <div class="work-meta-value">FC2</div>
  <!--seller--><div class="work-meta-label">販売者</div> <div class="work-meta-value"><a href="/channels/hira">ひらめき無無剣</a></div><!--seller-->
  <div class="work-meta-label">リンク</div> <div class="work-meta-value flex flex-top gap-m flex-wrap"><a href="https://adult.contents.fc2.com/article/4989610/" target="_blank" rel="noopener noreferrer nofollow">詳しくはこちら <i class="icon icon-launch"></i></a> <button class="btn btn-xs doLogin"><span>カスタム</span> <i class="icon icon-create"></i></button></div>
</div>
<div class="work-tags"><a href="/tags/articles/creampie" target="_blank"><i class="icon icon-tag"></i>中出し <i class="icon icon-launch"></i></a><a href="/tags/articles/cosplay" target="_blank"><i class="icon icon-tag"></i>コスプレ <i class="icon icon-launch"></i></a><a href="/tags/articles/pe-uniform" target="_blank"><i class="icon icon-tag"></i>体操服 <i class="icon icon-launch"></i></a></div>
<h3 class="artist-name"><span>元アイドルちゃん</span> <span class="cursor-copy hidden" onclick="copyboard(this)" data-text="元アイドルちゃん"><i class="icon icon-copy"></i></span></h3>
</body></html>
"""

# 无标签、无女优、无时长、无片商的「干净」页（实测 4989588 / 4989527 这种形态）
SPARSE_HTML = """
<html><head><meta name="description" content="FC2 PPV 4989588 【名作再販】色白巨乳バレーっこに超敏感でびくびくさせながら生ハメ">
<script type="application/ld+json">{"@type":"Movie","name":"FC2-PPV-4989588","description":"【名作再販】色白巨乳バレーっこに超敏感でびくびくさせながら生ハメ"}</script>
</head><body>
<h1 class="work-title">4989588 <span class="cursor-copy" data-text="4989588"><i class="icon icon-copy"></i></span></h1>
<div class="work-image-large work-photos hidden" data-id="BBB">https://xximgs.cc/uploads/9/a.webp https://contents-thumbnail2.fc2.com/w276/storage201000.contents.fc2.com/file/386/38500504/1791462676.07.jpg</div>
<div class="work-meta">
  <div class="work-meta-label">カテゴリ</div> <div class="work-meta-value"><span class="color_free0"> 未流出</span></div>
  <div class="work-meta-label">配信日</div> <div class="work-meta-value"></div>
  <div class="work-meta-label">収録時間</div> <div class="work-meta-value" id="duration">00:00:00</div>
  <div class="work-meta-label">販売者</div> <div class="work-meta-value"><span class="color_free">不詳</span></div>
</div>
<div class="work-tags"></div>
<h3 class="artist-name"><span>不詳</span> <span class="cursor-copy hidden" data-text="不詳"><i class="icon icon-copy"></i></span></h3>
</body></html>
"""


def test_fc2_digits():
    assert PLUGIN._fc2_digits("FC2-PPV-4989610") == "4989610"
    assert PLUGIN._fc2_digits("FC2PPV-4989610") == "4989610"
    assert PLUGIN._fc2_digits("FC2 4989610") == "4989610"
    assert PLUGIN._fc2_digits("4989610") == "4989610"
    assert PLUGIN._fc2_digits("ABP-123") is None
    assert PLUGIN._fc2_digits("") is None


def test_norm_duration():
    assert PLUGIN._norm_duration("00:58:46") == 58
    assert PLUGIN._norm_duration("02:07:27") == 127
    assert PLUGIN._norm_duration("23:39") == 23
    assert PLUGIN._norm_duration("00:00:00") is None
    assert PLUGIN._norm_duration("") is None


def test_norm_date():
    assert PLUGIN._norm_date("2026-10-09") == "2026-10-09"
    assert PLUGIN._norm_date("2026/10/9") == "2026-10-09"
    assert PLUGIN._norm_date("") is None


def test_canonical_image_url_unwraps_thumbnail_wrapper():
    wrapped = (
        "https://contents-thumbnail2.fc2.com/w276/"
        "storage202000.contents.fc2.com/file/385/38437260/1791463320.51.jpg"
    )
    assert PLUGIN._canonical_image_url(wrapped) == (
        "https://storage202000.contents.fc2.com/file/385/38437260/1791463320.51.jpg"
    )
    assert PLUGIN._canonical_image_url("https://xximgs.cc/uploads/2641/a.webp") == (
        "https://xximgs.cc/uploads/2641/a.webp"
    )


def test_extract_work_fields_full_page():
    data = PLUGIN._extract_work_fields(DETAIL_HTML)
    assert data["number"] == "4989610"
    assert data["title"].startswith("無修正、第２弾")
    assert data["title"] == (
        "無修正、第２弾、黒髪清楚素人美女、ひかりちゃん、"
        "可愛いブルマコスプレパイパン中出しハメ撮り動画、レビュー特典"
    )
    assert data["release"] == "2026-10-09"
    assert data["runtime"] == 58
    assert data["studio"] == "ひらめき無無剣"
    assert data["category"] == "未流出"
    assert data["tags"] == ["中出し", "コスプレ", "体操服"]
    assert data["actors"] == ["元アイドルちゃん"]
    assert data["poster"] == (
        "https://storage202000.contents.fc2.com/file/385/38437260/1791463320.51.jpg"
    )
    assert data["gallery"] == [
        "https://xximgs.cc/uploads/2641/6ac8368b9cce3387.webp",
        "https://xximgs.cc/uploads/2641/6ac8368baddad831.webp",
    ]


def test_extract_work_fields_sparse_page():
    data = PLUGIN._extract_work_fields(SPARSE_HTML)
    assert data["number"] == "4989588"
    assert data["title"].startswith("【名作再販】")
    assert data["release"] is None          # 配信日为空串
    assert data["runtime"] is None          # 00:00:00 视为无时长
    assert data["studio"] is None           # 不詳 → None
    assert data["tags"] == []               # .work-tags 是空 div
    assert data["actors"] == []             # 不詳 被过滤
    assert data["poster"].endswith("1791462676.07.jpg")
    assert data["gallery"] == ["https://xximgs.cc/uploads/9/a.webp"]


def test_extract_title_falls_back_to_meta_description():
    # 把 LD 脚本的 type 改掉，模拟「JSON-LD 缺失」的页面
    html = DETAIL_HTML.replace(
        '<script type="application/ld+json">', "<script type='text/x-nope'>"
    )
    data = PLUGIN._extract_work_fields(html)
    # LD 拿不到时回落到 meta description，且要去掉「FC2 PPV 4989610 」前缀
    assert not data["title"].startswith("FC2 PPV")
    assert data["title"].startswith("無修正、第２弾")


def test_is_challenge_detects_cloudflare_page():
    assert PLUGIN._is_challenge(
        "<html><head><title>Just a moment...</title></head><body></body></html>"
    )
    assert not PLUGIN._is_challenge(DETAIL_HTML)
```

- [ ] **步骤 2：运行测试确认失败**

运行：`/c/ProgramData/anaconda3/python.exe -m pytest tests -v`
预期：新增的 8 个测试 FAIL，报 `AttributeError: module 'fd2ppv_plugin' has no attribute '_fc2_digits'`（描述符那两个仍 PASS）。

- [ ] **步骤 3：实现纯函数**

在 `unofficialscraper.fd2ppv/plugin.py` 的「站点常量」之后、`class Fd2PpvConfig` 之前，插入：

```python
# ---------- 纯函数：番号 / 日期 / 时长 / 图片 ----------

_FC2_RE = re.compile(r"^FC2(?:[-_\s]?PPV)?[-_\s]*(\d{4,9})$", re.I)
_DIGITS_RE = re.compile(r"^\s*(\d{4,9})\s*$")


def _fc2_digits(raw: str) -> str | None:
    """把 FC2 番号的各种写法归一成纯数字串；不是 FC2 就返回 None。"""
    s = (raw or "").strip()
    if not s:
        return None
    m = _FC2_RE.match(s) or _DIGITS_RE.match(s)
    return m.group(1) if m else None


def _norm_date(raw: str) -> str | None:
    s = _squash(raw)
    if not s:
        return None
    m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.match(r"(\d{4})[-/.](\d{1,2})", s)
    if m:
        y, mo = (int(x) for x in m.groups())
        return f"{y:04d}-{mo:02d}"
    return s


def _norm_duration(raw: str) -> int | None:
    """``H:MM:SS`` / ``MM:SS`` / ``MM`` → 分钟；全零或解析不出返回 None。"""
    s = _squash(raw)
    if not s:
        return None
    parts = [p for p in re.split(r"[:\s]+", s) if p]
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        h, m = nums[0], nums[1]
    elif len(nums) == 2:
        h, m = 0, nums[0]
    elif len(nums) == 1:
        h, m = 0, nums[0]
    else:
        return None
    total = h * 60 + m
    return total or None


_THUMB_WRAP_RE = re.compile(r"^https?://contents-thumbnail2\.fc2\.com/w\d+/", re.I)


def _canonical_image_url(url: str) -> str:
    """还原被 FC2 缩略图域包裹的原始图地址，并补全协议相对 URL。"""
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    return _THUMB_WRAP_RE.sub("https://", u)


def _unique(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = (item or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


# ---------- 纯函数：文本 ----------

_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def _strip_tags(fragment: str) -> str:
    return _TAG_STRIP_RE.sub(" ", fragment or "")


def _squash(text: str) -> str:
    return _SPACE_RE.sub(" ", (text or "")).strip()


def _text_of(fragment: str) -> str:
    """去标签 + 解 HTML 实体 + 折叠空白。"""
    return _squash(_html.unescape(_strip_tags(fragment)))


def _safe_name(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", (text or "").strip())[:80] or "x"


# ---------- 纯函数：详情页解析 ----------

_TITLE_H1_RE = re.compile(
    r'<h1[^>]*class="[^"]*work-title[^"]*"[^>]*>(.*?)</h1>', re.S | re.I
)
_META_DESC_RE = re.compile(
    r'<meta[^>]+name="description"[^>]+content="([^"]*)"', re.S | re.I
)
_TITLE_PREFIX_RE = re.compile(r"^\s*FC2\s*(?:PPV)?\s*[-_]?\s*\d+\s*", re.I)
_LD_JSON_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I
)
_META_PAIR_RE = re.compile(
    r'<div[^>]*class="[^"]*work-meta-label[^"]*"[^>]*>(.*?)</div>\s*'
    r'<div[^>]*class="[^"]*work-meta-value[^"]*"[^>]*>(.*?)</div>',
    re.S | re.I,
)
_WORK_TAGS_RE = re.compile(
    r'<div[^>]*class="[^"]*work-tags[^"]*"[^>]*>(.*?)</div>', re.S | re.I
)
_TAG_LINK_RE = re.compile(r'<a[^>]+href="[^"]*/tags/[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
_WORK_PHOTOS_RE = re.compile(
    r'<div[^>]*class="[^"]*work-photos[^"]*"[^>]*>(.*?)</div>', re.S | re.I
)
_ARTIST_RE = re.compile(
    r'<h3[^>]*class="[^"]*artist-name[^"]*"[^>]*>(.*?)</h3>', re.S | re.I
)

_UNKNOWN_LABELS = {"", "不詳", "不详"}

_CHALLENGE_TITLE_RE = re.compile(
    r"<title>\s*(just a moment|attention required|请稍候)", re.I
)
_CHALLENGE_MARKERS = (
    "cf_chl_opt",
    "cf-browser-verification",
    "enable javascript and cookies to continue",
    "challenge-platform",
)


def _is_challenge(html: str) -> bool:
    """判断响应正文是不是 Cloudflare 挑战 / 拦截页。"""
    head = (html or "")[:8000]
    if _CHALLENGE_TITLE_RE.search(head):
        return True
    low = head.lower()
    return any(marker in low for marker in _CHALLENGE_MARKERS)


def _extract_work_fields(html: str) -> dict:
    """从详情页 HTML 里抽出全部目标字段（纯函数，无副作用）。"""
    text = html or ""

    # 1) .work-meta 的「标签 → 值」表
    meta: dict[str, str] = {}
    for m in _META_PAIR_RE.finditer(text):
        label = _text_of(m.group(1))
        if label and label not in meta:
            meta[label] = _text_of(m.group(2))

    # 2) 番号（h1.work-title）
    hm = _TITLE_H1_RE.search(text)
    number = _text_of(hm.group(1)) if hm else ""

    # 3) 标题：JSON-LD description 优先（已去前缀），meta description 兜底
    title = ""
    lm = _LD_JSON_RE.search(text)
    if lm:
        try:
            ld = json.loads(lm.group(1))
        except Exception:  # noqa: BLE001 - LD 坏了不影响其它字段
            ld = None
        if isinstance(ld, dict):
            title = _squash(str(ld.get("description") or ""))
    if not title:
        dm = _META_DESC_RE.search(text)
        if dm:
            title = _TITLE_PREFIX_RE.sub("", _text_of(dm.group(1))).strip()

    # 4) 图片：FC2 官方域 = 封面；其余（xximgs）= 剧照墙
    poster = ""
    gallery: list[str] = []
    pm = _WORK_PHOTOS_RE.search(text)
    if pm:
        for raw in _squash(_strip_tags(pm.group(1))).split():
            url = _canonical_image_url(raw)
            if not url.startswith("http"):
                continue
            if "contents.fc2.com" in url:
                poster = url
            else:
                gallery.append(url)

    # 5) 标签（必须限定在 .work-tags 内，否则会命中导航链接「タグ」）
    tags: list[str] = []
    tm = _WORK_TAGS_RE.search(text)
    if tm:
        tags = [_text_of(x) for x in _TAG_LINK_RE.findall(tm.group(1))]

    # 6) 女优（过滤「不詳」）
    actors = [_text_of(x) for x in _ARTIST_RE.findall(text)]

    def _clean(value: str | None) -> str | None:
        v = _squash(value or "")
        return None if v in _UNKNOWN_LABELS else v

    return {
        "number": number,
        "title": title,
        "release": _norm_date(meta.get("配信日", "")),
        "runtime": _norm_duration(meta.get("収録時間", "")),
        "studio": _clean(meta.get("販売者")),
        "category": _clean(meta.get("カテゴリ")),
        "tags": _unique([t for t in tags if t != "タグ"]),
        "actors": _unique([a for a in actors if _clean(a)]),
        "poster": poster,
        "gallery": _unique(gallery),
    }
```

- [ ] **步骤 4：运行测试确认通过**

运行：`/c/ProgramData/anaconda3/python.exe -m pytest tests -v`
预期：`10 passed`。

- [ ] **步骤 5：Commit**

```bash
git add unofficialscraper.fd2ppv/plugin.py tests/test_fd2ppv_parse.py
git commit -m "feat(fd2ppv): 详情页纯解析（番号/标题/日期/时长/标签/女优/封面/剧照）"
```

---

### 任务 4：Provider 组装（`_build_meta`）

交付物：解析结果按配置组装成 `MediaMetadata`，字段缺失不会崩。

**文件：**
- 修改：`unofficialscraper.fd2ppv/plugin.py`
- 测试：`tests/test_fd2ppv_parse.py`（追加）

- [ ] **步骤 1：追加失败的测试**

在 `tests/test_fd2ppv_parse.py` 末尾追加：

```python
class _StubContext:
    http_client = None
    web_client = None

    def __init__(self, data_dir):
        self.data_dir = data_dir


def _provider(tmp_path, **overrides):
    cfg = PLUGIN.Fd2PpvConfig(**overrides)
    return PLUGIN.Fd2PpvProvider(_StubContext(tmp_path), cfg)


def test_build_meta_full(tmp_path):
    data = PLUGIN._extract_work_fields(DETAIL_HTML)
    meta = _provider(tmp_path)._build_meta("FC2-PPV-4989610", "4989610",
                                           "https://fd2ppv.cc/articles/4989610", data)
    assert meta.number == "FC2-PPV-4989610"
    assert meta.external_id == "4989610"
    assert meta.source_url == "https://fd2ppv.cc/articles/4989610"
    assert meta.release == "2026-10-09"
    assert meta.runtime == 58
    assert meta.studio == "ひらめき無無剣"
    assert meta.tags == ["中出し", "コスプレ", "体操服"]
    assert meta.actors == ["元アイドルちゃん"]
    assert meta.poster_urls == [
        "https://storage202000.contents.fc2.com/file/385/38437260/1791463320.51.jpg"
    ]
    assert meta.thumb_urls == meta.poster_urls
    assert len(meta.extrafanart) == 2


def test_build_meta_respects_switches(tmp_path):
    data = PLUGIN._extract_work_fields(DETAIL_HTML)
    meta = _provider(
        tmp_path,
        include_cover=False,
        include_gallery=False,
        include_tags=False,
        include_actresses=False,
    )._build_meta("FC2-PPV-4989610", "4989610", "u", data)
    assert not getattr(meta, "poster_urls", None)
    assert not getattr(meta, "extrafanart", None)
    assert not getattr(meta, "tags", None)
    assert not getattr(meta, "actors", None)
    assert meta.title.startswith("無修正")


def test_build_meta_max_tags(tmp_path):
    data = PLUGIN._extract_work_fields(DETAIL_HTML)
    meta = _provider(tmp_path, max_tags=2)._build_meta("FC2-PPV-4989610", "4989610", "u", data)
    assert meta.tags == ["中出し", "コスプレ"]


def test_build_meta_sparse_page_does_not_crash(tmp_path):
    data = PLUGIN._extract_work_fields(SPARSE_HTML)
    meta = _provider(tmp_path)._build_meta("FC2-PPV-4989588", "4989588", "u", data)
    assert meta.number == "FC2-PPV-4989588"
    assert meta.title.startswith("【名作再販】")
    assert not getattr(meta, "release", None)
    assert not getattr(meta, "runtime", None)
    assert not getattr(meta, "studio", None)
    assert not getattr(meta, "tags", None)
    assert not getattr(meta, "actors", None)
```

- [ ] **步骤 2：运行测试确认失败**

运行：`/c/ProgramData/anaconda3/python.exe -m pytest tests -v`
预期：4 个新测试 FAIL，报 `AttributeError: 'Fd2PpvProvider' object has no attribute '_build_meta'`。

- [ ] **步骤 3：实现 `_build_meta` 与 `fetch`**

在 `Fd2PpvProvider` 里，把任务 2 留下的占位 `fetch` 换成下面这些方法（`_get` / `_browser_fetch_page` / Cloudflare 会话三件套留到任务 5）：

```python
    # ---------- 主流程 ----------

    async def fetch(self, query, options=None):
        number = (query.number or "").strip()
        if not number:
            self._log("跳过：查询里没有番号")
            return None
        digits = _fc2_digits(number)
        if not digits:
            self._log(f"跳过：{number} 不是 FC2 番号（本站只收录 FC2-PPV-<数字>）")
            return None
        url = self._detail_url(digits)
        self._log(f"=== 开始 {number} digits={digits} → {url}")

        text, err = await self._get(url)
        if err:
            return self._miss(number, [err])
        if not text:
            return self._miss(number, ["空响应"])
        if _is_challenge(text):
            return self._miss(number, ["撞到 Cloudflare 挑战页，浏览器兜底也没过"])
        if self._config.debug_save_pages:
            self._dump(f"{_safe_name(digits)}.html", text)

        data = _extract_work_fields(text)
        page_number = _fc2_digits(data.get("number") or "")
        if page_number and page_number.lstrip("0") != digits.lstrip("0"):
            self._log(f"番号不符：页面 {page_number} ≠ 目标 {digits}")
            return self._miss(number, [f"详情页番号不符：{page_number}"])
        if not data.get("title"):
            return self._miss(number, ["详情页没有标题（站点可能改版 / 未收录）"])
        return self._build_meta(number, digits, url, data)

    def _build_meta(self, number: str, digits: str, url: str, data: dict):
        kwargs: dict[str, object] = {
            "number": number,
            "title": data["title"],
            "source_url": url,
            "external_id": digits,
        }
        if data.get("release"):
            kwargs["release"] = data["release"]
        if data.get("runtime"):
            kwargs["runtime"] = data["runtime"]
        if data.get("studio"):
            kwargs["studio"] = data["studio"]

        tags = list(data.get("tags") or [])
        if self._config.drop_category_tag and data.get("category"):
            tags = [t for t in tags if t != data["category"]]
        if self._config.max_tags > 0:
            tags = tags[: self._config.max_tags]
        if self._config.include_tags and tags:
            kwargs["tags"] = tags

        if self._config.include_actresses and data.get("actors"):
            kwargs["actors"] = list(data["actors"])
        if self._config.include_cover and data.get("poster"):
            kwargs["poster_urls"] = [data["poster"]]
            kwargs["thumb_urls"] = [data["poster"]]
        if self._config.include_gallery and data.get("gallery"):
            kwargs["extrafanart"] = list(data["gallery"])

        allowed = _metadata_field_names()
        if allowed:
            kwargs = {k: v for k, v in kwargs.items() if k in allowed}

        self._log(
            "组装完成: " + ", ".join(f"{k}={str(v)[:60]}" for k, v in kwargs.items())
        )
        return MediaMetadata(**kwargs)  # type: ignore[misc]

    def _detail_url(self, digits: str) -> str:
        base = (self._config.base_url or BASE).rstrip("/")
        template = self._config.detail_template or "{base}/articles/{digits}"
        return template.replace("{base}", base).replace("{digits}", digits)

    def _miss(self, number: str, notes: list[str]):
        detail = "; ".join(notes) if notes else "没有任何可用来源"
        self._log(f"未命中 {number}：{detail}")
        if self._config.report_misses:
            raise SourceError(  # type: ignore[misc]
                FailureReason.NO_USABLE_METADATA, detail=f"fd2ppv: {detail}"
            )
        return None

    # ---------- 诊断 ----------

    def _log(self, message: str) -> None:
        if not self._config.debug_dump:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with (self._debug_dir / "debug.log").open("a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] {message}\n")
        except Exception:  # noqa: BLE001 - 日志失败不能影响抓取
            pass

    def _dump(self, name: str, text: str) -> None:
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            (self._debug_dir / name).write_text(text, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
```

同时在 `Fd2PpvConfig` 里补一个 `drop_category_tag` 字段（`_build_meta` 用到了它）：

```python
    drop_category_tag: bool = Field(
        default=True, title="去掉分类同名标签",
        description="标签里若出现与「カテゴリ」同名的项（如 未流出），写入前去掉。",
    )
```

- [ ] **步骤 4：运行测试确认通过**

运行：`/c/ProgramData/anaconda3/python.exe -m pytest tests -v`
预期：`14 passed`。

- [ ] **步骤 5：Commit**

```bash
git add unofficialscraper.fd2ppv/plugin.py tests/test_fd2ppv_parse.py
git commit -m "feat(fd2ppv): 解析结果按配置组装为 MediaMetadata"
```

---

### 任务 5：网络层——HTTP 优先 + 浏览器过挑战兜底

交付物：撞 Cloudflare 时自动起本机浏览器，直接取回详情页 HTML，并缓存 `cf_clearance` 供后续 HTTP 复用。

**文件：**
- 修改：`unofficialscraper.fd2ppv/plugin.py`

- [ ] **步骤 1：把 CDP 浏览器块整段复制进来**

把 `esl1.supfc2/plugin.py` 的 **1365–1929 行**（从 `# ====` 注释头到 `browser_fetch` 的 `return {...}` 结尾）**逐字复制**到 `unofficialscraper.fd2ppv/plugin.py` 文件**末尾**。

⚠️ 必须连它自带的 import 一起复制（`esl1.supfc2/plugin.py:1382-1389`）：

```python
import base64
import os
import shutil
import socket
import struct
import subprocess
import time
from urllib.request import ProxyHandler, Request, build_opener, urlopen
```

⚠️ 复制后检查：该块里若有 `Supfc2` 字样（如 profile 目录名、UA 注释）无需改动；`browser_fetch` 的签名与返回值**保持原样**（返回字典的 `html` 键才是我们要的页面）。

- [ ] **步骤 2：在 `Fd2PpvProvider` 里补 `_get` 与浏览器兜底**

```python
    # ---------- 网络 ----------

    async def _get(self, url: str) -> tuple[str | None, str | None]:
        """带退避重试的 GET。404 直接判未收录；撞挑战则起浏览器兜底。"""
        attempts = max(0, self._config.request_retries) + 1
        refresh_left = 1 if self._config.browser_fallback else 0
        delay = self._config.request_retry_delay
        last_err = "未知错误"
        index = 0
        while index < attempts:
            index += 1
            challenged = False
            try:
                text = await self._http.get_html(
                    url, headers=self._headers(), cookies=self._cookie_dict()
                )
                if text and not _is_challenge(text):
                    return text, None
                if text:
                    challenged = True
                    last_err = "Cloudflare 挑战页（HTTP 200，但正文是挑战页）"
                else:
                    last_err = "空响应"
            except SourceError as exc:  # type: ignore[misc]
                status = getattr(exc, "http_status", None)
                reason = getattr(getattr(exc, "reason", None), "value", str(exc))
                if status in (404, 410):
                    return None, f"HTTP {status}：站点未收录 {url}"
                if reason in ("cloudflare_challenge", "cloudflare_blocked") or status == 403:
                    challenged = True
                last_err = f"HTTP {status or '-'} {reason}"
            except Exception as exc:  # noqa: BLE001 - 任何异常都要落到诊断里
                last_err = f"{type(exc).__name__}: {exc}"

            if challenged and refresh_left > 0:
                refresh_left -= 1
                self._session = None  # 旧的 clearance 可能过期了，清掉重拿
                page = await self._browser_fetch_page(url)
                if page:
                    self._log(f"浏览器兜底取回详情页：{url}")
                    return page, None
                last_err = f"{last_err}（Cloudflare 拦截，浏览器兜底也没拿到）"

            if index < attempts:
                self._log(f"抓取失败（{last_err}），{delay:.1f}s 后重试（{index}/{attempts - 1}）")
                await asyncio.sleep(delay)
                delay += self._config.request_retry_delay
        return None, last_err

    # ---------- Cloudflare 会话（cf_clearance + 配套 UA）----------
    #
    # 详情页整片在 Cloudflare 托管挑战后面，且 cf_clearance 是 HttpOnly 的
    # （document.cookie 读不到），只能靠 CDP 的 Network.getCookies 导出。
    # 这里起一次有头 Chrome（立刻最小化）直接导航到**详情页本身**：
    # 既让挑战在真正需要的路径上过掉，又顺手把 DOM 取回来——省掉一次请求。
    # 拿到的 cookie 同时落盘，供后续纯 HTTP 请求复用。

    def _session_path(self) -> Path:
        return self._data_dir / "cf_session.json"

    def _browser_profile_dir(self) -> Path:
        configured = (self._config.browser_profile or "").strip()
        return Path(configured) if configured else (self._data_dir / "browser-profile")

    def _read_session_file(self) -> dict | None:
        try:
            raw = json.loads(self._session_path().read_text(encoding="utf-8") or "{}")
        except Exception:  # noqa: BLE001 - 没有这个文件属正常
            return None
        if not isinstance(raw, dict) or not raw.get("cookie_header"):
            return None
        age = time.time() - float(raw.get("ts") or 0)
        if age > max(60, self._config.session_ttl):
            return None
        raw["age"] = int(age)
        return raw

    def _load_session(self) -> dict | None:
        """取当前可用的 Cloudflare 会话：手填 cookie → 内存 → 磁盘。"""
        if self._config.cookie.strip():
            return {
                "cookie_header": self._config.cookie.strip(),
                "user_agent": self._config.user_agent.strip() or _DEFAULT_UA,
                "source": "config",
            }
        if self._session:
            return self._session
        from_disk = self._read_session_file()
        if from_disk:
            from_disk["source"] = "disk"
            self._session = from_disk
        return self._session

    def _save_session(self, session: dict) -> None:
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._session_path().write_text(
                json.dumps(session, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass

    def _headers(self) -> dict[str, str]:
        session = self._load_session() or {}
        ua = session.get("user_agent") or self._config.user_agent.strip() or _DEFAULT_UA
        return {**_BROWSER_HEADERS, "User-Agent": ua}

    def _cookie_dict(self) -> dict[str, str] | None:
        session = self._load_session()
        if not session:
            return None
        jar: dict[str, str] = {}
        for item in (session.get("cookie_header") or "").split(";"):
            name, _, value = item.strip().partition("=")
            if name and value:
                jar[name] = value
        return jar or None

    async def _browser_fetch_page(self, url: str) -> str | None:
        """起浏览器导航到详情页，过掉挑战后把 DOM HTML 返回；顺手缓存 cookie。"""
        if not self._config.browser_fallback:
            return None
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()
        async with self._session_lock:
            profile = self._browser_profile_dir()
            log_path = self._debug_dir / "chrome.log"
            # 先试「有头 + 立刻最小化」（不打扰用户），不行再试「有头 + 窗口可见」。
            # 实测无头模式过不了 Cloudflare，所以不排进梯队。
            attempts = [
                ("minimized", self._config.browser_headless, not self._config.browser_headless),
                ("visible", False, False),
            ]
            result: dict | None = None
            for label, headless, minimize in attempts:
                self._log(f"Cloudflare 挑战：起浏览器过挑战（{label}，{url}）")
                try:
                    result = await asyncio.to_thread(
                        browser_fetch,
                        url,
                        profile_dir=profile,
                        browser_path=(self._config.browser_path or "").strip() or None,
                        headless=headless,
                        proxy=(self._config.browser_proxy or "").strip() or None,
                        challenge_timeout=self._config.challenge_timeout,
                        minimize=minimize,
                        log_path=log_path,
                    )
                except Exception as exc:  # noqa: BLE001 - 浏览器起不来也要给出原因
                    self._log(f"浏览器过挑战失败（{label}）：{type(exc).__name__}: {exc}")
                    if self._config.debug_save_pages:
                        self._dump("cloudflare.error.txt", f"[{label}] {type(exc).__name__}: {exc}\n")
                    continue
                for line in result.get("diag") or []:
                    self._log(f"  浏览器诊断[{label}] {line}")
                if result.get("cookies"):
                    break
                if self._config.debug_save_pages and result.get("raw_html"):
                    self._dump(f"cloudflare.{label}.html", result["raw_html"])
                self._log(f"浏览器没拿到 cookie（{label}）：挑战没过，换下一种模式再试")
            if result is None:
                return None

            names = [c.get("name") for c in result.get("cookies") or []]
            if names:
                session = {
                    "cookie_header": result.get("cookie_header") or "",
                    "user_agent": result.get("user_agent") or _DEFAULT_UA,
                    "ts": time.time(),
                    "source": "browser",
                }
                self._session = session
                self._save_session(session)
                self._log(
                    f"过挑战成功：cookies={names} "
                    f"cf_clearance={'有' if 'cf_clearance' in names else '无'}"
                )
            else:
                self._log("浏览器始终没拿到 cookie：挑战没过。可看 debug/cloudflare.*.html 与 debug/chrome.log。")

            html = result.get("html") or ""
            if html and not _is_challenge(html):
                return html
            if self._config.debug_save_pages and result.get("raw_html"):
                self._dump("cloudflare.last.html", result["raw_html"])
            return None
```

- [ ] **步骤 3：追加 `fetch` 路由分支的测试**

`fetch` 的主控流（非 FC2 番号 / 404 / 番号不符 / 挑战页 / 成功 / report_misses）不依赖真实网络——把 `_get` 覆盖掉就能离线测。在 `tests/test_fd2ppv_parse.py` 末尾追加：

```python
import asyncio


class _FakeGetProvider(PLUGIN.Fd2PpvProvider):
    """把 _get 换成固定返回，专测 fetch 的路由分支。"""

    def __init__(self, tmp_path, html=None, err=None, **cfg_overrides):
        super().__init__(_StubContext(tmp_path), PLUGIN.Fd2PpvConfig(**cfg_overrides))
        self._html, self._err = html, err

    async def _get(self, url):
        return self._html, self._err


def _run(coro):
    return asyncio.run(coro)


def _query(number):
    return PLUGIN.SearchQuery(number=number)


def test_fetch_rejects_non_fc2_number(tmp_path):
    p = _FakeGetProvider(tmp_path, html=DETAIL_HTML)
    assert _run(p.fetch(_query("ABP-123"))) is None


def test_fetch_miss_on_http_404(tmp_path):
    p = _FakeGetProvider(tmp_path, html=None, err="HTTP 404：站点未收录")
    assert _run(p.fetch(_query("FC2-PPV-99999999"))) is None


def test_fetch_miss_on_number_mismatch(tmp_path):
    # 页面里是 4989610，却按 4989611 去查 → 必须拒绝，避免「同号不同片」
    p = _FakeGetProvider(tmp_path, html=DETAIL_HTML)
    assert _run(p.fetch(_query("FC2-PPV-4989611"))) is None


def test_fetch_miss_on_challenge_page(tmp_path):
    p = _FakeGetProvider(
        tmp_path, html="<html><head><title>Just a moment...</title></head></html>"
    )
    assert _run(p.fetch(_query("FC2-PPV-4989610"))) is None


def test_fetch_success(tmp_path):
    p = _FakeGetProvider(tmp_path, html=DETAIL_HTML)
    meta = _run(p.fetch(_query("FC2-PPV-4989610")))
    assert meta is not None
    assert meta.external_id == "4989610"
    assert meta.title.startswith("無修正、第２弾")


def test_fetch_miss_raises_when_report_misses(tmp_path):
    p = _FakeGetProvider(tmp_path, html=None, err="HTTP 404", report_misses=True)
    with pytest.raises(PLUGIN.SourceError):
        _run(p.fetch(_query("FC2-PPV-99999999")))
```

注意：`pytest` 已在任务 3 追加测试时导入过（若没有，补 `import pytest` 到测试文件顶部）；`_StubContext` / `_provider` 来自任务 4。

- [ ] **步骤 4：跑测试确认通过**

运行：`/c/ProgramData/anaconda3/python.exe -m pytest tests -v`
预期：`20 passed`。

- [ ] **步骤 5：语法与导入自检**

运行：
```bash
/c/ProgramData/anaconda3/python.exe -c "import ast,pathlib; ast.parse(pathlib.Path('unofficialscraper.fd2ppv/plugin.py').read_text(encoding='utf-8')); print('AST OK')"
```
预期：`AST OK`（复制来的 CDP 块若漏了 import，会在实机运行时才炸，所以这里先过一遍语法）。

- [ ] **步骤 6：Commit**

```bash
git add unofficialscraper.fd2ppv/plugin.py
git commit -m "feat(fd2ppv): HTTP 优先 + CDP 浏览器过 Cloudflare 兜底"
```

---

### 任务 6：README 与实机验证

交付物：README 收录新插件；在 amane 里真实刮削成功。

**文件：**
- 修改：`README.md:7-13`（插件列表表格）
- 修改：`README.md:26-31`（运行要求里补一行浏览器说明）

- [ ] **步骤 1：README 插件列表加一行**

在 `README.md` 表格中 `esl1.supfc2` 那行之后插入：

```markdown
| `unofficialscraper.fd2ppv` | 0.1.0 | `fc2` | fd2ppv.cc | FC2 元数据，含剧照墙（`extrafanart`）。详情页在 Cloudflare 挑战后，撞挑战自动起浏览器兜底 |
```

- [ ] **步骤 2：README「浏览器支持」补一句**

在 `README.md` 的运行要求列表里，`esl1.fc2ppvdb` / `esl1.supfc2` / `esl1.supjav` 那条后面追加 `unofficialscraper.fd2ppv`：

```markdown
  - `esl1.fc2ppvdb` / `esl1.supfc2` / `esl1.supjav` / `unofficialscraper.fd2ppv`：需要 Chrome / Edge 内核浏览器，用于通过 Cloudflare 挑战（fd2ppv 直接用浏览器取回详情页）
```

- [ ] **步骤 3：安装到 amane**

把插件目录复制进 amane 数据目录的 `plugins/sources/`：

```bash
cp -r unofficialscraper.fd2ppv /c/Users/ZhaoG/AppData/Local/amane/plugins/sources/
```

然后在 amane 界面里「重新扫描」插件，并到 **设置 → 刮削 → 内容路由** 把 `unofficialscraper.fd2ppv` 勾进 `fc2` 路由。

- [ ] **步骤 4：打开调试开关并实机刮削**

在插件设置里打开 **调试日志** 与 **保存页面**，然后对下面这些真实番号各刮一次：

| 番号 | 期望 |
| --- | --- |
| `FC2-PPV-4989610` | 有标题 / 发行日 2026-10-09 / 时长 58 / 片商 ひらめき無無剣 / 3 个标签 / 女优 元アイドルちゃん / 封面 + 2 张剧照 |
| `FC2-PPV-4989588` | 有标题 / **无**发行日 / **无**时长 / **无**片商 / 无标签 / 无女优 / 有封面 + 1 张剧照（不崩） |
| `FC2-PPV-99999999` | 未收录 → 返回 None（不报错） |

- [ ] **步骤 5：核对 debug 产物**

运行：
```bash
tail -n 60 /c/Users/ZhaoG/AppData/Local/amane/plugins/sources/unofficialscraper.fd2ppv/debug/debug.log
ls /c/Users/ZhaoG/AppData/Local/amane/plugins/sources/unofficialscraper.fd2ppv/debug/
```
预期：日志里有 `=== 开始 …`、`过挑战成功：cookies=[...] cf_clearance=有`、`组装完成: …`；`debug/` 下有 `4989610.html`、`chrome.log`。

**若 `cf_clearance=无` 或始终「挑战没过」**：看 `debug/cloudflare.*.html` 与 `debug/chrome.log`；确认本机 Chrome/Edge 可正常打开 fd2ppv.cc；若走了代理，在插件设置里把 **浏览器代理** 填成 `http://127.0.0.1:7890`。

- [ ] **步骤 6：Commit**

```bash
git add README.md
git commit -m "docs: README 收录 unofficialscraper.fd2ppv 插件"
```

---

## 自检

**规格覆盖度**

| 附录 A 的结论 | 落在哪个任务 |
| --- | --- |
| 详情页路由 `/articles/{digits}`，无需搜索 | 任务 4 `_detail_url` |
| 404 = 未收录 | 任务 5 `_get` 的 `status in (404, 410)` |
| Cloudflare 挑战 + HttpOnly 的 `cf_clearance` | 任务 5 `_browser_fetch_page` + CDP 块 |
| `number` / `title` / `release` / `runtime` / `studio` | 任务 3 `_extract_work_fields`，任务 4 `_build_meta` |
| `tags` 限定在 `.work-tags` 内 | 任务 3 `_WORK_TAGS_RE` + `_TAG_LINK_RE`（有专门测试） |
| `actors` 过滤「不詳」 | 任务 3 `_clean` + 稀疏页测试 |
| `poster` / `extrafanart` 按 FC2 域名分流 + 缩略图域还原 | 任务 3 `_canonical_image_url` + 两个解析测试 |
| 空 `.work-tags` / `不詳` / `00:00:00` 容错 | 任务 3 `SPARSE_HTML` 测试、任务 4 稀疏页测试 |
| `.gitignore` 忽略开发目录 | 任务 1 |

**占位符扫描**：无「待定 / TODO / 后续实现」；任务 2 的 `fetch` 占位在任务 4 被显式替换，且替换代码已完整给出。

**类型一致性**：`_extract_work_fields` 返回的键（`number/title/release/runtime/studio/category/tags/actors/poster/gallery`）与任务 4 `_build_meta` 读取的键逐一对应；`_build_meta` / `_detail_url` / `_miss` / `_log` / `_dump` / `_get` / `_browser_fetch_page` / `_headers` / `_cookie_dict` / `_load_session` / `_save_session` / `_read_session_file` / `_session_path` / `_browser_profile_dir` 在任务 4、5 中定义并相互引用，无悬空引用。配置字段 `drop_category_tag` 在任务 4 的 `_build_meta` 使用前已在该任务的步骤 3 中定义。
