"""supjav.com 影片元数据插件（amane）。

supjav.com 是 JAV（有码 / 无码 / 中文字幕 / FC2 素人）在线片库。本站
**无法从番号直连**——文章页是 ``https://supjav.com/{站点自增 id}.html``
（例：``/302946.html``），id 与番号（HMN-625 之类）无关，只能先搜索再进详情页。
这是「站内搜索 + 详情解析」两跳结构的典型代表。

站点结构（2026-09-27 用本机浏览器过 Cloudflare 后**实测**，非推断）
------------------------------------------------------------------

**① Cloudflare Managed Challenge 是最大的门槛。**
curl_cffi 无论怎么模拟指纹（chrome/safari/firefox 全试过）都只能拿到
``cType: 'managed'`` 的挑战页；无头 Chrome（``--headless=new``，即使换了 UA、
注入反自动化脚本）也过不去。**只有有头 Chrome 能自动过关**。
所以插件内置了一个极简 CDP 客户端：真起一个有头 Chrome（窗口立刻最小化）
跑一遍挑战 → 导出 ``cf_clearance`` + UA → **关掉浏览器**，之后所有请求都走
宿主的 curl_cffi（带这串 cookie）——快、省、且能吃到宿主的限速与代理。
``cf_clearance`` 绑定客户端 IP + UA，所以 cookie 与 UA 必须成对使用。

**② 列表页（站内搜索 ``/?s=<番号>``，WordPress 风格）**::

    <div class="posts clearfix">
      <div class="post">
        <a href="https://supjav.com/302946.html" class="img" title="…">
          <img data-original="https://img.supjav.com/images/2024/10/hmn625pl.jpg!320x216.jpg" class="thumb">
        </a>
        <div class="con">
          <h3 itemprop="name headline"><a href="https://supjav.com/302946.html">标题</a></h3>
          <div class="meta">2024/10/23<span class="date">81832 Views</span></div>
        </div>
      </div>
    </div>

* 封面带 CDN 尺寸后缀 ``!320x216.jpg``，取 ``!`` 前才是原图。
* **发布日期只出现在列表页**（``div.con div.meta`` 的第一个文本节点），
  详情页里根本没有日期 —— 所以日期靠候选条目带过来。
* 同一部作品常有多条（``[Reducing Mosaic]`` / ``[4K]`` / ``[Chinese Subtitles]``
  等前缀版本），插件按 ``article_preference`` 打分挑一条。

**③ 详情页**::

    <div class="video-wrap"><div class="left">
      <div class="archive-title"><h1>标题</h1></div>
      <div id="player-wrap" class="player-wrap" style="background-image: url(https://img.supjav.com/images/2024/10/hmn625pl.jpg);">
    </div></div>
    <div class="content content-padding"><div class="post-meta clearfix">
      <img src="https://img.supjav.com/images/2024/10/hmn625pl.jpg" class="img">
      <h2>标题</h2>
      <div class="cats">
        <p class="cat"><a href="/category/reducing-mosaic">Reducing Mosaic</a></p>
        <p><span>Maker : </span><a href="/category/maker/honnaka">Honnaka</a></p>
        <p><span>Cast : </span><a href="/category/cast/kasui-jun">Kasui Jun</a></p>
      </div>
      <div class="tags"><a href="/tag/creampie" rel="tag">Creampie</a>…</div>
    </div></div>

* 标题：``div.video-wrap h1``（全页只有 1 个 h1，``You May Also Like`` 用的是 h2）。
* 封面：``div#player-wrap`` 的 ``style`` 里的 ``url(...)``，或 ``div.post-meta img``；
  **详情页封面是原图**（无 ``!`` 后缀），所以优先用它。
* 女优 / 片商 / 标签都走 URL 路径匹配：``/category/cast/``、``/category/maker/``、``/tag/``。
* **详情页没有日期、没有时长、没有剧照**（``post-content`` 是空的），
  ``og:`` 标签、JSON-LD 也都没有。
* FC2 作品标题形如 ``FC2PPV 4551862 …``（无 dash），女优栏整块缺失。

**④ 语言目录**：``/zh/`` 有中文标题、``/ja/`` 日文，结构与英文版一致，
文章地址形如 ``/zh/301856.html``。``lang_path`` 默认留空（英文版）。

**⑤ FC2 番号的搜索写法很关键**（实测）::

    ?s=FC2-PPV-4981113   → 0 条   ← 宿主给的规范写法，本站搜不到！
    ?s=FC2PPV-4981113    → 0 条
    ?s=FC2PPV 4981113    → 1 条 ✓
    ?s=4981113           → 1 条 ✓

  所以番号形如 ``FC2-PPV-xxxxxxx`` 时改用「``FC2PPV <纯数字>`` → ``<纯数字>``」
  两跳搜索（``_search_variants`` 里做掉）。

设计取舍
--------

* **两跳**：先 ``?s=<番号>`` 搜出候选，再进详情页。搜索结果里已经有「标题 + 封面 + 日期」，
  所以即使详情页抓不到，也能降级返回最小元数据（``accept_search_result`` 控制）。
* **选择器全部可配**：mapping 到 settings 面板，改类目不用改代码。
* **命中与否都要有诊断**：``debug/debug.log`` 记每一步，并打印**每个选择器的命中数**
  （``h1=1, div#player-wrap::attr(style)=1``）。一行 ``=0`` 就是「站点换皮了」最快的信号。
* 遇到验证码/Cloudflare 拦截页时**不静默返回 None**，而是抛带原因的 ``SourceError``。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import sys
import time
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote, urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field


# ---------- 请求头 ----------
#
# 站点的 Cloudflare 除了查 cookie，也看这套导航头。带全它更像浏览器，
# 少很多无谓的挑战；UA 必须与拿到 cf_clearance 时的那个一致（见 _headers）。
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_BROWSER_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


# ---------- 与宿主握手：取到宿主校验时真正会比对的那些类对象 ----------
#
# 不能直接 ``from amane.plugin import ...``：宿主判定插件是否合法时用的是
# ``amane.plugins.api.FilmSourcePlugin`` 这个具体对象，而 ``amane.plugin`` 只是
# 再导出壳；打包版（PyInstaller）下两条路径可能解析到不同的类对象，
# ``issubclass()`` 为假，宿主报 "plugin.py must define a FilmSourcePlugin ... subclass
# named Plugin"——即便插件代码完全正确。
#
# 解法和 javarchive / fc2cmadb 插件一致：按优先级走 ``importlib.import_module``，
# 优先命中宿主自己在用的模块（``importlib`` 会走 ``sys.modules``）。

_SDK_API_NAMES = (
    "FailureReason",
    "FetchOptions",
    "FilmSourcePlugin",
    "FilmSourceProvider",
    "MediaMetadata",
    "PluginContext",
    "RequestError",
    "SearchQuery",
    "SourceCapability",
    "SourceDescriptor",
    "SourceError",
    "WebClient",
)

# (模块, 该模块负责提供的名字) —— 顺序即优先级
_API_PLAN = (
    ("amane.plugins.api", ("FilmSourcePlugin", "FilmSourceProvider", "PluginContext")),
    ("amane.plugins.models", ("SourceCapability", "SourceDescriptor")),
    ("amane.net.errors", ("FailureReason", "SourceError", "RequestError")),
    ("amane.net.http", ("WebClient",)),
    ("amane.crawlers.models", ("MediaMetadata", "SearchQuery", "FetchOptions")),
    ("amane.plugin", _SDK_API_NAMES),  # 兜底：作者 SDK 壳
)


def _load_host_api() -> tuple[dict[str, object], list[str], list[str]]:
    """按优先级把插件 API 凑齐，返回 (名字 -> 对象, 诊断行, 缺失的名字)。"""
    found: dict[str, object] = {}
    notes: list[str] = []
    for module_name, provided in _API_PLAN:
        try:
            module = importlib.import_module(module_name)
        except BaseException as exc:  # 任何一种失败都只记一笔，继续尝试后面的模块
            notes.append(f"[x] {module_name}: {type(exc).__name__}: {exc}")
            continue
        notes.append(f"[v] {module_name}: {getattr(module, '__file__', '?')}")
        for name in provided:
            if name in found or not hasattr(module, name):
                continue
            found[name] = getattr(module, name)
    missing = [name for name in _SDK_API_NAMES if name not in found]
    return found, notes, missing


_API, _IMPORT_NOTES, _MISSING_API = _load_host_api()


def _write_import_report() -> None:
    """把导入实况写进插件目录，便于核对基类身份；任何异常都不允许影响加载。"""
    try:
        canonical: object | None = None
        try:
            canonical = importlib.import_module("amane.plugins.api").FilmSourcePlugin
        except BaseException:
            pass
        base = _API.get("FilmSourcePlugin")
        lines = [
            "# amane 插件导入实况（排查加载失败时先看这个文件）",
            f"插件文件 : {__file__}",
            f"Python   : {sys.version.split()[0]}",
            f"frozen   : {getattr(sys, 'frozen', None)}",
            f"执行文件 : {getattr(sys, 'executable', '?')}",
            f"amane    : {getattr(sys.modules.get('amane'), '__file__', '未导入')}",
            "",
            "## 候选模块导入结果",
            *_IMPORT_NOTES,
            "",
            "## 基类身份",
            f"本插件用的 FilmSourcePlugin   = {base!r}",
            f"宿主 manager 比对用的那个     = {canonical!r}",
            f"两者是同一对象                = {base is canonical}",
            "",
            "## 缺失的名字",
            "（无）" if not _MISSING_API else ", ".join(_MISSING_API),
        ]
        Path(__file__).with_name("import-report.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    except BaseException:
        pass


_write_import_report()

if _MISSING_API:  # pragma: no cover - 只有宿主 API 与插件不兼容时才会走到
    raise ImportError(
        "amane 插件 API 不完整，缺少: "
        + ", ".join(_MISSING_API)
        + "；诊断: "
        + " | ".join(_IMPORT_NOTES)
    )

FailureReason = _API["FailureReason"]
FetchOptions = _API["FetchOptions"]
FilmSourcePlugin = _API["FilmSourcePlugin"]
FilmSourceProvider = _API["FilmSourceProvider"]
MediaMetadata = _API["MediaMetadata"]
PluginContext = _API["PluginContext"]
RequestError = _API["RequestError"]
SearchQuery = _API["SearchQuery"]
SourceCapability = _API["SourceCapability"]
SourceDescriptor = _API["SourceDescriptor"]
SourceError = _API["SourceError"]
WebClient = _API["WebClient"]

BASE = "https://supjav.com"
# 封面图片自己的域名（宿主用它建限速器；ResourceStore 下载封面会打到这儿）
IMAGE_HOST = "https://img.supjav.com"


# ---------- 配置 ----------
#
# 所有旋钮都是标量（str/int/bool/float），宿主配置面板才能直接编辑。
# 中文标题写在 Field(title=..., description=...) 里：amane 的插件设置表单由
# JSON Schema 渲染，插件专属字段没有内置 i18n 词条，标签取 ``schema.title``、
# 说明取 ``schema.description``（三级回退的最后一级）。**字段名保持英文**，
# 因为宿主的敏感字段脱敏是按名字匹配 ``token`` / ``api_key`` / ``secret`` 的。


class SupjavConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # --- 基础 ---
    base_url: str = Field(
        default=BASE,
        title="站点基址",
        description="正常不用改，站长换域名时才需要。结尾不要带斜杠。已知镜像：supjav.com。",
    )
    lang_path: str = Field(
        default="",
        title="语言目录",
        description="站点语言前缀：英文版留空；填 zh 走 https://supjav.com/zh/...，"
        "填 ja 走日文版。标题语言随之变化，番号不受影响。",
    )
    search_path: str = Field(
        default="/?s={q}",
        title="站内搜索路径",
        description="本站是 WordPress 风格搜索。{q} 会替换成番号。如果你的域名变了导致搜索失效，"
        "在这里改成实际路径，例如 /search?q={q}。",
    )
    article_url_template: str = Field(
        default="{base}/{id}.html",
        title="详情页模板",
        description="文章页地址模板，{base} 是站点基址，{lang} 是语言目录，{id} 是搜索得到的站内编号。",
    )

    # --- 发现 ---
    try_variants: bool = Field(
        default=True,
        title="尝试番号变体",
        description="站内搜索无果时，再试 SONE353 / SONE 353 等写法（多花一到三次请求）。"
        "FC2 番号的「FC2PPV 纯数字 / 纯数字」两跳搜索是必须的，不受本开关影响。",
    )
    fallback_engines: str = Field(
        default="",
        title="备用搜索引擎",
        description="站内搜索全灭时的兜底，逗号分隔的 URL 模板，{q} 替换成番号。"
        "留空表示只用站内搜索。可填第三方 SearXNG 实例等，"
        "注意多数公共引擎会对机房 IP 弹验证码。",
    )
    verify_number: bool = Field(
        default=True,
        title="校验详情页番号",
        description="详情页正文里必须出现该番号才认这条结果，避免搜到同名但不同番号的作品。"
        "站点标题写法特殊导致误判时可以关掉。",
    )
    accept_search_result: bool = Field(
        default=True,
        title="搜索结果可直接用",
        description="详情页抓不动时，退化成用搜索结果里的「标题 + 封面」返回最小元数据。"
        "宁缺毋滥可以关。",
    )
    list_thumb_fallback: bool = Field(
        default=True,
        title="用列表封面兜底",
        description="详情页拿不到封面时，用搜索结果列表里的封面图（data-original）顶上。",
    )
    article_preference: str = Field(
        default="4k, chinese, chn sub, subtitle, uncensored, reducing",
        title="同号多版本偏好",
        description="同一部作品本站常有多条（[4K]、[Chinese Subtitles]、[Reducing Mosaic]…）。"
        "无前缀的版本永远优先；都有前缀时按这里的顺序挑，逗号分隔、越靠前越优先。",
    )

    # --- 解析选择器（都可改，日志会打印每个选择器的命中数）---
    # 下面这些默认值 = 2026-09-27 实测的真实结构，不是猜的。
    title_selector: str = Field(
        default="div.video-wrap h1::text, div.archive-title h1::text, "
        "div.post-meta h2::text, h1::text",
        title="标题选择器",
        description="逗号分隔、按顺序尝试。详情页全页只有 1 个 h1（You May Also Like 用 h2）。",
    )
    cover_selector: str = Field(
        default="div#player-wrap::attr(style), div.post-meta img::attr(src), "
        "div#player-wrap img::attr(src), meta[property='og:image']::attr(content)",
        title="封面选择器",
        description="详情页封面写在播放器的 background-image 里（插件会自动从 url(...) 里抠出来），"
        "另一份在 post-meta 的 img 上。两处都是**原图**，不带 ! 尺寸后缀。",
    )
    release_selector: str = Field(
        default="",
        title="发布日期选择器",
        description="**留空即可**：本站详情页压根没有发布日期，日期来自搜索列表条目。"
        "只有你发现了新位置才需要填。",
    )
    maker_selector: str = Field(
        default="div.cats a[href*='/maker/']::text, div.cats a[href*='/studio/']::text",
        title="片商选择器",
        description="按 URL 路径包含匹配而不是 class 名：换皮时路径最不容易变。"
        "取不到会再从正文的「Maker : xxx」里正则兜底。",
    )
    cast_selector: str = Field(
        default="div.cats a[href*='/cast/']::text, div.cats a[href*='/actress/']::text",
        title="女优选择器",
        description="同上，按 URL 路径匹配。FC2 作品页面整块没有 Cast，属正常。",
    )
    tags_selector: str = Field(
        default="div.tags a[href*='/tag/']::text, div.cats a[href*='/tag/']::text",
        title="标签选择器",
        description="按 URL 路径匹配标签链接。",
    )
    category_selector: str = Field(
        default="div.cats p.cat a::text",
        title="分类选择器",
        description="有码 / 无码 / 中文字幕这类大类。默认只用于日志（不写进元数据），"
        "需要时可以自己接。",
    )
    extras_selector: str = Field(
        default="",
        title="剧照选择器",
        description="剧照容器（可选）。本站详情页**没有剧照墙**，默认留空不开。",
    )

    # --- 数据开关 ---
    include_actors: bool = Field(
        default=True,
        title="抓取女优",
        description="从详情页抓出演者列表。",
    )
    include_tags: bool = Field(
        default=True,
        title="抓取标签",
        description="从详情页抓标签列表。",
    )
    include_studio: bool = Field(
        default=True,
        title="抓取片商",
        description="从详情页抓 Maker（片商 / 发行商）。",
    )
    strip_title_prefix: bool = Field(
        default=True,
        title="去掉标题方括号前缀",
        description="本站标题几乎都带「[4K]」「[Reducing Mosaic]」「[Chinese Subtitles]」这类前缀，"
        "默认剥掉开头所有 [xxx] 片段（只影响标题，不影响番号匹配）。",
    )
    tags_max: int = Field(
        default=24,
        title="标签上限",
        description="最多保留多少个标签。",
    )
    tags_drop: str = Field(
        default="supjav,hd,video,jav",
        title="剔除的标签",
        description="要剔除的标签，逗号分隔（大小写不敏感）。留空表示不剔除。",
    )
    cover_allow_gif: bool = Field(
        default=True,
        title="允许 GIF 封面",
        description="允许 GIF 当封面。多数 JAV 站封面是 JPG，装饰性 GIF 已被 logo/banner 等黑名单拦掉。",
    )

    # --- 抓取 ---
    request_retries: int = Field(
        default=2,
        title="重试次数",
        description="额外的重试次数（总请求数 = 重试次数 + 1）。只对网络错误 / 5xx / 429 生效，404 不重试。",
    )
    request_retry_delay: float = Field(
        default=2.0,
        title="重试等待秒数",
        description="首次重试前的等待秒数，之后每次线性递增。",
    )

    # --- Cloudflare 过挑战（本站必开，否则一条都抓不到）---
    browser_fallback: bool = Field(
        default=True,
        title="被 Cloudflare 拦时自动开浏览器",
        description="**本站在 Cloudflare 托管挑战后面**，纯 HTTP 客户端一律 403。"
        "开启后插件会临时起一个本机 Chrome/Edge（窗口立刻最小化）跑一遍挑战、"
        "拿到 cf_clearance 就把它关掉，之后所有请求继续用普通 HTTP 客户端（快、省）。"
        "关掉的话只能靠下面手填 cookie。",
    )
    browser_path: str = Field(
        default="",
        title="浏览器路径",
        description="留空自动找 Chrome，其次 Edge。填绝对路径可指定，例如 "
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    )
    browser_headless: bool = Field(
        default=False,
        title="浏览器无头模式",
        description="**建议保持关闭**：实测无头模式过不了 Cloudflare（即使换了 UA、"
        "注入反自动化脚本），有头模式 + 窗口最小化才稳。",
    )
    browser_proxy: str = Field(
        default="",
        title="浏览器代理",
        description="留空 = 用系统代理（通常就是你 Clash 的混合端口）。"
        "如果浏览器直连不通、系统又没设代理，就在这里填，例如 http://127.0.0.1:7892。",
    )
    challenge_timeout: float = Field(
        default=45.0,
        title="过挑战超时秒数",
        description="等 Cloudflare 挑战自动过关的最长秒数，超过就放弃并报错。",
    )
    session_ttl: int = Field(
        default=1500,
        title="cookie 缓存秒数",
        description="cf_clearance 的有效期。到期后下一次抓取会自动再开一次浏览器。",
    )
    cookie: str = Field(
        default="",
        title="手工 cookie（可选）",
        description="填了就优先用它，形如 cf_clearance=xxxx。"
        "注意 cf_clearance 与「客户端 IP + 浏览器 UA」绑定，所以通常要连下面 UA 一起填。",
    )
    user_agent: str = Field(
        default="",
        title="手工 User-Agent（可选）",
        description="配合上面的 cookie 一起用。留空时用浏览器自动拿到的 UA，"
        "再留空则用内置的 Chrome UA。",
    )
    browser_profile: str = Field(
        default="",
        title="浏览器 profile 目录",
        description="留空 = 插件数据目录下的 browser-profile。"
        "换个目录等于换一个干净的浏览器身份（挑战过不去时可以试试）。",
    )

    # --- 诊断 ---
    debug_dump: bool = Field(
        default=True,
        title="写调试日志",
        description="把每一步写进插件数据目录下的 debug/debug.log —— 排查问题先看这个文件。",
    )
    debug_save_pages: bool = Field(
        default=True,
        title="保存抓到的页面",
        description="把搜索页 / 详情页落盘到 debug 目录（按番号命名，重复运行覆盖）。"
        "调优选择器时请把这个目录发给开发者。",
    )
    cache_resolved: bool = Field(
        default=True,
        title="缓存番号→地址",
        description="命中一次后写入 resolved.json，下次跳过搜索，直接进详情页。",
    )
    index_file: bool = Field(
        default=True,
        title="读取手工索引",
        description="支持自己写 debug/index.csv（每行「番号,详情页 URL」），给已知 URL 用。",
    )
    report_misses: bool = Field(
        default=True,
        title="找不到时报错",
        description="找不到时抛出带原因的错误而不是静默返回空。稳定后想减少日志噪音可以关。",
    )


# ---------- 纯函数 helpers（无网络依赖，便于 selftest）----------


# 验证码 / 反爬拦截页的特征。宿主的 classify_block() 只认很少几个特征，
# 所以插件必须自己再查一遍——这是唯一能看到响应正文的一层。
#
# 注意：**别把 "challenge-platform" 当特征**！挑战页里有它，但真页面也会被
# Cloudflare 塞一段 /cdn-cgi/challenge-platform/scripts/jsd/main.js（JSD 检测脚本），
# 拿它当判据会把正常页面误判成拦截页。用下面这些只在挑战页出现的。
_CHALLENGE_MARKERS: tuple[str, ...] = (
    "select all squares containing",
    "/assets/anomaly/",
    "bots use",
    "verification required",
    "verify you are human",
    "i'm not a robot",
    "g-recaptcha",
    "recaptcha/api.js",
    "hcaptcha.com",
    "altcha",
    "cf-chl-",
    "just a moment",
    "请稍候",
    "challenges.cloudflare.com",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
    "error 1020",
    "error 1015",
    "error 1034",
    "you have been blocked",
    "unusual traffic",
    "automated queries",
    "our systems have detected",
    "captcha-delivery.com",
)


def _is_challenge(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _CHALLENGE_MARKERS)


# 明显不是封面的图片：站点 logo / favicon / 广告条 / 占位图。
_BAD_IMAGE_MARKERS: tuple[str, ...] = (
    "favicon",
    "logo",
    "banner",
    "spacer",
    "blank.",
    "/pixel",
    "pixel.",
    "/ads/",
    "/assets/",
    "data:image/",
    "placeholder",
)


def _is_bad_image(url: str, allow_gif: bool = True) -> bool:
    """黑名单在前：先用路径特征剔除装饰图/占位图，再看 GIF。

    早期版本无条件拒绝 ``.gif``，结果把真实 GIF 封面一起误杀；装饰性 GIF
    其实早就被 ``/assets/`` 之类的 marker 拦掉了，不需要靠扩展名。
    """
    low = (url or "").lower()
    if not low or low.startswith("data:"):
        return True
    if any(marker in low for marker in _BAD_IMAGE_MARKERS):
        return True
    if low.endswith(".gif") or low.endswith(".svg"):
        return not allow_gif
    return False


_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")


def _looks_like_image(url: str) -> bool:
    low = (url or "").lower().split("?")[0].split("#")[0]
    return any(low.endswith(ext) for ext in _IMAGE_EXTS)


def _local_blob(node, own: str, extra: str = "") -> str:
    """取「这一个条目自己的文字」，用于番号比对。

    **不能用祖先的全部文本**：搜索页顶部的 ``Search Result For: HMN-625(0)``
    就在祖先里，一旦把它算进来，任何一条无关结果都会「命中」番号。
    所以只收「看起来像条目容器」的祖先（内部指向详情页的链接 ≤ 2 个、
    文字长度 < 300），整页容器会被这两条挡掉。
    """
    parts = [own, extra]
    try:
        ancestors = node.xpath("ancestor::*[position()<=2]")
    except Exception:  # noqa: BLE001
        ancestors = []
    for ancestor in ancestors:
        try:
            # <html> / <body> 的文本 = 整页文本，永远不能算进来（页头的
            # "Search Result For: HMN-625(0)" 就在那儿）
            if getattr(ancestor.root, "tag", None) in ("html", "body"):
                continue
            if len(ancestor.css("a[href*='.html']")) > 2:
                continue
            text = _squash(" ".join(_safe_css(ancestor, "::text")))
        except Exception:  # noqa: BLE001
            continue
        if text and len(text) < 300:
            parts.append(text)
    return " ".join(part for part in parts if part)


_ARTICLE_HREF_RE = re.compile(r"(\d{3,9})\.html")


def _strip_cdn_suffix(url: str) -> str:
    """列表封面常带 CDN 参数，形式 ``xxx.jpg!thumb`` —— 取 ``!`` 前的原图。

    第三方工具 supjavd 也是这么干的（``thumb.split("!")[0]``）。
    """
    head, sep, _tail = (url or "").partition("!")
    return head if sep else url


def _abs_url(url: str, base: str) -> str:
    """补齐相对 / 协议相对地址。"""
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urljoin(base, url)


_PSEUDO_RE = re.compile(r"::(?:text|attr\([^)]*\))\s*$")


def _bare(selector: str) -> str:
    """剥掉末尾的 ``::text`` / ``::attr(...)``，由调用方决定取什么。"""
    return _PSEUDO_RE.sub("", selector.strip()).strip()


def _has_pseudo(selector: str) -> bool:
    return bool(_PSEUDO_RE.search(selector.strip()))


def _split_selectors(selector: str) -> list[str]:
    return [part.strip() for part in (selector or "").split(",") if part.strip()]


def _text_exprs(selector: str) -> list[str]:
    """把选择器展开成"取文本"的写法。

    ``h1 a::text`` 这种写法存在时原样使用；否则退化成 ``::text``、
    ``后代文本``、``::attr(title)`` 三种写法——<h1><a>标题</a></h1>
     用 ``h1::text`` 是取不到的。
    """
    if _has_pseudo(selector):
        return [selector]
    base = _bare(selector)
    return [f"{base}::text", f"{base} ::text", f"{base}::attr(title)"]


def _safe_css(sel, expr: str) -> list[str]:
    try:
        return sel.css(expr).getall()
    except Exception:  # noqa: BLE001 - 用户粘贴的坏选择器不能让插件崩
        return []


def _first_text(sel, selector: str) -> str | None:
    for part in _split_selectors(selector):
        for expr in _text_exprs(part):
            for value in _safe_css(sel, expr):
                value = (value or "").strip()
                if value:
                    return _squash(value)
    return None


def _all_texts(sel, selector: str) -> list[str]:
    out: list[str] = []
    for part in _split_selectors(selector):
        for expr in _text_exprs(part):
            for value in _safe_css(sel, expr):
                value = _squash((value or "").strip())
                if value and value not in out:
                    out.append(value)
    return out


_COVER_ATTRS = ("data-original", "data-original-src", "data-src", "src", "content", "poster")

_STYLE_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.I)


def _extract_style_url(value: str) -> str:
    """``background-image: url(https://x/y.jpg);`` → ``https://x/y.jpg``。

    本站详情页的封面就写在播放器的 ``style`` 里，所以取 ``::attr(style)``
    之后必须再过一道这个。
    """
    text = (value or "").strip()
    if "url(" not in text.lower():
        return text
    match = _STYLE_URL_RE.search(text)
    return match.group(1).strip() if match else ""


def _first_image_source(sel, selector: str, allow_gif: bool = True) -> str | None:
    """逐个选择器、逐个候选属性找第一张合格图片。

    每部分若自带 ``::attr(x)`` 就按它来；否则按
    ``data-original → data-src → src → content → poster`` 顺序试
    （列表页懒加载普遍用 ``data-original``）。
    """
    for part in _split_selectors(selector):
        if _has_pseudo(part):
            for value in _safe_css(sel, part):
                value = _strip_cdn_suffix(_extract_style_url(value))
                if value and not _is_bad_image(value, allow_gif):
                    return value
            continue
        base = _bare(part)
        for attr in _COVER_ATTRS:
            for value in _safe_css(sel, f"{base}::attr({attr})"):
                value = _strip_cdn_suffix(_extract_style_url(value))
                if value and not _is_bad_image(value, allow_gif):
                    return value
    return None


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _unique(values) -> list[str]:
    out: list[str] = []
    for value in values:
        value = (value or "").strip()
        if value and value not in out:
            out.append(value)
    return out


# ---------- 番号与号码匹配 ----------

# 番号前缀里也可能带数字（FC2 / 1PONDO / 300MIUM / 259LUXU…），所以 charset
# 要含数字；但「纯数字」不是番号，靠 _code_parts 里的字母检查挡掉。
_CODE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9&\-]*?)[-_\s]*(\d{2,8})$")

# FC2 系列番号：FC2-PPV-4981113 / FC2PPV 4981113 / FC2 4981113 / 纯数字
_FC2_RE = re.compile(r"^FC2(?:[-_\s]?PPV)?[-_\s]*(\d{4,9})$", re.I)


def _code_parts(number: str) -> tuple[str, str] | None:
    """``SONE-353`` → ``("SONE", "353")``；纯数字（不是番号）返回 None。"""
    m = _CODE_RE.match((number or "").strip())
    if not m:
        return None
    prefix = m.group(1).strip("-&").upper()
    digits = m.group(2)
    if not any(ch.isalpha() for ch in prefix):
        return None
    return (prefix, digits) if prefix and digits else None


def _fc2_digits(number: str) -> str | None:
    """``FC2-PPV-4981113`` / ``FC2PPV 4981113`` / ``FC2 4981113`` → ``"4981113"``。"""
    m = _FC2_RE.match((number or "").strip())
    return m.group(1) if m else None


def _code_pattern(number: str) -> re.Pattern[str] | None:
    """构造在自由文本里识别该番号的正则。

    要求能容忍 ``SONE-353`` / ``SONE353`` / ``SONE 353`` / ``SONE-0353``
    这些写法，但不能把 ``SONE-3537`` 认成 ``SONE-353``（尾部有数字环视守卫）。

    **FC2 特例（2026-09-27 实测修）**：amane 在 FC2 路由里给的规范写法是
    ``FC2-3125926``，而本站标题一律写成 ``FC2PPV 3125926``。旧版按
    ``FC2[-_\\s]*3125926`` 生成，中间的 ``PPV`` 不匹配，于是**明明搜到了条目
    却被判成错配**（日志里表现为「命中 0 条」）。FC2 的身份信息全在那串数字上，
    ``FC2`` / ``PPV`` 只是前后缀，所以这里把它们做成**可选**：

        ``FC2PPV 3125926`` / ``FC2-PPV-3125926`` / ``FC2 3125926`` /
        ``FC23125926`` / 光秃秃的 ``3125926``  全部算命中
    """
    digits = _fc2_digits(number)
    if digits:
        pattern = (
            r"(?<![0-9A-Za-z])"
            r"(?:FC2[-_\s]*(?:PPV[-_\s]*)?)?"  # FC2 / PPV 前缀可有可无
            r"0*"
            + digits
            + r"(?![0-9])"
        )
        try:
            return re.compile(pattern, re.I)
        except re.error:  # pragma: no cover
            return None

    parts = _code_parts(number)
    if not parts:
        return None
    prefix, digits = parts
    pattern = (
        r"(?<![0-9A-Za-z])"
        + re.escape(prefix).replace(r"\-", r"[-_\s]?")
        + r"[-_\s]*0*"
        + digits
        + r"(?![0-9])"
    )
    try:
        return re.compile(pattern, re.I)
    except re.error:  # pragma: no cover - 番号含奇怪字符
        return None


def _norm_code(number: str) -> str:
    """归一成「只留字母数字的小写串」，用于宽松比对。"""
    return re.sub(r"[^0-9a-z]", "", (number or "").lower())


def _variants(number: str) -> list[str]:
    """站内搜索可以尝试的番号写法（去重保序）。"""
    raw = (number or "").strip()
    parts = _code_parts(raw)
    out: list[str] = [raw]
    if parts:
        prefix, digits = parts
        for candidate in (f"{prefix}-{digits}", f"{prefix}{digits}", f"{prefix} {digits}"):
            if candidate not in out:
                out.append(candidate)
    return out


def _search_variants(number: str, enabled: bool) -> list[str]:
    """真正拿去 ``?s=`` 的查询串序列。

    FC2 特例（实测）：本站标题写 ``FC2PPV 4981113``，用宿主的规范写法
    ``FC2-PPV-4981113`` 搜**一条都搜不到**（0 条），必须换成
    ``FC2PPV 4981113`` 或干脆只搜纯数字。这两条是硬需求，不受 ``try_variants``
    开关影响，否则 FC2 路由永远空手而归。
    """
    raw = (number or "").strip()
    digits = _fc2_digits(raw)
    if digits:
        out = [f"FC2PPV {digits}", digits]
        if enabled:
            out.extend([f"FC2 {digits}", f"FC2PPV-{digits}"])
        return [q for i, q in enumerate(out) if q not in out[:i]]
    return _variants(raw) if enabled else [raw]


_ARTICLE_PREFIX_RE = re.compile(r"^\s*\[([^\]]{1,48})\]")


def _article_score(title: str, preference: str) -> int:
    """同一番号的多个版本里挑一条：无前缀最优先，其次按 ``preference`` 顺序。"""
    text = (title or "").strip()
    match = _ARTICLE_PREFIX_RE.match(text)
    if not match:
        return 1000
    tag = match.group(1).lower()
    for index, token in enumerate(
        t.strip().lower() for t in (preference or "").split(",") if t.strip()
    ):
        if token and token in tag:
            return 900 - index * 10
    return 100


# ---------- 日期 / 时长 ----------

_DATE_RE = re.compile(r"(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})")


def _norm_date(raw: str) -> str | None:
    m = _DATE_RE.search(raw or "")
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


_TIME_LEN_RE = re.compile(
    r"(?:runtime|duration|length|収録時間|再生時間|时长|時間)\s*[:：]?\s*"
    r"(?:(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?|(\d{1,3})\s*(?:min|分|mins|minutes?))",
    re.I,
)


def _parse_runtime_minutes(text: str) -> int | None:
    """正文里的时长字符串 → 分钟。认 ``01:18:04`` / ``78:04`` / ``118 min`` / ``118分``。"""
    m = _TIME_LEN_RE.search(text or "")
    if not m:
        return None
    if m.group(4):
        minutes = int(m.group(4))
        return minutes if 0 < minutes < 1000 else None
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    if m.group(3) is not None:  # H:MM:SS
        total = h * 3600 + mi * 60 + s
    elif ":" in (m.group(0) or ""):  # MM:SS
        total = h * 60 + mi
        h = 0
    else:
        return None
    minutes = round(total / 60)
    return minutes if 0 < minutes < 1000 else None


_LABEL_KEYS = (
    r"Maker|Studio|Brand|Publisher|Production|Label"
    r"|Cast|Actress|Actor|Starring"
    r"|Tags?|Genre|Category|Keywords?"
    r"|Release[d]?|Releas|Pubdate|Date|投稿日|発売日|配信日"
    r"|Duration|Runtime|Length|収録時間|再生時間|時間|时长"
)

# 值用非贪婪 + 前瞻终止：文本里 ``Maker : X Cast : Y`` 是连着的
# （已 squash 成一行），没有前瞻会把 Cast 的值一起吞下来。
_LABEL_RE = re.compile(
    rf"(?<!\w)((?:{_LABEL_KEYS}))\s*[:：]\s*(.*?)(?=\s(?:{_LABEL_KEYS})\s*[:：]|$)",
    re.I,
)


def _label_map(text: str) -> dict[str, str]:
    """从可见文本里抓 ``Maker : xxx`` 这类键值对（CSS 选择器失效时的兜底）。"""
    out: dict[str, str] = {}
    for key, value in _LABEL_RE.findall(_squash(text) or ""):
        key = key.lower()
        value = _squash(value)
        if key and value and key not in out:
            out[key] = value
    return out


_SPLIT_RE = re.compile(r"[,，、/｜|]+")


def _split_names(raw: str) -> list[str]:
    return _unique(v.strip() for v in _SPLIT_RE.split(raw or "") if v.strip())


# ---------- 提供器 ----------


class SupjavProvider(FilmSourceProvider):
    """站内搜索 → 详情页 →（可选）正文正则兜底。"""

    def __init__(self, context: PluginContext, config: SupjavConfig) -> None:
        self._http = context.http_client
        self._web: WebClient = context.web_client
        self._config = config
        self._data_dir = Path(context.data_dir)
        self._debug_dir = self._data_dir / "debug"
        # Cloudflare 会话（cf_clearance + 拿它时用的 UA），内存 + 磁盘各存一份
        self._session: dict | None = None
        self._session_lock: asyncio.Lock | None = None
        self._plain_ua = _DEFAULT_UA

    # ---------- 主流程 ----------

    async def fetch(
        self,
        query: SearchQuery,
        options: FetchOptions | None = None,
    ) -> MediaMetadata | None:
        number = (query.number or "").strip()
        if not number:
            self._log("跳过：查询里没有番号")
            return None
        self._log(f"=== 开始 {number} data_dir={self._data_dir}")

        # 1. 已知 URL：手工索引 / 命中缓存
        known = self._load_known()
        cached = known.get(number)
        if cached:
            url = cached.get("url") if isinstance(cached, dict) else str(cached)
            self._log(f"已知地址：{url}")
            hit = await self._fetch_detail(number, url, cached.get("thumb") if isinstance(cached, dict) else None)
            if hit is not None:
                return hit

        # 2. 站内搜索
        candidates, notes = await self._discover(number)
        if not candidates:
            # 3. 备用搜索引擎
            candidates, ext_notes = await self._external_search(number)
            notes.extend(ext_notes)
        if not candidates:
            return self._miss(number, notes or ["站内搜索没有返回任何候选链接"])

        # 同一部作品常有多条（[4K] / [Reducing Mosaic] / [Chinese Subtitles]…），
        # 先按偏好打分排序再依次尝试；sort 是稳定的，同分时保持列表页的
        # 原始顺序（站点就是按日期倒序排的，等于同分取最新）。
        candidates.sort(
            key=lambda c: -_article_score(c.get("title", ""), self._config.article_preference)
        )

        for cand in candidates[: self._max_candidates()]:
            self._log(
                f"候选 {cand['id']} title={cand.get('title', '')[:60]!r} "
                f"date={cand.get('date')} thumb={'有' if cand.get('thumb') else '无'}"
            )
            meta = await self._fetch_detail(
                number, cand["url"], cand.get("thumb"), cand.get("date")
            )
            if meta is not None:
                if self._config.cache_resolved:
                    self._remember(number, cand["url"], cand.get("thumb"))
                return meta

        notes.append(f"{len(candidates)} 个候选都不可用")
        # 详情页全挂但搜索有标题+封面 → 降级返回最小元数据
        if self._config.accept_search_result:
            fallback = self._from_search(number, candidates[0])
            if fallback is not None:
                self._log("详情页不可用，降级使用搜索结果的标题+封面")
                return fallback
        return self._miss(number, notes)

    def _max_candidates(self) -> int:
        return 3

    # ---------- 发现 ----------

    async def _discover(self, number: str) -> tuple[list[dict], list[str]]:
        """站内搜索，返回候选列表与诊断信息。"""
        from parsel import Selector

        candidates: list[dict] = []
        notes: list[str] = []
        pattern = _code_pattern(number)
        norm = _norm_code(number)
        base_host = urlsplit(self._config.base_url).netloc.lower()

        for query in _search_variants(number, self._config.try_variants):
            url = self._search_url(query)
            text, err = await self._get(url)
            if err:
                notes.append(f"站内搜索 {query!r} 失败：{err}")
                continue
            if self._config.debug_save_pages:
                self._dump(f"{_safe_name(number)}.search.html", text)
            if _is_challenge(text):
                notes.append(f"站内搜索 {query!r} 撞到人机验证/反爬页")
                continue
            sel = Selector(text=text)
            found = self._extract_candidates(sel, urlsplit(url).scheme + "://" +
                                             urlsplit(url).netloc, pattern, norm, number)
            self._log(f"站内搜索 {query!r}: 命中 {len(found)} 条"
                      f"（页内详情页链接共 {len(re.findall(_ARTICLE_HREF_RE, text))} 条）")
            for item in found:
                if all(item["id"] != existing["id"] for existing in candidates):
                    candidates.append(item)
            if candidates and not self._config.try_variants:
                break
            if candidates:
                break
        if not candidates:
            notes.append(f"站内搜索没找到番号 {number} 对得上的作品")
        self._log(
            f"发现阶段结束：候选 {len(candidates)} 条"
            + (f"（含 {len({c['host'] for c in candidates})} 个主机，目标主机 {base_host}）"
               if candidates else "")
        )
        return candidates, notes

    def _extract_candidates(
        self,
        sel,
        page_url: str,
        pattern: re.Pattern[str] | None,
        norm: str,
        number: str,
    ) -> list[dict]:
        """从搜索结果页挑出番号对得上的条目。

        2026-09-27 实测结构::

            div.posts > div.post
                a.img[href][title] > img.thumb[data-original]
                div.con > h3 > a[href][title]
                div.con > div.meta        → "2024/10/23<span>81832 Views</span>"

        但**不硬依赖这套 class**：先按这个结构取，取不到再退化成「扫所有指向
        本站详情页的 ``<a>``」，因为 ``/{id}.html`` 的 URL 形状比 class 稳得多。
        """
        target_host = urlsplit(self._config.base_url).netloc.lower()

        def keep(href: str) -> tuple[str, str] | None:
            """返回 (文章 id, 绝对 URL)，不合格则 None。"""
            match = re.search(r"(\d{3,9})\.html", href or "")
            if not match:
                return None
            full = href if href.startswith("http") else urljoin(page_url, href)
            host = urlsplit(full).netloc.lower()
            if host != target_host and not host.endswith("." + target_host):
                return None
            if re.search(r"/(category|tag|page|genre|cast|maker)/", urlsplit(full).path, re.I):
                return None
            return match.group(1), full

        def add(out: list[dict], seen: set[str], article_id: str, full: str, host: str,
                title: str, blob: str, thumb: str | None, date: str | None) -> None:
            if article_id in seen:
                return
            if pattern:
                # 严格：SONE-3534 这种相似番号不能混进来（尾部有数字环视守卫）
                ok = bool(pattern.search(blob))
            else:
                # 纯数字番号没有字母前缀，只能做子串宽松比对
                ok = bool(norm) and norm in _norm_code(blob)
            if not ok:
                return
            seen.add(article_id)
            out.append(
                {
                    "id": article_id,
                    "url": full,
                    "host": host,
                    "title": _squash(title)[:300],
                    "thumb": thumb,
                    "date": date,
                }
            )

        out: list[dict] = []
        seen: set[str] = set()

        # ① 首选：真实列表结构
        for post in sel.css("div.posts div.post, div.post"):
            anchor = post.css("div.con h3 a[href*='.html']") or post.css("a.img[href*='.html']")
            if not anchor:
                continue
            href = (anchor[0].attrib.get("href") or "").strip()
            parsed = keep(href)
            if not parsed:
                continue
            article_id, full = parsed
            title = _squash(
                post.css("div.con h3 a::text").get()
                or anchor[0].attrib.get("title")
                or _squash(" ".join(_safe_css(post, "div.con h3 ::text")))
            )
            thumb = None
            for attr in ("data-original", "data-src", "src"):
                for value in _safe_css(post, f"img::attr({attr})"):
                    value = _strip_cdn_suffix(_extract_style_url(value))
                    if value and not _is_bad_image(value):
                        thumb = _abs_url(value, page_url)
                        break
                if thumb:
                    break
            date = None
            for raw in _safe_css(post, "div.con div.meta::text") + _safe_css(post, "div.meta::text"):
                date = _norm_date(raw)
                if date:
                    break
            blob = " ".join(
                x for x in (
                    title,
                    anchor[0].attrib.get("title") or "",
                    " ".join(_safe_css(post, "img::attr(alt)")),
                    _squash(" ".join(_safe_css(post, "::text"))),
                ) if x
            )
            add(out, seen, article_id, full, urlsplit(full).netloc.lower(),
                title, blob, thumb, date)

        if out:
            return out

        # ② 兜底：结构变了也能找到「指向详情页的链接」
        for node in sel.css("a[href*='.html']"):
            parsed = keep((node.attrib.get("href") or "").strip())
            if not parsed:
                continue
            article_id, full = parsed
            title = _squash(
                node.attrib.get("title")
                or " ".join(_safe_css(node, "::text"))
                or _squash(" ".join(_safe_css(node, "img::attr(alt)")))
            )
            blob = _local_blob(
                node,
                title,
                " ".join(_safe_css(node, "img::attr(alt)")),
            )
            thumb = None
            for attr in ("data-original", "data-src", "src"):
                for value in _safe_css(node, f"img::attr({attr})"):
                    value = _strip_cdn_suffix((value or "").strip())
                    if value and not _is_bad_image(value):
                        thumb = _abs_url(value, page_url)
                        break
                if thumb:
                    break
            add(out, seen, article_id, full, urlsplit(full).netloc.lower(),
                title, blob, thumb, None)
        return out

    async def _external_search(self, number: str) -> tuple[list[dict], list[str]]:
        """配置里的备用搜索引擎（默认关闭）。"""
        engines = [t.strip() for t in (self._config.fallback_engines or "").split(",") if t.strip()]
        notes: list[str] = []
        if not engines:
            return [], notes
        target_host = urlsplit(self._config.base_url).netloc.lower()
        pattern = _code_pattern(number)
        for template in engines:
            url = template.format(q=quote(number), number=quote(number))
            text, err = await self._get(url)
            if err:
                notes.append(f"备用引擎 {template} 失败：{err}")
                continue
            if _is_challenge(text):
                notes.append(f"备用引擎 {template} 撞到人机验证")
                continue
            hits = [
                u for u in re.findall(r'https?://[^\s"\'<>()]+', text)
                if target_host in urlsplit(u).netloc.lower() and re.search(r"(\d{3,9})\.html", u)
            ]
            # 引擎摘要里未必带番号，这里只做域名筛选，番号对错交给详情页校验
            hits = list(dict.fromkeys(hits))
            if hits:
                self._log(f"备用引擎命中 {len(hits)} 条")
                return [
                    {"id": re.search(r"(\d{3,9})\.html", u).group(1), "url": u,
                     "host": urlsplit(u).netloc.lower(), "title": "", "thumb": None}
                    for u in hits[:5]
                ], notes
            notes.append(f"备用引擎 {template} 没有可用结果")
        return [], notes

    # ---------- 详情 ----------

    async def _fetch_detail(
        self,
        number: str,
        url: str,
        fallback_thumb: str | None,
        release: str | None = None,
    ):
        from parsel import Selector

        text, err = await self._get(url)
        if err:
            self._log(f"详情页失败：{url} -> {err}")
            return None
        if self._config.debug_save_pages:
            self._dump(f"{_safe_name(number)}.{_article_id(url)}.article.html", text)
        if _is_challenge(text):
            self._log(f"详情页撞到人机验证/反爬页：{url}")
            return None
        sel = Selector(text=text)
        meta = self._parse(number, url, sel, fallback_thumb, release)
        if meta is None:
            self._log(f"详情页解析失败（选择器命中见上一行）：{url}")
            return None
        self._log(
            f"命中 {number}: title={(meta.title or '')[:60]!r} release={meta.release} "
            f"runtime={meta.runtime} studio={meta.studio} tags={len(meta.tags)} "
            f"actors={len(meta.actors)} poster={len(meta.poster_urls)}"
        )
        return meta

    def _parse(
        self,
        number: str,
        url: str,
        sel,
        fallback_thumb: str | None,
        release: str | None = None,
    ):
        cfg = self._config

        # --- 番号校验（避免搜到同名不同号）---
        visible = _squash(" ".join(_safe_css(sel, "body ::text")))
        pattern = _code_pattern(number)
        title_hit = True
        if self._config.verify_number and pattern:
            title_hit = bool(pattern.search(visible))
            if not title_hit:
                self._log(f"详情页正文里找不到番号 {number}，判定为错配：{url}")
                return None

        # --- 标题 ---
        title = _first_text(sel, cfg.title_selector)
        if not title:
            title = _first_text(sel, "title::text")
        if title:
            title = _squash(title)
            title = re.sub(r"\s*[-|]\s*(supjav|SupJav).*$", "", title).strip() or title
        if not title:
            self._log(f"详情页没有标题：{url}")
            return None
        if cfg.strip_title_prefix:
            stripped = re.sub(r"^\s*(?:\[[^\]]*\]\s*)+", "", title).strip()
            title = stripped or title

        # --- 封面 ---
        cover = _first_image_source(sel, cfg.cover_selector, cfg.cover_allow_gif)
        if cover:
            cover = _abs_url(cover, url)
        if not cover and cfg.list_thumb_fallback and fallback_thumb:
            cover = _abs_url(fallback_thumb, url)
            self._log("封面取自搜索列表")
        if cover and not _looks_like_image(cover) and "?" not in cover:
            self._log(f"封面 URL 看着不像图片，仍然返回：{cover}")

        # --- 发布日期 ---
        # 详情页**没有**日期，优先用候选条目（搜索结果列表）带过来的那个。
        release_date = release or _norm_date(_first_text(sel, cfg.release_selector) or "")
        if not release_date and cfg.release_selector:
            release_date = _norm_date(visible)

        # --- 时长（本站没有，留着兜底）---
        runtime = _parse_runtime_minutes(visible)

        # --- 片商 / 女优 / 标签：选择器优先，正文正则兜底 ---
        labels = _label_map(visible)
        studio = None
        if cfg.include_studio:
            studio = _first_text(sel, cfg.maker_selector)
            if not studio:
                studio = labels.get("maker") or labels.get("studio")
        actors: list[str] = []
        if cfg.include_actors:
            actors = _all_texts(sel, cfg.cast_selector)
            if not actors:
                actors = _split_names(labels.get("cast", ""))
        tags: list[str] = []
        if cfg.include_tags:
            tags = _all_texts(sel, cfg.tags_selector)
            if not tags:
                tags = _split_names(labels.get("tags") or labels.get("genre") or "")

        # 输出封面命中诊断（调优选择器时最快信号）
        self._log_selectors(sel)

        drop = {t.strip().lower() for t in (cfg.tags_drop or "").split(",") if t.strip()}
        tags = [t for t in tags if t and t.lower() not in drop][: max(0, cfg.tags_max)]

        extras: list[str] = []
        if cfg.extras_selector:
            extras = [
                _abs_url(_strip_cdn_suffix(v.strip()), url)
                for v, _ in [
                    (v, None) for v in _safe_css(sel, f"{_bare(cfg.extras_selector)}::attr(data-src)")
                ]
                if v and not _is_bad_image(v)
            ]
            if not extras:
                extras = [
                    _abs_url(_strip_cdn_suffix(v.strip()), url)
                    for v in _safe_css(sel, f"{_bare(cfg.extras_selector)}::attr(data-original)")
                    if v and not _is_bad_image(v)
                ]
            if not extras:
                extras = [
                    _abs_url(_strip_cdn_suffix(v.strip()), url)
                    for v in _safe_css(sel, f"{_bare(cfg.extras_selector)}::attr(src)")
                    if v and not _is_bad_image(v)
                ]

        fields = _metadata_field_names()
        kwargs: dict[str, object] = {
            "number": number,
            "title": title,
            "source_url": url,
            "external_id": _article_id(url) or number,
        }
        if release_date:
            kwargs["release"] = release_date
        if runtime:
            kwargs["runtime"] = runtime
        if studio and "studio" in fields:
            kwargs["studio"] = studio
        if tags and "tags" in fields:
            kwargs["tags"] = tags
        if cover:
            if "poster_urls" in fields:
                kwargs["poster_urls"] = [cover]
            if "thumb_urls" in fields:
                kwargs["thumb_urls"] = [cover]
        if actors and "actors" in fields:
            # list[str] 必须经构造器传入：宿主的 field_validator 才会把字符串收成
            # FilmActor（pydantic 的属性赋值不跑校验器，事后 meta.actors = [...] 会留裸 str）
            kwargs["actors"] = actors  # type: ignore[assignment]
        if extras and "extrafanart" in fields:
            kwargs["extrafanart"] = extras  # type: ignore[assignment]
        return MediaMetadata(**kwargs)

    def _from_search(self, number: str, cand: dict):
        """详情页不可用时的最小元数据（标题 + 封面 + 日期）。"""
        title = (cand.get("title") or "").strip()
        thumb = cand.get("thumb")
        if not title and not thumb:
            return None
        if self._config.strip_title_prefix and title:
            title = re.sub(r"^\s*(?:\[[^\]]*\]\s*)+", "", title).strip() or title
        fields = _metadata_field_names()
        kwargs: dict[str, object] = {
            "number": number,
            "title": title or number,
            "source_url": cand.get("url"),
            "external_id": cand.get("id") or number,
        }
        if cand.get("date") and "release" in fields:
            kwargs["release"] = cand["date"]
        if thumb:
            if "poster_urls" in fields:
                kwargs["poster_urls"] = [thumb]
            if "thumb_urls" in fields:
                kwargs["thumb_urls"] = [thumb]
        return MediaMetadata(**kwargs)

    # ---------- 网络 ----------

    async def _get(self, url: str) -> tuple[str | None, str | None]:
        """带退避重试的 GET。

        404 不重试（确定无此番号）。**撞上 Cloudflare 挑战时会先过一次挑战
        （起本机浏览器拿 cf_clearance）再重试**，且这次过挑战不算重试次数。
        """
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
                    url,
                    headers=self._headers(),
                    cookies=self._cookie_dict(),
                )
                if text and not _is_challenge(text):
                    return text, None
                if text:
                    challenged = True
                    last_err = "Cloudflare 挑战页（HTTP 200，但正文是挑战页）"
                else:
                    last_err = "空响应"
            except SourceError as exc:
                status = getattr(exc, "http_status", None)
                reason = getattr(getattr(exc, "reason", None), "value", str(exc))
                if status == 404:
                    return None, f"HTTP 404：{url}"
                if reason in ("cloudflare_challenge", "cloudflare_blocked") or status == 403:
                    challenged = True
                last_err = f"HTTP {status or '-'} {reason}"
            except Exception as exc:  # noqa: BLE001 - 任何异常都要落到诊断里
                last_err = f"{type(exc).__name__}: {exc}"

            if challenged and refresh_left > 0:
                refresh_left -= 1
                # 之前拿到的 clearance 可能过期了，清掉重拿
                self._session = None
                if await self._refresh_session(url):
                    self._log(f"已准备好 Cloudflare 会话，重试：{url}")
                    index -= 1  # 过挑战不算一次失败重试
                    continue
                if not self._config.browser_fallback:
                    last_err = f"{last_err}（Cloudflare 拦截，且未开启自动过挑战）"

            if index < attempts:
                self._log(f"抓取失败（{last_err}），{delay:.1f}s 后重试（{index}/"
                          f"{attempts - 1}）")
                await asyncio.sleep(delay)
                delay += self._config.request_retry_delay
        return None, last_err

    # ---------- Cloudflare 会话（cf_clearance + 配套 UA）----------
    #
    # 本站整站在 Cloudflare 托管挑战后面：纯 HTTP 客户端拿到的永远是
    # ``cType: 'managed'`` 的挑战页。唯一稳定的过法是「真浏览器跑一遍 JS」，
    # 所以这里起一次有头 Chrome（立刻最小化）→ 导出 cookie → 关掉，
    # 之后所有请求都带着这串 cookie 走宿主的普通 HTTP 客户端。
    #
    # cf_clearance 与「客户端 IP + 浏览器 UA」绑定，所以 cookie 和 UA 必须成对用，
    # 这也是为什么 UA 也一起缓存、而不是写死一个常量。

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
        ua = (session.get("user_agent") or self._config.user_agent.strip() or _DEFAULT_UA)
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

    async def _refresh_session(self, url: str) -> bool:
        """起本机浏览器过一遍 Cloudflare 挑战，把 cf_clearance 落到磁盘。"""
        if not self._config.browser_fallback:
            return False
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()
        async with self._session_lock:
            # 同一批任务可能一起撞上挑战，先看有没有别人刚刷好的
            fresh = self._read_session_file()
            if fresh and fresh.get("source") != "config":
                self._session = fresh
                return True
            parts = urlsplit(url)
            target = f"{parts.scheme or 'https'}://{parts.netloc}/"  # 首页最省事
            profile = self._browser_profile_dir()
            log_path = self._debug_dir / "chrome.log"
            # 先试「有头 + 立刻最小化」（不打扰用户），不行再试「有头 + 窗口可见」。
            # 实测无头模式过不了 Cloudflare，所以不把它排进梯队。
            attempts = [
                ("minimized", self._config.browser_headless, not self._config.browser_headless),
                ("visible", False, False),
            ]
            result: dict | None = None
            for label, headless, minimize in attempts:
                self._log(f"Cloudflare 挑战：起浏览器过挑战（{label}，{target}）")
                try:
                    result = await asyncio.to_thread(
                        browser_fetch,
                        target,
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
                self._log(f"浏览器没能拿到 cookie（{label}）：挑战没过，换下一种模式再试")
            if result is None:
                return False
            if self._config.debug_save_pages and result.get("raw_html"):
                self._dump("cloudflare.last.html", result["raw_html"])
            names = [c.get("name") for c in result.get("cookies") or []]
            if not names:
                self._log(
                    "浏览器始终没拿到 cookie：挑战没过。"
                    "可看 debug/cloudflare.*.html 与 debug/chrome.log 判断卡在哪；"
                    "也可以换个「浏览器 profile 目录」，或手工填 cookie + UA。"
                )
                return False
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
                f"cf_clearance={'有' if 'cf_clearance' in names else '无'} ua={session['user_agent']}"
            )
            return True

    def _search_url(self, query: str) -> str:
        base = self._config.base_url.rstrip("/")
        lang = (self._config.lang_path or "").strip("/")
        root = f"{base}/{lang}" if lang else base
        path = (self._config.search_path or "/?s={q}").strip()
        if not path.startswith("/"):
            path = "/" + path
        url = root + path
        return url.replace("{q}", quote(query))

    def _article_url(self, article_id: str) -> str:
        base = self._config.base_url.rstrip("/")
        lang = (self._config.lang_path or "").strip("/")
        return (self._config.article_url_template or "{base}/{id}.html").replace(
            "{base}", base
        ).replace("{lang}", lang).replace("{id}", article_id)

    # ---------- 诊断 ----------

    def _miss(self, number: str, notes: list[str]):
        detail = "; ".join(notes) if notes else "没有任何可用来源"
        self._log(f"未命中 {number}：{detail}")
        if self._config.report_misses:
            raise SourceError(FailureReason.NO_USABLE_METADATA, detail=f"supjav: {detail}")
        return None

    def _log_selectors(self, sel) -> None:
        """打印每个选择器的命中数——站点换皮时这是最快的定位方式。"""
        cfg = self._config
        parts = []
        for name, selector in (
            ("title", cfg.title_selector),
            ("cover", cfg.cover_selector),
            ("release", cfg.release_selector),
            ("maker", cfg.maker_selector),
            ("cast", cfg.cast_selector),
            ("tags", cfg.tags_selector),
        ):
            counts = []
            for part in _split_selectors(selector)[:4]:
                try:
                    n = len(sel.css(part))
                except Exception:  # noqa: BLE001
                    n = -1
                counts.append(f"{part}={n}")
            parts.append(f"{name}[{', '.join(counts)}]")
        self._log("选择器命中: " + "  ".join(parts))

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

    # ---------- 已知 URL 缓存 / 手工索引 ----------

    def _index_path(self) -> Path:
        return self._debug_dir / "index.csv"

    def _cache_path(self) -> Path:
        return self._data_dir / "resolved.json"

    def _load_known(self) -> dict[str, dict]:
        """merge index.csv（手工）+ resolved.json（自动缓存），后者优先。"""
        out: dict[str, dict] = {}
        if self._config.index_file:
            try:
                for line in self._index_path().read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "," not in line:
                        continue
                    number, _, url = line.partition(",")
                    number, url = number.strip(), url.strip()
                    if number and url:
                        out[number] = {"url": url, "thumb": None}
            except Exception:  # noqa: BLE001 - 没有这个文件属正常
                pass
        if self._config.cache_resolved:
            try:
                raw = json.loads(self._cache_path().read_text(encoding="utf-8") or "{}")
                for number, value in (raw or {}).items():
                    if isinstance(value, str):
                        out[number] = {"url": value, "thumb": None}
                    elif isinstance(value, dict) and value.get("url"):
                        out[number] = {"url": value.get("url"), "thumb": value.get("thumb")}
            except Exception:  # noqa: BLE001
                pass
        return out

    def _remember(self, number: str, url: str, thumb: str | None) -> None:
        if not self._config.cache_resolved:
            return
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            raw: dict[str, object] = {}
            if self._cache_path().exists():
                try:
                    loaded = json.loads(self._cache_path().read_text(encoding="utf-8") or "{}")
                    if isinstance(loaded, dict):
                        raw = loaded
                except ValueError:
                    raw = {}
            raw[number] = {"url": url, "thumb": thumb}
            self._cache_path().write_text(
                json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass


def _safe_name(number: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]", "_", number or "") or "unknown"


def _article_id(url: str) -> str:
    m = re.search(r"(\d{3,9})\.html", url or "")
    return m.group(1) if m else ""


def _metadata_field_names() -> set[str]:
    """探测宿主 MediaMetadata 支持哪些字段（兼容 pydantic v1 / v2 与旧版 amane）。

    ``extrafanart`` / ``actors`` 在新版 SDK 里有，老版本没有。少了字段时跳过赋值，
    绝不因缺字段而崩。
    """
    try:
        fields = getattr(MediaMetadata, "model_fields", None)  # pydantic v2
        if callable(fields) or fields is None:
            fields = getattr(MediaMetadata, "__fields__", {})  # pydantic v1
        return set(fields.keys())
    except Exception:  # noqa: BLE001
        return set()


# ---------- 入口 ----------


class Plugin(FilmSourcePlugin):
    """放在 ``{数据目录}/plugins/sources/esl1.supjav/`` 下的 plugin.py 必须导出此类。"""

    config_model: ClassVar[type[BaseModel]] = SupjavConfig

    @classmethod
    def descriptor(cls) -> SourceDescriptor:
        return SourceDescriptor(
            id="esl1.supjav",
            name="supjav.com (JAV 在线片库)",
            version="0.2.1",
            capabilities=frozenset({SourceCapability.FILM_METADATA}),
            # 本站收录有码 / 无码 / 中文字幕 / FC2 素人。
            # **想接到别的路由（chinese / amateur / hentai）就改这一行**，
            # 只声明不接路由不会被调用，反之没声明就在路由里选不到这个源。
            content_types=frozenset({"censored", "uncensored", "fc2"}),
            metadata_fields=frozenset(
                {
                    "title",
                    "actors",
                    "tags",
                    "release",
                    "studio",
                    "poster_urls",
                    "thumb_urls",
                    "source_url",
                }
            ),
            urls=(BASE, IMAGE_HOST),
            rate_limit=1.0,
        )

    def build(self, context: PluginContext, config: BaseModel) -> FilmSourceProvider:
        if not isinstance(config, SupjavConfig):
            raise TypeError("unexpected config type")
        return SupjavProvider(context, config)


# ============================================================================
#  以下为内置的极简 Chrome DevTools Protocol 客户端
#
#  作用：本站整站在 Cloudflare 托管挑战后面，curl_cffi 过不去（实测各种
#  指纹都不行），无头 Chrome 也不行 —— 只有「有头 Chrome + 窗口最小化」能
#  自动过关。于是插件自己起一次浏览器，把 cf_clearance 取出来交给普通 HTTP
#  客户端复用，然后立刻关掉浏览器。
#
#  只用标准库（socket / struct / subprocess + 手写 WebSocket 帧），因为插件
#  跑在打包好的 amane 里，装不了第三方包。
# ============================================================================

"""极简 Chrome DevTools Protocol 客户端（只用标准库）。

用途：**过 Cloudflare 挑战**。curl_cffi 再像浏览器也执行不了挑战页里的 JS，
但只要本机有 Chrome / Edge，就能借它真跑一遍 JS、拿到 ``cf_clearance``，
再把 cookie 交给 curl_cffi 复用（快、且不占浏览器）。

本文件是自包含的：插件里直接内联同一份实现，不依赖任何第三方包。
"""

import base64
import os
import shutil
import socket
import struct
import subprocess
import time
from urllib.request import ProxyHandler, Request, build_opener, urlopen

# ---------- 找浏览器 ----------

_WIN_BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
)

_POSIX_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
)

CHALLENGE_MARKERS = (
    "just a moment",
    "请稍候",
    "cf-chl",
    "checking your browser",
    "attention required",
)

# 无头 Chrome 会自己暴露 navigator.webdriver 等信号，Cloudflare 一眼识破。
# 这段在新文档执行前注入，抹掉最常见的几个自动化痕迹。
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => false});
if (!window.chrome) { window.chrome = {}; }
if (!window.chrome.runtime) { window.chrome.runtime = {}; }
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
Object.defineProperty(navigator, 'plugins', {
  get: () => [
    {name: 'PDF Viewer'}, {name: 'Chrome PDF Viewer'},
    {name: 'Chromium PDF Viewer'}, {name: 'Microsoft Edge PDF Viewer'},
    {name: 'WebKit built-in PDF'},
  ],
});
if (navigator.permissions && navigator.permissions.query) {
  const original = navigator.permissions.query.bind(navigator.permissions);
  navigator.permissions.query = (params) =>
    params && params.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : original(params);
}
"""


def find_browser(explicit: str | None = None) -> str | None:
    """定位可用的 Chrome / Edge。``explicit`` 非空时优先。"""
    if explicit:
        path = Path(os.path.expandvars(explicit)).expanduser()
        if path.exists():
            return str(path)
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (_WIN_BROWSERS if sys.platform == "win32" else _POSIX_BROWSERS):
        path = Path(os.path.expandvars(candidate))
        if path.exists():
            return str(path)
    return None


def looks_blocked(html: str) -> bool:
    low = (html or "").lower()
    return any(marker in low for marker in CHALLENGE_MARKERS)


# ---------- 最小 WebSocket 客户端 ----------


class WSError(RuntimeError):
    pass


class _WebSocket:
    def __init__(self, url: str, timeout: float = 30.0) -> None:
        if not url.startswith("ws://"):
            raise WSError(f"只支持 ws://：{url}")
        rest = url[5:]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self._sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {hostport}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(handshake.encode())
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise WSError("握手时连接被关闭")
            header += chunk
        head, _, remainder = header.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise WSError(f"握手失败：{head.split(b'\r\n')[0]!r}")
        self._buf = remainder
        self._frag_opcode: int | None = None
        self._frag_data = b""

    # -- 底层读写 --

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise WSError("连接被关闭")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv_text(self, timeout: float | None = None) -> str:
        if timeout is not None:
            self._sock.settimeout(timeout)
        while True:
            b1, b2 = self._read_exact(2)
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if b2 & 0x80 else None
            payload = self._read_exact(length) if length else b""
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise WSError("服务端关闭了 WebSocket")
            if opcode == 0x9:  # ping → pong
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                if fin:
                    return payload.decode("utf-8", "replace")
                self._frag_opcode, self._frag_data = opcode, payload
                continue
            if opcode == 0x0 and self._frag_opcode is not None:
                self._frag_data += payload
                if fin:
                    data = self._frag_data
                    self._frag_opcode, self._frag_data = None, b""
                    return data.decode("utf-8", "replace")
                continue

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._sock.close()
        except Exception:  # noqa: BLE001
            pass


# ---------- CDP 会话 ----------


class CDP:
    def __init__(self, ws_url: str, timeout: float = 30.0) -> None:
        self._ws = _WebSocket(ws_url, timeout=timeout)
        self._id = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        """发一条命令并等它的回包（事件顺手丢弃）。"""
        self._id += 1
        mid = self._id
        self._ws.send_text(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while True:
            left = max(0.5, deadline - time.time())
            message = json.loads(self._ws.recv_text(timeout=left))
            if message.get("id") == mid:
                if "error" in message:
                    raise WSError(f"{method} 失败：{message['error']}")
                return message.get("result") or {}

    def evaluate(self, expression: str, timeout: float = 20.0):
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        return (result.get("result") or {}).get("value")

    def close(self) -> None:
        self._ws.close()


def _json_get(url: str, method: str = "GET", timeout: float = 10.0):
    # 本机回环的调试端口必须**绕过代理**：urllib 默认读 http_proxy 环境变量，
    # 于是 127.0.0.1:<port> 也被发去代理，表现为「调试接口没有就绪」。
    opener = build_opener(ProxyHandler({}))
    request = Request(url, method=method)
    with opener.open(request, timeout=timeout) as response:  # noqa: S310 - 本机回环
        return json.loads(response.read().decode("utf-8", "replace") or "{}")


class Browser:
    """临时起一个带独立 profile 的 Chrome，用完关掉。"""

    def __init__(
        self,
        profile_dir: str | Path,
        *,
        browser_path: str | None = None,
        headless: bool = True,
        proxy: str | None = None,
        extra_args: tuple[str, ...] = (),
        log_path: str | Path | None = None,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.exe = find_browser(browser_path)
        if not self.exe:
            raise WSError("找不到 Chrome / Edge，请安装或在设置里指定路径")
        self.headless = headless
        self.proxy = proxy
        self.extra_args = extra_args
        self.log_path = Path(log_path) if log_path else None
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self._log_handle = subprocess.DEVNULL

    def __enter__(self) -> "Browser":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> "Browser":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        # 上一次崩溃残留的锁会让 Chrome 直接退出，先清掉
        for stale in ("SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort"):
            try:
                (self.profile_dir / stale).unlink()
            except Exception:  # noqa: BLE001
                pass
        args = [
            self.exe,
            f"--user-data-dir={self.profile_dir}",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-extensions",
            "--disable-blink-features=AutomationControlled",
            # 这三条很关键：Chrome 会「省电」地掐掉后台/被遮挡窗口的定时器和
            # requestAnimationFrame，而 Cloudflare 的挑战页正是靠定时器和 rAF
            # 跑完的 —— 掐掉就永远停在「请稍候」。从后台进程（比如 amane 服务）
            # 启动的浏览器窗口更容易被判成后台，所以必须显式关掉这些节流。
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-features=Translate,OptimizationHints,MediaRouter,CalculateNativeWinOcclusion",
            "--lang=zh-CN",
        ]
        if self.headless:
            args.append("--headless=new")
            args.append("--disable-gpu")
        else:
            args += ["--window-size=1280,800", "--window-position=0,0"]
        if self.proxy:
            args.insert(1, f"--proxy-server={self.proxy}")
        args += list(self.extra_args)
        args.append("about:blank")
        if self.log_path:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self._log_handle = self.log_path.open("wb")
            except Exception:  # noqa: BLE001
                self._log_handle = subprocess.DEVNULL
        else:
            self._log_handle = subprocess.DEVNULL
        creation = 0x08000000 if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(  # noqa: S603
                args,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creation,
            )
        except Exception as exc:  # noqa: BLE001
            raise WSError(f"启动浏览器失败：{exc}") from exc
        port_file = self.profile_dir / "DevToolsActivePort"
        deadline = time.time() + 30
        while time.time() < deadline:
            if port_file.exists():
                try:
                    first = port_file.read_text(encoding="utf-8", errors="replace").splitlines()[0]
                    if first.strip().isdigit():
                        self.port = int(first.strip())
                        break
                except Exception:  # noqa: BLE001
                    pass
            if self.proc.poll() is not None:
                raise WSError(f"浏览器启动即退出（exit={self.proc.returncode}）")
            time.sleep(0.25)
        if not self.port:
            raise WSError("浏览器没写出调试端口（启动超时）")
        # 等调试接口就绪
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                _json_get(f"http://127.0.0.1:{self.port}/json/version", timeout=3)
                return self
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        raise WSError("调试接口没有就绪")

    def open_tab(self, url: str = "about:blank") -> CDP:
        base = f"http://127.0.0.1:{self.port}"
        target = None
        for method in ("PUT", "GET"):
            try:
                target = _json_get(f"{base}/json/new?{url}", method=method, timeout=10)
                break
            except Exception:  # noqa: BLE001
                continue
        if not target or "webSocketDebuggerUrl" not in target:
            raise WSError("新建标签页失败")
        return CDP(target["webSocketDebuggerUrl"])

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=6)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        try:
            if self._log_handle not in (subprocess.DEVNULL, None):
                self._log_handle.close()
        except Exception:  # noqa: BLE001
            pass


# ---------- 对外主函数 ----------


def browser_fetch(
    url: str,
    *,
    profile_dir: str | Path,
    browser_path: str | None = None,
    headless: bool = True,
    proxy: str | None = None,
    challenge_timeout: float = 45.0,
    stealth: bool = True,
    minimize: bool = False,
    log_path: str | Path | None = None,
) -> dict:
    """用本机浏览器打开 ``url``，等 Cloudflare 过关后取 DOM / cookie / UA。

    返回 ``{"html", "cookies", "cookie_header", "user_agent", "url", "blocked"}``；
    页面一直停在挑战页则 ``html`` 为 ``None``、``blocked=True``，
    并在 ``raw_html`` 里给出当时的页面，便于事后判断到底卡在哪。
    """
    diag: list[str] = []
    with Browser(
        profile_dir,
        browser_path=browser_path,
        headless=headless,
        proxy=proxy,
        log_path=log_path,
    ) as browser:
        diag.append(f"browser={browser.exe} headless={headless} port={browser.port}")
        cdp = browser.open_tab("about:blank")
        try:
            cdp.call("Page.enable", timeout=15)
            cdp.call("Runtime.enable", timeout=15)
            if minimize:
                try:
                    window = cdp.call("Browser.getWindowForTarget", timeout=10)
                    cdp.call(
                        "Browser.setWindowBounds",
                        {"windowId": window["windowId"], "bounds": {"windowState": "minimized"}},
                        timeout=10,
                    )
                    diag.append("window minimized")
                except Exception as exc:  # noqa: BLE001
                    diag.append(f"minimize failed: {type(exc).__name__}: {exc}")
            if stealth:
                try:
                    cdp.call(
                        "Page.addScriptToEvaluateOnNewDocument",
                        {"source": STEALTH_JS},
                        timeout=10,
                    )
                except Exception:  # noqa: BLE001
                    pass
            # 让页面以为自己在前台：被遮挡/未聚焦的页面会被 Chrome 掐掉定时器，
            # Cloudflare 的挑战就跑不完。
            try:
                cdp.call("Emulation.setFocusEmulationEnabled", {"enabled": True}, timeout=10)
            except Exception:  # noqa: BLE001
                pass
            try:
                cdp.call("Page.bringToFront", timeout=10)
            except Exception:  # noqa: BLE001
                pass
            # 无头模式 UA 里带 "HeadlessChrome"，Cloudflare 一眼识破 —— 必须换掉
            try:
                raw_ua = cdp.evaluate("navigator.userAgent") or ""
                clean_ua = raw_ua.replace("HeadlessChrome/", "Chrome/")
                if clean_ua and clean_ua != raw_ua:
                    cdp.call(
                        "Emulation.setUserAgentOverride",
                        {
                            "userAgent": clean_ua,
                            "acceptLanguage": "zh-CN,zh;q=0.9,en;q=0.8",
                            "platform": "Win32",
                        },
                        timeout=15,
                    )
            except Exception:  # noqa: BLE001
                pass

            html = ""
            start = time.time()
            deadline = start + challenge_timeout
            # t=0 先导航；如果挑战卡住不动，过了一半时间再刷新一次
            # （有时第一次请求只发 tk 不跳转）。
            pending: list[tuple[float, str]] = [
                (0.0, "navigate"),
                (challenge_timeout * 0.55, "reload"),
            ]
            while time.time() < deadline:
                elapsed = time.time() - start
                while pending and elapsed >= pending[0][0]:
                    _, action = pending.pop(0)
                    try:
                        if action == "navigate":
                            cdp.call("Page.navigate", {"url": url}, timeout=30)
                        else:
                            cdp.call("Page.reload", timeout=20)
                            diag.append("挑战卡住 → 刷新一次")
                    except Exception as exc:  # noqa: BLE001
                        diag.append(f"{action} failed: {type(exc).__name__}: {exc}")
                time.sleep(1.2)
                try:
                    state = cdp.evaluate(
                        "document.readyState + '\\u0000' + document.documentElement.outerHTML.length"
                    )
                except Exception:  # noqa: BLE001 - 导航中执行上下文会短暂失效
                    continue
                if not state:
                    continue
                try:
                    html = cdp.evaluate("document.documentElement.outerHTML") or ""
                except Exception:  # noqa: BLE001
                    continue
                if html and not looks_blocked(html):
                    break
            cookies = []
            try:
                raw = cdp.call("Network.getCookies", {"urls": [url]}, timeout=15)
                cookies = raw.get("cookies") or []
            except Exception:  # noqa: BLE001
                pass
            try:
                ua = cdp.evaluate("navigator.userAgent")
            except Exception:  # noqa: BLE001
                ua = None
            final_url = None
            try:
                final_url = cdp.evaluate("location.href")
            except Exception:  # noqa: BLE001
                pass
            if looks_blocked(html or ""):
                # 失败时把现场信息留下来，省得反复猜
                for probe_expr, label in (
                    ("document.title", "title"),
                    ("document.readyState", "readyState"),
                    ("String(navigator.webdriver)", "webdriver"),
                    ("String(navigator.plugins.length)", "plugins"),
                    ("String(Boolean(window.chrome))", "window.chrome"),
                    ("String(document.visibilityState)", "visibility"),
                    ("String(document.scripts.length)", "scripts"),
                ):
                    try:
                        diag.append(f"{label}={cdp.evaluate(probe_expr)}")
                    except Exception:  # noqa: BLE001
                        pass
                diag.append(f"html_len={len(html or '')}")
        finally:
            cdp.close()
    blocked = (not html) or looks_blocked(html)
    header = "; ".join(f"{c.get('name')}={c.get('value')}" for c in cookies if c.get("name"))
    return {
        "html": None if blocked else html,
        "blocked": blocked,
        "cookies": cookies,
        "cookie_header": header,
        "user_agent": ua,
        "url": final_url or url,
        "raw_html": html,
        "diag": diag,
    }
