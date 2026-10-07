"""javarchive.com 影片元数据插件 (amane)。

javarchive.com 是面向中文/日文用户的 FC2 索引站：每篇文章对应一部影片，
带封面图、标题、剧照（不是每篇都有）与分类。本插件把它作为 javdb / fc2ppvdb /
fc2 / freejavbt 全部失手后的「封面 + 标题 + 剧照 + 发布日期」兜底源。


v0.6.0 —— 老文章（2017 年前后）刮不到封面
------------------------------------------

用户报告 FC2-597145 刮不到封面。翻 debug 目录里 212 篇真实文章页做统计，
发现**站点有两套文章模板**，v0.5.0 只认了新的那套：

* **新文章**：封面在 ``div.fisrst_sc img``（站点把 ``first`` 拼错了），
  图床是 ``img.javstore.net``，命名 ``{番号}pl.jpg`` / ``ps.jpg``。
* **老文章**（postid 小、多为 2017 年前后）：**根本没有 ``div.fisrst_sc`` 这个
  容器**，封面在 ``div.Recipepod img[itemprop='image']``，图床是
  ``img3.javarchive.com``，命名 ``FC2PPV{番号}.jpg``。

实测覆盖率（212 篇真实样本）：

    div.fisrst_sc img                        175/212   有值
    div.Recipepod img[itemprop='image']      212/212   有值  ← 两套模板通吃

``div.Recipepod`` 那个滥用 schema.org/Recipe 的容器，v0.3.0 就注意到它"没有
可用信息"，其实它的 ``itemprop="image"`` **恰恰是唯一跨模板稳定的封面位置**。
所以 v0.6.0 把它加进 ``cover_selector`` 兜底链的**最后一级**（前三级仍是
``fisrst_sc`` 大图优先——老模板的 ``ps`` 图偏小）。

顺带否掉一个看似合理的优化：**不能要求封面 URL 必须含番号**。统计里有 18 篇
封面的文件名是时间戳 / 哈希（``1646037558.9.gif``、``3eC2ebcE64Fc7174pl.jpg``），
不含番号但**确实是正确封面**；加这条过滤会误杀 17 篇，只换来修好 1 篇
（555173 那篇站点自己存错了图，属站点数据问题，插件侧无解）。

另注：老模板文章同样没有剧照区和正文「日期：」行，所以这类番号只有
标题 + 封面 + 面包屑分类，属站点本身的数据缺失。

同一轮统计还暴露了第二个问题：**``.gif`` 被无条件拉黑**。212 篇里有 7 篇的
封面**本身就是 GIF 动图**（``1423962pl.gif``、``FC2PPV-3260300.gif``），
一律拒绝等于这 7 篇永远没封面。而当初拉黑 GIF 想挡的装饰动图
（``/assets/top-view-3.gif``）其实已被 ``_BAD_IMAGE_MARKERS`` 的 ``/assets/``
与 ``top-view`` 挡掉，根本不需要靠扩展名来拦。所以 v0.6.0 把规则改成
**先过 markers，再看 GIF 是否在图床 ``/images/`` 目录下**，并加
``cover_allow_gif`` 开关（默认开）。

两项合计，212 篇真实样本的封面命中率 **79.2% → 100%**。


v0.5.0 —— javstore 原图 404 自动复活
------------------------------------

v0.4.0 还有一个隐性 bug：javstore.net 图床**部分原图会 404**（即 javarchive
文章页 HTML 仍指向那个 URL，但实际 GET/HEAD 都是 404）。用户实测案例：

    原图: https://img.javstore.net/images/2022/07/30/FC2PPV-3061625.jpg  → 404
    -2  : https://img.javstore.net/images/2022/07/30/FC2PPV-3061625-2.jpg → 200

这是 javstore 的"派生图保留、原图清理"现象（估计是上游 CDN / storage GC 触发的）。
v0.5.0 加 ``cover_javstore_resurrect`` 配置 + ``_javstore_resurrect`` 自动复活：

1. ``fetch`` 流程在 ``_parse`` 之后、构造 MediaMetadata 之前对封面 URL 做一次 HEAD。
3. 只对 ``*.javstore.net`` 域生效；已带 ``-N`` 后缀的 URL 不再套。
4. 失败时按 ``-2`` / ``-3`` 后缀再各 HEAD 一次；命中即替换原 URL；全失败保留原 URL。
5. 单次 HEAD 默认 4s 超时（``cover_resurrect_timeout``），最坏 ~12s。
6. 走 amane 的线程池（``asyncio.to_thread`` 兼容实现 ``_to_thread``），
   不阻塞事件循环。
7. 关掉 ``cover_javstore_resurrect`` 即可回到 v0.4.0 行为。


v0.4.0 —— 补齐剧照与发布日期
--------------------------------

v0.3.0 只能刮封面 + 标题，剧照字段一直空着，amane 里就没法挂剧照墙。
两个长期遗留问题顺手也补了：

1. **剧照**。javarchive 是个 XHTML 老站，剧照区**不是每篇都有**——有的是
   ``<ul class="highslide-gallery" id="lightgallery">``，每张图
   ``<li data-src="https://...fc2.com/w1280/..."><a><img src="..."></a></li>``，
   一篇 4~8 张；没有的就完全没这块。``_extract_gallery`` 优先 ``::attr(data-src)``
   （高分辨率），退到 ``img::attr(src)``，自动过 ``_is_bad_image`` 黑名单。
   结果直接写进 ``MediaMetadata.extrafanart``。如果宿主 ``MediaMetadata`` 没这个字段
   （SDK 旧版本），插件会跳过，不影响加载。

2. **发布日期**。v0.3.0 在文档里说"本站文章页没有自身发布日期"——
   这结论**只对了一半**。``span.news_date`` 是别人文章日期不能用，**但**
   文章正文（``div.news`` 的直接文本节点）里有这一行：

       标签：xxx｜yyy
       卖家：[RED]
       日期：YYYY/MM/DD
       时长：HH:MM:SS

   这些是直接文本节点 + ``<br class="a5555">`` 分隔，CSS 选不定位，只能拿
   ``div.news`` 的纯文本再正则。所以 v0.4.0 加了 ``_extract_body_meta``：
   顺手把"标签/日期/时长"三个字段都收上来，**发布日期就是 YYYY-MM-DD 准确值**，
   不像 ``release_from_cover_path`` 偏几个月。三个字段都有独立开关（默认全开）。

3. **真标签**。``news_pro_tag a`` 把标题拆成词不能当标签（v0.3.0 已说明），
   但 ``div.news`` 文本里的"标签：xxx｜yyy"是**真题材标签**（如
   ``1980pt｜ハメ撮り｜素人｜中出し｜個人撮影``）。v0.4.0 加进了 tags 列表
   （归一化后是标题子串的会被 ``_filter_tags`` 过滤掉）。


v0.3.0 —— 拿到真实页面后的重写
--------------------------------

v0.2.0 的默认选择器是**用 Wayback 快照按"通用 WordPress 主题"猜的**，
全部落空。v0.3.0 直接对线上真实 HTML 做了逐条验证，结论如下（实测记录）：

1. **站点自带可用搜索**：``GET /search?q=<番号>``，表单为
   ``<form action="/search" method="GET"><input name="q">``。
   实测 ``q=1793751`` / ``q=FC2-PPV-1793751`` / ``q=FC2PPV-1793751``
   三种写法都精确返回 2 条匹配文章；垃圾查询返回 0 条。
   **所以根本不需要搜索引擎**——v0.2.0 把搜索引擎当主路是方向性错误。
   注意它返回的是**相对链接**（``/731705-fc2-ppv-...``），必须 urljoin。

2. **不是 WordPress**，是 XHTML 1.0 老站。``h1.entry-title`` /
   ``.entry-content`` / ``.post-tags`` / ``span.entry-date`` 一个都不存在。

3. **``h1::text`` 取不到标题**：正文标题是 ``<h1><a ...>标题</a></h1>``，
   ``::text`` 只取直接子文本节点，所以返回空。必须用 ``h1 a``（或
   ``h1 ::text``）。这是最容易踩空的一处。

4. **``og:image`` 是 ``/favicon.png``**。v0.2.0 的 og 兜底会把封面设成
   站点图标。v0.3.0 加了图片黑名单（favicon / logo / banner / assets…）。

5. **``span.news_date`` 不能当发布日期**：那是侧栏"Top Views in week"里
   **别的文章**的日期，取第一个就是写入错误数据。发布日期改走
   ``div.news`` 文本里的"日期：YYYY/MM/DD"（见 v0.4.0 第 2 点）。

6. 封面在 ``div.fisrst_sc > img``，形如
   ``https://img.javstore.net/images/2021/05/17/FC2PPV-1793751.jpg``。
   图片路径里的日期**不是**配信日（实测 1793751 偏了约 3 个月），
   所以 ``release_from_cover_path`` 默认关闭。

7. 分类在面包屑里：``ul[itemtype*='Breadcrumb'] a span[itemprop='title']``
   → ``['Home', 'AV Uncensored']``。这是本站唯一干净的标签来源。


⚠️ 仍然无法解决 / 已知取舍
---------------------------

* 文章的 ``postid`` 是站点自增号（约 7 位数），**与番号没有可推导关系**，
  所以拼不出 URL，必须靠站内搜索（或 ``index.csv`` 手工喂）。
* 本站**没有演员信息**（``span[itemprop='name']`` 是作品标题，
  ``div.Recipepod`` 是滥用 schema.org/Recipe 的容器），所以
  ``metadata_fields`` 里不含 actor。
* keyword 区（``div.tag`` / ``.news_pro_tag``）是把**标题拆成词**，不是题材
  标签，所以 ``tags_selector`` 默认关闭——题材标签改由 ``div.news`` 文本提供
  （见 v0.4.0 第 3 点）。
* 站内搜索正确率很高但**不保证唯一**：同一部片可能有多篇（实测 1793751
  有两篇，一篇原帖一篇 ``[KBJ]`` 转发）。本插件取第一条。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import socket as _socket
import sys
import time
import urllib.error as _urllib_error
import urllib.request as _urllib_request
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, quote, quote_plus, unquote, urljoin, urlsplit, urlunsplit

from parsel import Selector
from pydantic import BaseModel, ConfigDict, Field

try:  # 打包版可能没收 base64：PyInstaller 只收宿主静态导入图里的标准库（上游 issue #178）
    import base64
except ImportError:  # pragma: no cover - 只有 bing 跳转壳解包会用到
    base64 = None  # type: ignore[assignment]


# ---------- 与宿主握手：取到宿主校验时真正会比对的那些类对象 ----------
#
# 为什么不直接 ``from amane.plugin import ...``：
# 宿主判定插件是否合法时，用的是 **``amane.plugins.api.FilmSourcePlugin`` 这个具体对象**
# （``amane/plugins/manager.py`` 里是 ``from .api import FilmSourcePlugin``），
# 而 ``amane.plugin`` 只是给插件作者看的再导出壳。打包版下、或机器上存在多份 amane
# 安装时，这两条路径可能解析到不同的类对象，``issubclass()`` 随之为假，宿主就报
#   plugin.py must define a FilmSourcePlugin or PlaybackPlugin subclass named Plugin
# ——即便插件代码本身完全正确。官方示例插件 ``nas.local`` 用
# ``try: amane.plugin / except ImportError: amane.plugins.api`` 正是为绕开这类差异。
#
# 这里做得更彻底：**优先**走宿主实现路径（``amane.plugins.*`` / ``amane.net.*`` /
# ``amane.crawlers.*``，manager 自己用的就是这些模块），拿不到才退回作者 SDK 路径。
# ``importlib.import_module`` 命中 ``sys.modules``，所以拿到的一定是宿主进程里正在
# 使用的那一个模块对象，而不是另一份同名副本。

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

BASE = "https://javarchive.com"


# ---------- 把同步阻塞调用推到线程池（兼容 Python 3.7+）----------


async def _to_thread(func, *args, **kwargs):
    """``asyncio.to_thread`` 是 3.9+ 才有的；这里给旧版本提供兼容实现。
    javstore 复活探测是同步 urllib HEAD，必须不阻塞 amane 事件循环。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: func(*args, **kwargs)
    )

# ---------- 番号规整 ----------

# 输入形态: "FC2-1234567" / "FC2-PPV-1234567" / "1234567" / "FC2PPV-1234567" / "fc2_ppv_1234567"
_FC2_NUMBER_RE = re.compile(r"(?i)FC2[-_ ]?(?:PPV[-_ ]?)?[-_ ]?(\d{6,8})\b")
_DIGITS_RE = re.compile(r"\b(\d{6,8})\b")


def _canonical_fc2(raw: str) -> str | None:
    """抽出数字并归一成 amane 内置 FC2 源使用的 ``FC2-PPV-NNNNNNN``（见 sites/fc2.py）。"""
    if not raw:
        return None
    m = _FC2_NUMBER_RE.search(raw) or _DIGITS_RE.search(raw)
    if not m:
        return None
    digits = m.group(1) if m.lastindex else m.group(0)
    return f"FC2-PPV-{digits}"


# 普通番号：字母前缀 + 连字符 + 数字，如 SSIS-001 / HMN-625 / MIDV-123
_STD_NUMBER_RE = re.compile(r"(?i)^([a-z]{2,6})-?(\d{2,5})$")


def _classify_number(raw: str) -> tuple[str, str, str] | None:
    """把番号分成 FC2 与普通两类。

    返回 ``(kind, display, token)``；无法识别返回 None。
    FC2 的 token 是纯数字（沿用旧逻辑）；普通番号 token 是字母数字归一形态
    （``SSIS-001`` -> ``ssis001``），用于 slug/标题匹配与缓存键。
    """
    text = (raw or "").strip()
    if not text:
        return None
    fc2 = _canonical_fc2(text)
    if fc2 is not None:
        return ("fc2", fc2, _digits(fc2))
    m = _STD_NUMBER_RE.match(text)
    if m:
        # 保留番号原始数字串（含前导零），实测 slug 为 ssis001 形态
        token = f"{m.group(1).lower()}{m.group(2)}"
        return ("std", text, token)
    return None


def _digits(number: str) -> str:
    m = _DIGITS_RE.search(number or "")
    return m.group(1) if m else ""


def _alnum(text: str) -> str:
    """小写并只留 ASCII 字母数字：``FC2-PPV-1793751`` 与 ``FC2PPV 1793751`` 都归成 ``fc2ppv1793751``。

    只用于比对 URL / 番号（本身就是 ASCII）。要比对日文文本请用 ``_squash``。
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _squash(text: str) -> str:
    """去掉标点与空白后小写，**保留日文/中文等非 ASCII 字母**（用于文本比对）。"""
    return re.sub(r"[^\w]", "", (text or "").lower(), flags=re.UNICODE)


# ---------- 搜索引擎（仅作最后兜底） ----------

_ENGINES: dict[str, str] = {
    "duckduckgo": "https://html.duckduckgo.com/html/?q={q}",
    "duckduckgo_lite": "https://lite.duckduckgo.com/lite/?q={q}",
    "mojeek": "https://www.mojeek.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}&count=30",
    "google": "https://www.google.com/search?q={q}&num=30",
    "brave": "https://search.brave.com/search?q={q}",
    "startpage": "https://www.startpage.com/sp/search?query={q}",
}

# 只用于给 amane 的 host 限速器提供主机键（声明出去就会按 rate_limit 整形）。
_SEARCH_HOST_URLS: tuple[str, ...] = (
    "https://html.duckduckgo.com",
    "https://lite.duckduckgo.com",
    "https://www.mojeek.com",
    "https://www.bing.com",
    "https://www.google.com",
    "https://search.brave.com",
    "https://www.startpage.com",
)

_ENGINE_GAP = 0.4
"""引擎之间的小停顿：每个引擎是不同 host，host 级限速器管不到跨引擎的连续请求。"""

# 风控 / 验证码特征。命中即认为"被拦住"，而不是"没结果"。
_CHALLENGE_MARKERS: tuple[str, ...] = (
    "select all squares containing",
    "/assets/anomaly/",
    "bots use duckduckgo too",
    "unfortunately, bots use",
    "verification required",
    "please complete the challenge",
    "verify you are human",
    "i'm not a robot",
    "recaptcha/api.js",
    "www.google.com/recaptcha",
    "g-recaptcha",
    "hcaptcha.com",
    "altcha",
    "cf-chl-",
    "just a moment",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
    "error 1020",
    "error 1015",
    "error 1034",
    "you have been blocked",
    "unusual traffic",
    "automated queries",
    "sorry your network appears",
    "our systems have detected",
    "captcha-delivery.com",
    "geo.captcha-delivery.com",
)


def _is_challenge(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _CHALLENGE_MARKERS)


# 明显不是封面的图片。javarchive 的文章页第一张图是站点 logo、
# 第二张是广告 banner，og:image 更是直接指向 /favicon.png。
_BAD_IMAGE_MARKERS: tuple[str, ...] = (
    "favicon",
    "logo",
    "/upload/setting/",
    "/assets/",
    "banner",
    "spacer",
    "blank.",
    "pixel",
    "/ads/",
    "top-view",
)


def _is_bad_image(url: str, allow_gif: bool = False) -> bool:
    """明显不是封面的图片。javarchive 的文章页第一张图是站点 logo、
    第二张是广告 banner，og:image 更是直接指向 /favicon.png。

    关于 GIF（v0.6.0 修正）：v0.5.0 之前**无条件拒绝 ``.gif``**，因为站点有个
    装饰动图 ``/assets/top-view-3.gif``。但实测 212 篇里有 7 篇的封面**本身就是
    GIF**（``1423962pl.gif``、``FC2PPV-3260300.gif``），一律拒绝等于这 7 篇永远
    没封面。而装饰 GIF 其实已被 ``_BAD_IMAGE_MARKERS`` 的 ``/assets/`` /
    ``top-view`` 挡掉，不需要靠扩展名来拦。

    所以规则改成：**先过 markers，再看 GIF 是否在图床目录里**
    （``/images/`` 下的 GIF 是内容图，其余位置的 GIF 视为装饰）。
    ``allow_gif=True`` 时接受图床目录下的 GIF 封面（v0.6.0 默认开）。
    """
    low = (url or "").lower()
    if not low:
        return True
    if any(marker in low for marker in _BAD_IMAGE_MARKERS):
        return True
    if low.endswith(".gif"):
        return not (allow_gif and "/images/" in low)
    return False


# 值得重试的文章页失败原因。CF 1034 是站点侧间歇性故障，等一会儿再试往往就通了。
_RETRYABLE: frozenset[FailureReason] = frozenset(
    {
        FailureReason.CLOUDFLARE_BLOCKED,
        FailureReason.CLOUDFLARE_CHALLENGE,
        FailureReason.SERVER_ERROR,
        FailureReason.TIMEOUT,
        FailureReason.EMPTY_RESPONSE,
    }
)

# 封面图存储路径里的日期，形如 /images/2021/05/17/xxx.jpg
_COVER_DATE_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/")


# ---------- 配置 ----------


class JavArchiveConfig(BaseModel):
    """用户在「管理 → 插件」里可改的旋钮。

    列表 / 映射类字段一律用字符串（逗号、分号或换行分隔），避免设置页渲染不出来。
    选择器用 CSS 语法（parsel），多条用逗号分隔、按序匹配、取首个非空结果；
    写 ``.x::text`` 形式也不必纠结——插件会按 ``::text`` → 后代文本 → ``::attr(title)``
    的顺序自己找。
    """

    model_config = ConfigDict(extra="forbid")

    # --- 文章页发现（站内搜索为主，这是本站唯一可靠入口）---
    site_search: bool = Field(
        default=True,
        title="站内搜索",
        description="用站点自带的 /search?q= 定位文章。实测精确、不会弹验证码，强烈建议保持开启。",
    )
    site_search_path: str = Field(
        default="/search",
        title="站内搜索路径",
        description="站内搜索的接口路径，接在站点基址后面。站点改版时才需要改。",
    )
    search_engines: str = Field(
        default="duckduckgo,duckduckgo_lite,mojeek,bing,google",
        title="外部搜索引擎",
        description="仅在站内搜索找不到时才用到的兜底，按书写顺序依次尝试。逗号分隔。",
    )
    query_template: str = Field(
        default="site:javarchive.com {number}",
        title="搜索引擎查询模板",
        description="外部搜索引擎的查询串：{number} 是 FC2-PPV-xxxxxxx，{digits} 是纯数字。",
    )
    site_query_template: str = Field(
        default="{digits}",
        title="站内搜索查询模板",
        description="站内搜索的查询串。实测纯数字和完整番号都能命中，纯数字结果最干净。",
    )
    searxng_base: str = Field(
        default="",
        title="SearXNG 基址",
        description="自建或公共 SearXNG 的地址（如 https://searx.example.com）。"
        "填了之后在「外部搜索引擎」里加上 searxng 即可启用，走 JSON 接口，不会弹验证码。",
    )
    extra_url_templates: str = Field(
        default="",
        title="直连 URL 模板",
        description="可选的直连地址模板，每行一条，支持 {number} / {digits}。"
        "默认留空：该站文章 URL 里的 postid 无法从番号推导，模板通常救不了场。",
    )
    index_file: str = Field(
        default="index.csv",
        title="手工索引文件",
        description="插件数据目录下的手工索引文件名，每行格式为「番号,文章链接」。"
        "搜索引擎全被拦时的最后兜底。",
    )
    cache_resolved: bool = Field(
        default=True,
        title="缓存已解析链接",
        description="命中过的「番号 → 文章链接」记进 resolved.json，下次直接复用，省一次搜索。",
    )

    # --- 外部搜索引擎出站 ---
    search_use_proxy: bool = Field(
        default=True,
        title="搜索引擎走代理",
        description="开：走 amane 的代理；关：直连。搜索引擎弹验证码多半是代理出口 IP 脏，换一边试试。",
    )
    search_cookie: str = Field(
        default="",
        title="搜索引擎 Cookie",
        description="发给搜索引擎的 Cookie，形如 k=v; k2=v2。有验证 Cookie 时可救活被拦的引擎。",
    )
    search_user_agent: str = Field(
        default="",
        title="搜索引擎 UA",
        description="覆盖搜索引擎请求的 User-Agent。留空走 amane 默认的浏览器指纹。",
    )

    # --- 命中判定 ---
    url_must_contain_number: bool = Field(
        default=True,
        title="链接必须含番号",
        description="要求文章链接里带 fc2ppv + 番号。这是最强的信号，能挡掉串台的结果，不建议关。",
    )
    title_must_contain_number: bool = Field(
        default=True,
        title="标题必须含番号",
        description="再兜一道：标题里必须出现番号，否则认为选错了页面并丢弃。",
    )

    # --- 文章页抓取 ---
    article_retries: int = Field(
        default=2,
        title="重试次数",
        description="额外的重试次数（总共重试次数 + 1 次请求）。只对 Cloudflare 拦截 / 5xx / 超时生效。",
    )
    article_retry_delay: float = Field(
        default=2.0,
        title="重试等待秒数",
        description="首次重试前的等待秒数，之后每次线性递增。",
    )

    # --- 选择器（v0.3.0 按线上真实 HTML 校准）---
    title_selector: str = Field(
        default="h1 a, h1, .first_des",
        title="标题选择器",
        description="h1 a 必须排在 h1 前面：本站标题是 <h1><a>标题</a></h1>，单独用 h1 取不到文本。",
    )
    cover_selector: str = Field(
        default=(
            "div.fisrst_sc img:first-of-type, "
            ".news .fisrst_sc img, "
            ".fisrst_sc img, "
            "div.Recipepod img[itemprop='image']"
        ),
        title="封面选择器",
        description="按序匹配取首个合格图。前三级锁定新文章的封面区 div.fisrst_sc（站点把 first 拼错了），"
        "最后一级 div.Recipepod img[itemprop='image'] 是 2017 年前后的老文章唯一有封面的位置。"
        "不要用「页面第一张图」做兜底——加了剧照区后第一张图会是剧照。",
    )
    tags_selector: str = Field(
        default="",
        title="标签选择器",
        description="次要标签来源，默认关闭。实测 .news_pro_tag a 抽出来的是标题拆成的词，不是题材标签。"
        "想收进来就填 .news_pro_tag a。",
    )
    category_selector: str = Field(
        default="ul[itemtype*='Breadcrumb'] a span[itemprop='title']",
        title="分类选择器",
        description="面包屑分类，实测得到 ['Home', 'AV Uncensored']。Home 会被自动剔除。",
    )
    release_selector: str = Field(
        default="",
        title="发布日期选择器",
        description="默认留空：本站文章页侧栏的日期全是别的文章的，抓了就是写错数据。",
    )
    release_from_cover_path: bool = Field(
        default=False,
        title="用封面路径推日期",
        description="从封面图路径推日期（/images/2021/05/17/ → 2021-05-17）。"
        "默认关：实测该日期与真实配信日相差数月，写错不如留空。",
    )
    tags_max: int = Field(
        default=20,
        title="标签上限",
        description="最多保留多少个标签。",
    )
    tags_drop_tokens: str = Field(
        default="FC2,PPV,Home",
        title="剔除的标签",
        description="要从标签里剔除的词，逗号分隔。纯数字标签总会被剔除。",
    )
    cover_allow_gif: bool = Field(
        default=True,
        title="允许 GIF 封面",
        description="约 3% 的文章封面本身就是 GIF 动图（如 1423962pl.gif）。"
        "开启后接受图床 /images/ 目录下的 GIF 作封面；站点的装饰动图和广告图仍会被挡掉。"
        "关掉则回到「GIF 一律不要」的旧行为。",
    )
    og_image_fallback: bool = Field(
        default=True,
        title="用 og:image 兜底封面",
        description="封面取不到时退化到 og:image / twitter:image，会自动跳过站点图标和 logo。",
    )
    cover_javstore_resurrect: bool = Field(
        default=True,
        title="封面 404 自动复活",
        description="javstore 部分原图被删（返回 404），加 -2 / -3 后缀可拿到另一份镜像。"
        "默认开：探测失败会保留原链接，行为不变。关掉的理由是探测最多会多花十几秒。",
    )
    cover_resurrect_timeout: float = Field(
        default=4.0,
        title="复活探测超时（秒）",
        description="单次探测的超时秒数。失败会再试 -2 / -3 两个候选，最坏情况约 3 倍超时。",
    )
    gallery_selector: str = Field(
        default="ul#lightgallery li, ul.highslide-gallery li",
        title="剧照选择器",
        description="剧照区（不是每篇文章都有）。每条优先取 data-src 高清原图，缺失时退到 img src。"
        "留空就关闭剧照提取。",
    )
    gallery_max: int = Field(
        default=24,
        title="剧照上限",
        description="单次最多收多少张剧照，防止某天站点塞几百张把数据撑爆。",
    )

    body_tags: bool = Field(
        default=True,
        title="正文提取题材标签",
        description="从正文纯文本里抽「标签：xxx｜yyy」这一行。这是本站唯一真正的题材标签区，"
        "关掉就只剩面包屑分类。",
    )
    body_release: bool = Field(
        default=True,
        title="正文提取发布日期",
        description="从正文文本里抽「日期：YYYY/MM/DD」。优先级高于「用封面路径推日期」，"
        "这是卖家留的真实日期。",
    )
    body_runtime: bool = Field(
        default=True,
        title="正文提取时长",
        description="从正文文本里抽「时长：HH:MM:SS」并换算成秒。"
        "仅当 amane 支持该字段时才写入，不会让旧版本报错。",
    )

    # --- 诊断 ---
    debug_dump: bool = Field(
        default=True,
        title="写调试日志",
        description="把每一步写进插件数据目录下的 debug/debug.log —— 排查问题时先看这个文件。",
    )
    debug_save_pages: bool = Field(
        default=True,
        title="保存抓到的页面",
        description="把抓到的搜索结果页 / 文章页落盘成 HTML（按番号命名，重复运行会覆盖）。",
    )
    report_misses: bool = Field(
        default=True,
        title="找不到时报错",
        description="找不到时抛出带各来源失败原因的错误，而不是静默返回空。稳定后可关掉减少日志噪音。",
    )


# ---------- 提供器 ----------


class JavArchiveProvider(FilmSourceProvider):
    """本地索引 → URL 模板 → 站内搜索 → 外部搜索引擎 → 文章页选择器解析。"""

    def __init__(self, context: PluginContext, config: JavArchiveConfig) -> None:
        self._http = context.http_client
        self._web: WebClient = context.web_client
        self._config = config
        self._data_dir = Path(context.data_dir)
        self._debug_dir = self._data_dir / "debug"
        self._search_cookies = _parse_pairs(config.search_cookie)

    # ---------- 主流程 ----------

    async def fetch(
        self,
        query: SearchQuery,
        options: FetchOptions | None = None,
    ) -> MediaMetadata | None:
        raw = query.number or ""
        classified = _classify_number(raw)
        if classified is None:
            self._log(f"跳过：无法从 {raw!r} 解析番号")
            return None
        kind, number, token = classified
        hint = _file_suffix_hint(getattr(query, "file_path", None))
        content_type = getattr(query, "content_type", None)
        self._log(
            f"=== 开始 {number} kind={kind} token={token} "
            f"route={content_type} hint={hint}"
        )

        url, text, notes = await self._resolve(number, token, content_type, hint)
        if not url:
            return self._miss(number, notes)
        self._log(f"文章页 = {url}")

        if text is None:
            text = await self._fetch_article(url, token)  # 失败会抛 SourceError
        if not text:
            return self._miss(number, [*notes, f"文章页空响应：{url}"])

        meta = self._parse(kind, number, token, url, text)
        if meta is None:
            return self._miss(number, [*notes, f"文章页解析不出标题或标题不含番号：{url}"])

        # v0.5.0：javstore 部分原图 404，加 -2/-3 后缀可复活（FC2-3061625 等）。
        if meta.poster_urls and self._config.cover_javstore_resurrect:
            new_cover = await self._resurrect_cover(meta.poster_urls[0])
            if new_cover != meta.poster_urls[0]:
                meta.poster_urls[0] = new_cover
                meta.thumb_urls[0] = new_cover

        self._remember(token, url)
        self._log(f"命中：title={meta.title!r} cover={'有' if meta.poster_urls else '无'}")
        return meta

    async def _resurrect_cover(self, cover: str) -> str:
        """javstore 复活探测：同步 HEAD 在线程池里跑，不阻塞 amane 事件循环。"""
        timeout = self._config.cover_resurrect_timeout
        return await _to_thread(_javstore_resurrect, cover, log=self._log, timeout=timeout)

    def _miss(self, number: str, notes: list[str]) -> MediaMetadata | None:
        detail = "; ".join(notes) if notes else "没有任何可用来源"
        self._log(f"未命中 {number}：{detail}")
        if self._config.report_misses:
            raise SourceError(FailureReason.NO_USABLE_METADATA, detail=detail)
        return None

    # ---------- 定位文章页 ----------

    async def _resolve(
        self, number: str, token: str, content_type: str | None, hint: str | None
    ) -> tuple[str | None, str | None, list[str]]:
        """返回 (URL, 已抓好的正文或 None, 过程记录)。"""
        notes: list[str] = []
        kind = "fc2" if number.startswith("FC2-PPV-") else "std"

        known = self._load_known()
        if token in known:
            self._log(f"本地索引命中：{known[token]}")
            return known[token], None, notes

        templates = self._templates()
        for tpl in templates:
            candidate = _enc_url(tpl.format(number=number, digits=token))
            text = await self._probe(candidate, token)
            if text:
                self._log(f"URL 模板命中：{candidate}")
                return candidate, text, notes
        if templates:
            notes.append(f"URL 模板 {len(templates)} 条全部未命中")

        if self._config.site_search:
            pick = await self._site_search(kind, number, token, content_type, hint, notes=notes)
            if pick:
                return pick, None, notes

        pick = await self._external_search(kind, number, token, content_type, hint, notes)
        if pick:
            return pick, None, notes

        return None, None, notes

    async def _site_search(
        self, kind: str, number: str, token: str,
        content_type: str | None, hint: str | None, notes: list[str],
    ) -> str | None:
        """站点自带搜索：``GET /search?q=<番号>``。

        实测这是本站唯一可靠的定位方式，且返回**相对链接**，必须 urljoin。
        注意每个页面都会带一块「最新文章」，所以必须靠 ``_pick`` 的
        番号slug匹配来筛，不能"有链接就当结果"。
        """
        path = self._config.site_search_path or "/search"
        display = number
        for q in _parse_list_q(self._config.site_query_template, kind, display, token):
            url = f"{BASE}{path}?q={quote_plus(q)}"
            try:
                text = await self._http.get_html(url)
            except SourceError as exc:
                status = f" HTTP {exc.http_status}" if exc.http_status else ""
                notes.append(f"站内搜索：{exc.reason.value}{status}")
                self._log(f"站内搜索不可用：{exc.reason.value}{status}")
                return None
            if not text:
                notes.append("站内搜索：空响应")
                return None
            if _is_challenge(text):
                notes.append("站内搜索：返回的是验证码/风控页")
                self._log("站内搜索返回风控页")
                return None
            if self._config.debug_save_pages:
                self._dump_page(f"{token}.search.site", text)
            links = _jam_links(text, base=f"{BASE}/")
            self._log(f"站内搜索 q={q!r} -> {len(links)} 条文章链接")
            pick = _pick(
                links, kind, token,
                self._config.url_must_contain_number, content_type, hint,
            )
            if pick:
                self._log(f"站内搜索命中：{pick}")
                return pick
            notes.append(f"站内搜索 q={q!r}：0 条匹配")
        return None

    async def _external_search(
        self, kind: str, number: str, token: str,
        content_type: str | None, hint: str | None, notes: list[str],
    ) -> str | None:
        engines = _parse_list(self._config.search_engines)
        queries = self._queries(number, token, kind)
        for engine in engines:
            if engine == "searxng":
                if not self._config.searxng_base.strip():
                    notes.append("searxng：未配置 searxng_base")
                    continue
                links, err = await self._searxng(queries[0])
                if err:
                    notes.append(f"searxng：{err}")
                    self._log(f"searxng -> {err}")
                    continue
                self._log(f"searxng q={queries[0]!r} -> {len(links)} 条链接")
                pick = _pick(
                    links, kind, token,
                    self._config.url_must_contain_number, content_type, hint,
                )
                if pick:
                    return pick
                notes.append("searxng：无结果")
                continue

            if engine not in _ENGINES:
                notes.append(f"{engine}：未知引擎（跳过）")
                continue

            blocked = False
            for q in queries:
                search_url = _ENGINES[engine].format(q=quote_plus(q))
                text, err = await self._search_request(search_url)
                if err:
                    notes.append(f"{engine}：{err}")
                    self._log(f"{engine} 不可用：{err}")
                    blocked = True
                    break
                links = _jam_links(text, base=f"{BASE}/")
                self._log(f"{engine} q={q!r} -> {len(links)} 条文章链接 {links[:2]}")
                if self._config.debug_save_pages:
                    self._dump_page(f"{token}.search.{engine}", text)
                pick = _pick(
                    links, kind, token,
                    self._config.url_must_contain_number, content_type, hint,
                )
                if pick:
                    self._log(f"{engine} 命中：{pick}")
                    return pick
            if not blocked:
                notes.append(f"{engine}：无结果")
            await asyncio.sleep(_ENGINE_GAP)
        return None

    def _queries(self, number: str, token: str, kind: str) -> list[str]:
        tpl = self._config.query_template or "site:javarchive.com {number}"
        values = (number,) if kind == "std" else (number, token)
        out: list[str] = []
        for value in values:
            try:
                q = tpl.format(number=value, digits=token, token=token)
            except (KeyError, IndexError, ValueError):
                q = tpl
            if q and q not in out:
                out.append(q)
        return out

    def _templates(self) -> list[str]:
        return [t for t in _parse_list(self._config.extra_url_templates) if "{" in t]

    async def _probe(self, url: str, token: str) -> str | None:
        try:
            text = await self._http.get_html(url)
        except SourceError as exc:
            self._log(f"模板候选 {url} -> {exc.reason.value}")
            return None
        if text and token in text:
            return text
        return None

    # ---------- 外部搜索引擎请求 ----------

    async def _search_request(self, url: str) -> tuple[str | None, str | None]:
        """返回 (正文, 失败说明)。正文为 None 表示这条引擎这条路走不通。"""
        headers = (
            {"User-Agent": self._config.search_user_agent.strip()}
            if self._config.search_user_agent.strip()
            else None
        )
        cookies = self._search_cookies or None
        try:
            if self._config.search_use_proxy:
                text = await self._http.get_html(url, headers=headers, cookies=cookies)
            else:
                # 直连：http_client 不透传 use_proxy，只能走底层 web_client。
                # 仍然是 amane 的同一个客户端，所以限速与记录照旧。
                response = await self._web.request(
                    "GET", url, headers=headers, cookies=cookies, use_proxy=False
                )
                text = response.text
        except RequestError as exc:
            body = ""
            failure = getattr(exc, "failure", None)
            if failure is not None and failure.body:
                body = failure.body.decode("utf-8", errors="replace")
            if body and _is_challenge(body):
                return None, f"被验证码/风控拦截（HTTP {exc.http_status}）"
            return None, f"HTTP {exc.http_status}：{exc.detail or '请求失败'}"
        except SourceError as exc:
            suffix = f" HTTP {exc.http_status}" if exc.http_status else ""
            return None, f"被拦截（{exc.reason.value}）{suffix}"
        if not text:
            return None, "空响应"
        if _is_challenge(text):
            return None, "返回的是验证码/风控页"
        return text, None

    async def _searxng(self, query: str) -> tuple[list[str], str | None]:
        base = self._config.searxng_base.rstrip("/")
        url = f"{base}/search?q={quote_plus(query)}&format=json"
        try:
            payload = await self._http.get_json(url)
        except SourceError as exc:
            return [], f"HTTP {exc.http_status}（{exc.reason.value}）"
        except Exception as exc:  # noqa: BLE001 - JSON 解析失败等，一律算这条路走不通
            return [], f"{type(exc).__name__}: {exc}"
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return [], "返回不是 SearXNG JSON（实例可能禁用了 format=json）"
        links = [
            str(item["url"])
            for item in results
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        ]
        return links, None

    # ---------- 文章页 ----------

    async def _fetch_article(self, url: str, token: str) -> str | None:
        attempts = max(1, self._config.article_retries + 1)
        last: SourceError | None = None
        for index in range(attempts):
            try:
                text = await self._http.get_html(_enc_url(url))
            except SourceError as exc:
                last = exc
                if index < attempts - 1 and exc.reason in _RETRYABLE:
                    delay = self._config.article_retry_delay * (index + 1)
                    self._log(
                        f"文章页第 {index + 1} 次失败（{exc.reason.value}），{delay:.1f}s 后重试"
                    )
                    await asyncio.sleep(delay)
                    continue
                self._log(f"文章页抓取失败：{exc.reason.value} {exc.detail or ''}")
                raise
            if self._config.debug_save_pages:
                self._dump_page(f"{token}.article", text)
            return text
        if last is not None:
            raise last
        return None

    def _title_ok(self, title: str, kind: str, token: str) -> bool:
        """标题中是否出现本番号。复用 slug 的边界判定。

        标题经 ``_alnum`` 归一（只留 ASCII 字母数字），再用 needle 在归一台
        标题里查找。找打后按 kind 决定是否做「两侧不得黏数字」的边界拒绝：
        - fc2：只要完整 needle（``fc2ppv<数字>``）出现即视为命中，**不做**
          前/后数字拒绝。真实 FC2 标题番号后极常见紧跟年龄等数字
          （``FC2 PPV 597145 【個人撮影】18歳`` 归一为 ...59714518...），
          拒绝会误丢弃真实文章；且 needle 已含 ``fc2`` 前缀，本就很针对，
          短数字不会单靠裸数字去撞无关年龄。
        - std（普通番号）：保留 before/after 的数字拒绝——字母前缀+短数字
          需要挡 ``SSIS-001`` vs ``SSIS-010``/``SSIS-0011``，普通番号标题连云
          号后紧跟别的数字属漏判会写错片，宁严勿松。与 before 侧一律数字拒绝
          不同，after 侧只挡「数字仍黏数字或已是串尾」（更长数字串的一部分），
          after 数字后紧跟字母（如 ``SSIS-001 4K`` 的 4、``1080p`` 的 1）则视为
          命中放行，避免把画质词开头的数字误当长番号漏判。
        """
        normalized = _alnum(title)
        needle = f"fc2ppv{token}" if kind == "fc2" else token
        start = 0
        while True:
            i = normalized.find(needle, start)
            if i < 0:
                # 普通番号允许 "SSIS 001" 这种字母数字间仍有空格的形态：
                # needle 已无分隔，归一标题也无分隔，此分支即真未命中
                return False
            if kind == "fc2":
                return True
            before = normalized[i - 1] if i > 0 else ""
            after = normalized[i + len(needle)] if i + len(needle) < len(normalized) else ""
            after2 = (
                normalized[i + len(needle) + 1]
                if i + len(needle) + 1 < len(normalized)
                else ""
            )
            # 数字拒绝：before 一律拒；after 仅当「仍黏数字或已是串尾」才拒
            # （数字是更长数字串的一部分）。若 after 数字后紧跟字母（4K/1080p
            # 画质词的起始数字），视为命中放行。
            if before.isdigit() or (after.isdigit() and (after2 == "" or after2.isdigit())):
                start = i + 1
                continue
            return True

    def _parse(self, kind: str, number: str, token: str, url: str, text: str) -> MediaMetadata | None:
        html = Selector(text=text)
        self._log_selectors(html)

        title = _first_text(html, self._config.title_selector)
        if not title:
            return None
        if self._config.title_must_contain_number and not self._title_ok(title, kind, token):
            self._log(f"标题不含番号，丢弃：{title!r}")
            return None

        allow_gif = self._config.cover_allow_gif
        cover = _first_image(
            html, self._config.cover_selector, allow_gif=allow_gif
        )
        if not cover and self._config.og_image_fallback:
            cover = _first_image(
                html,
                "meta[property='og:image'], meta[name='twitter:image']",
                attr="content",
                allow_gif=allow_gif,
            )
        if cover:
            cover = _normalize_image(cover)

        # 剧照（v0.4.0 新增）：lightgallery 不是每篇都有，没有就空列表。
        gallery = _extract_gallery(html, self._config.gallery_selector, self._config.gallery_max)
        self._log(f"剧照：{len(gallery)} 张")

        # 正文文本里抽"标签 / 日期 / 时长"（v0.4.0 新增，**优先级高于**旧 release_selector）
        body_meta = _extract_body_meta(html)
        if body_meta:
            self._log(
                f"正文 meta：tags={len(body_meta.get('tags', []))} "
                f"release={body_meta.get('release')!r} runtime={body_meta.get('runtime_seconds')}"
            )

        tags = _unique(
            [
                *_all_text(html, self._config.category_selector),
                *(
                    body_meta.get("tags", [])
                    if self._config.body_tags and "tags" in body_meta
                    else []
                ),
                *_all_text(html, self._config.tags_selector),
            ]
        )
        drop = {t.lower() for t in _parse_list(self._config.tags_drop_tokens)}
        tags = _filter_tags(
            [t for t in tags if t.lower() not in drop and not t.isdigit()], title
        )[: max(0, self._config.tags_max)]

        # 发布日期优先级：正文（最准）→ release_selector → 封面路径
        release: str | None = None
        if self._config.body_release:
            release = body_meta.get("release")
        if not release:
            release = _release(html, self._config.release_selector, cover, self._config)

        fields: dict[str, object] = {
            "number": number,
            "title": title.strip(),
            "tags": tags,
            "poster_urls": [cover] if cover else [],
            "thumb_urls": [cover] if cover else [],
            "release": release,
            "source_url": url,
            "external_id": number,
        }
        # 剧照：宿主 MediaMetadata 有 extrafanart 字段才传，避免 SDK 旧版本崩
        if gallery and "extrafanart" in _metadata_field_names():
            fields["extrafanart"] = gallery
        # runtime 字段名兼容：runtime / duration 都认
        if (
            self._config.body_runtime
            and "runtime_seconds" in body_meta
            and "runtime" in _metadata_field_names()
        ):
            fields["runtime"] = body_meta["runtime_seconds"]

        meta = MediaMetadata(**fields)
        self._log(
            f"命中：title={meta.title!r} cover={'有' if meta.poster_urls else '无'} "
            f"剧照={len(gallery)} release={getattr(meta, 'release', None)!r}"
        )
        return meta

    # ---------- 本地索引 ----------

    def _index_path(self) -> Path:
        # 只取文件名，防止配置里写出 ..\.. 之类的路径写到数据目录之外
        name = Path(self._config.index_file or "index.csv").name
        return self._data_dir / name

    def _cache_path(self) -> Path:
        return self._data_dir / "resolved.json"

    def _load_known(self) -> dict[str, str]:
        out: dict[str, str] = {}
        index = self._index_path()
        if index.exists():
            try:
                for line in index.read_text("utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = [p for p in re.split(r"[,\t ]+", line) if p]
                    if len(parts) < 2:
                        continue
                    key, url = parts[0], parts[1]
                    if not url.startswith("http"):
                        continue
                    classified = _classify_number(key) or _classify_number(url)
                    if classified:
                        out[classified[2]] = url
            except OSError as exc:
                self._log(f"index 读取失败：{exc}")
        cache = self._cache_path()
        if self._config.cache_resolved and cache.exists():
            try:
                data = json.loads(cache.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                self._log(f"resolved.json 读取失败：{exc}")
            else:
                if isinstance(data, dict):
                    out.update(
                        {
                            k: v
                            for k, v in data.items()
                            if isinstance(k, str) and isinstance(v, str) and v.startswith("http")
                        }
                    )
        return out

    def _remember(self, digits: str, url: str) -> None:
        if not self._config.cache_resolved:
            return
        path = self._cache_path()
        data: dict[str, str] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text("utf-8"))
                if isinstance(loaded, dict):
                    data = {str(k): str(v) for k, v in loaded.items()}
            except (OSError, ValueError):
                data = {}
        if data.get(digits) == url:
            return
        data[digits] = url
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
            tmp.replace(path)
        except OSError as exc:
            self._log(f"resolved.json 写入失败：{exc}")

    # ---------- 诊断 ----------

    def _log(self, message: str) -> None:
        if not self._config.debug_dump:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with (self._debug_dir / "debug.log").open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {message}\n")
        except OSError:
            pass

    def _dump_page(self, name: str, text: str) -> None:
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            (self._debug_dir / f"{name}.html").write_text(
                text[:2_000_000], "utf-8", errors="replace"
            )
        except OSError:
            pass

    def _log_selectors(self, html: Selector) -> None:
        if not self._config.debug_dump:
            return
        for label, selector in (
            ("title", self._config.title_selector),
            ("cover", self._config.cover_selector),
            ("gallery", self._config.gallery_selector),
            ("tags", self._config.tags_selector),
            ("category", self._config.category_selector),
        ):
            counts = []
            for part in _split_selectors(selector):
                try:
                    counts.append(f"{part}={len(html.css(_bare(part)))}")
                except Exception as exc:  # noqa: BLE001 - 非法选择器不该拖垮整次抓取
                    counts.append(f"{part}=非法({type(exc).__name__})")
            self._log(f"选择器 {label}: " + ", ".join(counts))


# ---------- 通用小工具 ----------


def _enc_url(url: str) -> str:
    """补全非 ASCII 字符的百分号编码（已编码的 ``%XX`` 不会被二次编码）。

    本站文章 slug 里含日文与全角符号，直接丢给 HTTP 客户端可能报编码错。
    """
    parts = urlsplit(url)
    if not parts.scheme:
        return url
    path = quote(parts.path, safe="/%:@!$&'()*+,;=~[]")
    query = quote(parts.query, safe="=&%?/:@!$'()*+,;+=~[]")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def _parse_list_q(
    template: str, kind: str, display: str, token: str
) -> list[str]:
    """站内搜索查询串，去重保序。
    FC2：纯数字优先（旧行为）；普通番号：完整番号优先（实测精确），
    token 归一形态兜底。模板支持 ``{number}`` / ``{digits}`` / ``{token}``。
    """
    tpl = (template or "").strip()
    if kind == "fc2":
        values = (token, f"FC2-PPV-{token}")
    else:
        values = (display, token)
    out: list[str] = []
    for value in values:
        try:
            q = (tpl or "{number}").format(
                number=value, digits=token, token=token, value=value
            )
        except (KeyError, IndexError, ValueError):
            q = tpl or value
        q = q.strip()
        if q and q not in out:
            out.append(q)
    if not out:
        out.append(token)
    return out


def _parse_pairs(raw: str) -> dict[str, str]:
    """``k=v; k2=v2`` / 换行分隔 → dict，用于 Cookie。"""
    out: dict[str, str] = {}
    for part in re.split(r"[;\n]", raw or ""):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if key:
            out[key] = value
    return out


def _parse_list(raw: str) -> list[str]:
    """逗号 / 分号 / 换行分隔的字符串 → 去重后的列表（保序）。"""
    out: list[str] = []
    for part in re.split(r"[,;\n]", raw or ""):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def _unwrap(href: str) -> str | None:
    """把各引擎的跳转壳剥成真实 URL（DDG 的 uddg、Google 的 /url、Bing 的 base64 u）。"""
    target = (href or "").strip()
    if not target:
        return None
    if target.startswith("//"):
        target = "https:" + target
    if "uddg=" in target:
        value = parse_qs(urlparse_query(target)).get("uddg", [None])[0]
        return unquote(value) if value else None
    if "bing.com/ck/a" in target:
        if base64 is None:  # 打包版缺 base64：放弃 bing 的跳转壳解包，其余引擎不受影响
            return None
        value = parse_qs(urlparse_query(target)).get("u", [None])[0]
        if value and value.startswith("a1"):
            payload = value[2:]
            try:
                decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            except Exception:  # noqa: BLE001
                return None
            return decoded.decode("utf-8", errors="replace")
        return None
    path = urlsplit(target).path
    if path.endswith("/url") or path == "/url":
        value = parse_qs(urlparse_query(target)).get("q", [None])[0]
        return value if value and value.startswith("http") else None
    return target if target.startswith("http") else None


def urlparse_query(url: str) -> str:
    return urlsplit(url).query


def _jam_links(text: str, base: str | None = None) -> list[str]:
    """抽出所有指向 javarchive.com 的文章链接（相对链接用 ``base`` 补全，去重保序）。"""
    out: list[str] = []
    seen: set[str] = set()
    for href in Selector(text=text).css("a::attr(href)").getall():
        url = _unwrap(href)
        if not url and base and href.strip().startswith("/"):
            url = urljoin(base, href.strip())
        if not url or url in seen:
            continue
        host = urlsplit(url).netloc.lower()
        if host == "javarchive.com" or host.endswith(".javarchive.com"):
            seen.add(url)
            out.append(url)
    return out


_JUNK_PATH_PARTS = (
    "/category/",
    "/tag/",
    "/page/",
    "/author/",
    "/search",
    "/archives",
    "/archive/",
    "/feed",
    "/comment",
    "/wp-",
)

# 本站文章页统一以 -pn.html 结尾；用它当第一道过滤，能把分类/归档页全挡掉。
_ARTICLE_RE = re.compile(r"-pn\.html$", re.I)


def _is_junk(url: str) -> bool:
    """过滤首页 / 分类 / 标签 / 归档 / 分页这类不会单部介绍影片的页面。"""
    path = (urlsplit(url).path or "").lower()
    if path in {"", "/"}:
        return True
    if not _ARTICLE_RE.search(path):
        return True
    return any(part in path for part in _JUNK_PATH_PARTS)


# slug 片段 -> 版本标记。顺序即优先级无关，仅用于识别。
_VERSION_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("uncensored", ("uncensored",)),
    ("leaked", ("leaked",)),
    ("reducing", ("reducing-mosaic", "reducing_mosaic", "reducingmosaic")),
    ("fhd", ("6000kbps", "-fhd-", "fhd-")),
    ("encode", ("encode720p", "encode-", "720p", "1080p")),
    ("engsub", ("engsub",)),
    ("chsub", ("chinese-sub", "chnsub")),
    ("4k", ("-4k-", "4k-")),
    ("summary", ("-summary-", "summary-")),
)


def _version_tags(url: str) -> set[str]:
    """从文章 slug 识别版本标记：转码 / 无码流出 / モザイク破壊 / 字幕 / 4K 等。"""
    slug = _alnum(unquote(url))  # 归一后连字符消失，标记也用无连字符形态
    tags: set[str] = set()
    for tag, markers in _VERSION_MARKERS:
        if any(m.replace("-", "").replace("_", "") in slug for m in markers):
            tags.add(tag)
    if {"uncensored"} <= tags:
        tags.add("leaked")  # uncensored 流出与 leaked 同档
    return tags


# 各路由的版本优先级：位次越小越优先；未列出的标记统一 9。
# 原版位次单独由 _original_rank 按路由给（无码路由原版列末位，R5）。
_ROUTE_VERSION_ORDER: dict[str, list[str]] = {
    "censored": ["reducing", "4k", "fhd", "uncensored", "leaked", "encode", "engsub", "chsub"],
    "uncensored": ["uncensored", "leaked", "reducing", "fhd", "4k", "encode", "engsub", "chsub"],
}


def _original_rank(content_type: str | None) -> int:
    """无标记原版的位次：censored/fc2 最前(0)；uncensored 路由列末位。"""
    if (content_type or "") == "uncensored":
        return 8
    return 0


def _version_rank(url: str, content_type: str | None) -> int:
    tags = _version_tags(url)
    if not tags:
        return _original_rank(content_type)
    order = _ROUTE_VERSION_ORDER.get(content_type or "", _ROUTE_VERSION_ORDER["censored"])
    for i, marker in enumerate(order, start=1):
        if marker in tags:
            return i
    return 9


_SUFFIX_HINT_RE = re.compile(
    r"(?i)-(?P<u>U)(?![a-z0-9])"      # -U：无码版
    r"|-(?P<hint>HD|4K)(?![a-z0-9])",    # -HD / -4K：都映射到 hint "hd"
)


def _file_suffix_hint(file_path: str | None) -> str | None:
    """从文件名番号之后的后缀识别版本倾向：``-U`` 无码，``-HD``/``-4K`` 高清。

    只用于在同番号多个文章条目中提前对应版本；缺失/无后缀返回 None。
    """
    if not file_path:
        return None
    stem = Path(file_path).stem
    m = _SUFFIX_HINT_RE.search(stem)
    if not m:
        return None
    if m.group("u"):
        return "uncensored"
    if m.group("hint"):
        return "hd"
    return None


def _rank_candidates(
    links: list[str], content_type: str | None, hint: str | None
) -> list[str]:
    """按内容路由排版本；文件名后缀 hint 是用户对该片的显式选择，压过路由默认。

    仅按 rank 排序、sorted 保持同档原始顺序（key 只返回标量）。
    hint=-U 时 uncensored/leaked 置 -1；hint=-HD/-4K 时 fhd/4k/encode 置 -1。
    无目标版本时不会无结果：自然落到该路由默认顺序（原版在无码路由末位）。
    """
    def rank_of(url: str) -> int:
        rank = _version_rank(url, content_type)
        tags = _version_tags(url)
        if hint == "uncensored" and ({"uncensored", "leaked"} & tags):
            return -1
        if hint == "hd" and ({"fhd", "4k", "encode"} & tags):
            return -1
        return rank

    return sorted(links, key=rank_of)


def _token_in_slug(url: str, kind: str, token: str) -> bool:
    """文章 slug 是否含本番号。边界感知，防止短数字误配长数字。

    fc2 先找 ``fc2ppv<digits>``（最强信号）；slug 缺 fc2 前缀（如 ff
    ``ppv<digits>`` / 仅 ``<digits>``）时退到裸数字 needle 再背边界查找。
    裸数字 needle：前邻字母一律拒绝（防数字撞进单词，如 ``abc1793751``），
    但紧跟 ``ppv`` 标记（``ppv<digits>`` 形态）合法；文章 id 数字串前邻允许。
    两侧不得再黏数字（挡 ssis001 vs ssis0011、1793751 vs 17937510）。
    """
    slug = _alnum(unquote(url))

    def match(needle: str, bare: bool) -> bool:
        start = 0
        while True:
            i = slug.find(needle, start)
            if i < 0:
                return False
            before = slug[i - 1] if i > 0 else ""
            after = slug[i + len(needle)] if i + len(needle) < len(slug) else ""
            # std 的 ppv 兜底：数字前面黏着 fc2 式 ppv 前缀视为无效
            if kind == "std" and before == "v" and i >= 3 and slug[i - 3 : i] == "ppv":
                start = i + 1
                continue
            # 裸数字 needle：前邻字母拒绝，例外是紧随 ppv 标记。
            if bare and before.isalpha():
                if not (before == "v" and i >= 3 and slug[i - 3 : i] == "ppv"):
                    start = i + 1
                    continue
            if after.isdigit():
                start = i + 1
                continue
            return True

    if kind == "fc2":
        return match(f"fc2ppv{token}", bare=False) or match(token, bare=True)
    return match(token, bare=False)


def _pick(
    links: list[str],
    kind: str,
    token: str,
    require_number: bool,
    content_type: str | None = None,
    hint: str | None = None,
) -> str | None:
    candidates = [url for url in links if not _is_junk(url)]
    matched = [u for u in candidates if _token_in_slug(u, kind, token)]
    if matched:
        return _rank_candidates(matched, content_type, hint)[0]
    if require_number:
        return None
    return _rank_candidates(candidates, content_type, hint)[0] if candidates else None


_PSEUDO_RE = re.compile(r"::(?:text|attr\([^)]*\))\s*$")


def _bare(selector: str) -> str:
    """剥掉选择器末尾的 ``::text`` / ``::attr(...)``，让调用方自己决定取什么。"""
    return _PSEUDO_RE.sub("", selector.strip()).strip()


def _has_pseudo(selector: str) -> bool:
    return bool(_PSEUDO_RE.search(selector.strip()))


def _split_selectors(selector: str) -> list[str]:
    return [part.strip() for part in (selector or "").split(",") if part.strip()]


def _text_exprs(selector: str) -> list[str]:
    """把选择器展开成"取文本"的几种写法。

    ``h1 a::text`` —— 本地标题是 ``<h1><a>标题</a></h1>``，``h1::text`` 取不到，
    所以 ``::text`` 之外还要退到后代文本 ``h1 ::text`` 与 ``::attr(title)``。
    """
    if _has_pseudo(selector):
        return [selector]
    bare = _bare(selector)
    return [f"{bare}::text", f"{bare} ::text", f"{bare}::attr(title)"]


def _first_text(html: Selector, selector: str) -> str | None:
    for part in _split_selectors(selector):
        for expr in _text_exprs(part):
            try:
                value = html.css(expr).get()
            except Exception:  # noqa: BLE001
                continue
            if value and value.strip():
                return value.strip()
    return None


def _first_attr(html: Selector, selector: str, attr: str) -> str | None:
    for part in _split_selectors(selector):
        try:
            value = html.css(f"{_bare(part)}::attr({attr})").get()
        except Exception:  # noqa: BLE001
            continue
        if value and value.strip():
            return value.strip()
    return None


def _first_image(
    html: Selector, selector: str, attr: str = "src", allow_gif: bool = False
) -> str | None:
    """取第一张合格图片：跳过 favicon / logo / banner 这类非封面图。"""
    for part in _split_selectors(selector):
        bare = _bare(part)
        for expr in (f"{bare}::attr({attr})", f"{bare}::attr(src)", f"{bare}::attr(data-src)"):
            try:
                values = html.css(expr).getall()
            except Exception:  # noqa: BLE001
                continue
            for value in values:
                if value and value.strip() and not _is_bad_image(value, allow_gif=allow_gif):
                    return value.strip()
    return None


def _normalize_image(url: str) -> str:
    """协议相对 → https；已知支持 https 的图床由 http 升到 https（实测完全等价）。"""
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("http://"):
        host = urlsplit(url).netloc.lower().split(":")[0]
        if any(host == h or host.endswith("." + h) for h in _HTTPS_SAFE_HOSTS):
            url = "https://" + url[7:]
    return url


_HTTPS_SAFE_HOSTS: tuple[str, ...] = ("javstore.net",)
"""实测 http / https 返回同一张图的图床（javarchive 的封面都在这里）。"""


# ---------- javstore 原图 404 复活 ----------
#
# 用户实测：https://img.javstore.net/images/2022/07/30/FC2PPV-3061625.jpg 会 404，
# 但 https://img.javstore.net/images/2022/07/30/FC2PPV-3061625-2.jpg 能 200。
# javstore 这种"派生图保留、原图清理"现象不止 3061625 一次踩到，所以做成通用规则。
#
# 规则:
#   - 只对 ``*.javstore.net`` 域生效（FC2/DMM 等 CDN 各自命名规则不同）
#   - URL 已带 ``-N`` 后缀就不再套（避免 ``-1.jpg`` → ``-2.jpg`` 错中）
#   - 非图床扩展名（.html / .css / .js / .svg）跳过
#   - 用 stdlib HEAD 探测，单次 4s 超时；探测失败/网络异常返回原 URL
#   - 走 amane 线程池（_to_thread），不阻塞事件循环
#
# 这一段如果以后 javstore 改名或加更多可复活路径，把 ``_JAVSTORE_RESURRECT_SUFFIXES``
# 和 ``_is_javstore_url`` 改一下即可，调用方不必动。


_JAVSTORE_RESURRECT_SUFFIXES: tuple[int, ...] = (2, 3)
"""按用户实测 3061625 用 -2 复活就够，再多就属于"凑巧"；保持保守。"""

_JAVSTORE_IMAGE_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|webp|gif)$", re.I)
_JAVSTORE_SUFFIX_RE = re.compile(r"-\d{1,2}\.(?:jpg|jpeg|png|webp|gif)$", re.I)


def _is_javstore_url(url: str) -> bool:
    """判断 URL 是否在 ``*.javstore.net`` 域下。"""
    try:
        host = urlsplit(url).netloc.lower().split(":", 1)[0]
    except Exception:  # noqa: BLE001 - 任何解析失败都按非 javstore 处理
        return False
    return host == "javstore.net" or host.endswith(".javstore.net")


def _javstore_resurrect(
    url: str, *, log=None, timeout: float = 4.0, head_fn=None
) -> str:
    """javstore 原图 404 时按 ``-2`` / ``-3`` 后缀探测复活。

    实测案例（用户报告）::

            原图  : https://img.javstore.net/images/2022/07/30/FC2PPV-3061625.jpg → 404
            -2 后缀: https://img.javstore.net/images/2022/07/30/FC2PPV-3061625-2.jpg → 200

    ``head_fn`` 默认是 ``_head_status``，可在 selftest 里注入 fake。
    """
    if not url or not _is_javstore_url(url):
        return url
    if _JAVSTORE_SUFFIX_RE.search(url):
        # 已带 -N 后缀的不要再套一层
        return url
    if not _JAVSTORE_IMAGE_EXT_RE.search(url):
        return url

    head = head_fn or _head_status

    status = head(url, timeout)
    if 200 <= status < 300:
        return url
    if status == 0:
        # 网络异常/超时：不要再继续（否则 12s 全浪费）
        if log:
            log(f"封面复活探测超时/网络错误，保留原 URL: {url}")
        return url

    stem, ext = url.rsplit(".", 1)
    for n in _JAVSTORE_RESURRECT_SUFFIXES:
        candidate = f"{stem}-{n}.{ext}"
        cand_status = head(candidate, timeout)
        if 200 <= cand_status < 300:
            if log:
                log(f"封面复活：{url} ({status}) → {candidate}")
            return candidate

    if log:
        log(f"封面复活失败（原 {status}，-2/-3 都不通），保留原 URL: {url}")
    return url


def _head_status(url: str, timeout: float) -> int:
    """stdlib HEAD 探测 URL 返回码。失败/网络异常返回 0。"""
    try:
        req = _urllib_request.Request(url, method="HEAD")
        req.add_header(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        with _urllib_request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status)
    except _urllib_error.HTTPError as e:
        return int(e.code) if e.code else 0
    except (TimeoutError, _socket.timeout, OSError):
        return 0
    except Exception:  # noqa: BLE001 - 让复活探测本身永不抛
        return 0


def _extract_gallery(html: Selector, selector: str, limit: int) -> list[str]:
    """剧照：``ul#lightgallery li`` 的 ``data-src`` 优先，缺失时退到 ``img::attr(src)``。

    不是每篇文章都有剧照区：javarchive 老站没剧照的文章根本不渲染 ``<ul id="lightgallery">``，
    这种情况下返回空列表。自动过 ``_is_bad_image`` 黑名单。

    数量上限 ``limit`` 防止站点某天塞 N 张图把 metadata 撑爆。
    """
    if not selector or not selector.strip():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in _split_selectors(selector):
        bare = _bare(part)
        # data-src 优先（高分辨率原图），img@src 兜底
        for expr in (
            f"{bare}::attr(data-src)",
            f"{bare} img::attr(src)",
            f"{bare}::attr(src)",
        ):
            try:
                values = html.css(expr).getall()
            except Exception:  # noqa: BLE001
                continue
            for v in values:
                v = v.strip() if v else ""
                if not v or _is_bad_image(v) or v in seen:
                    continue
                seen.add(v)
                out.append(_normalize_image(v))
                if len(out) >= max(0, limit):
                    return out
    return out


_BODY_TAG_PREFIX_RE = re.compile(
    r"标签\s*[：:]\s*([^卖家日期时长FC2PPV\n]+?)(?=\s*(?:卖家|日期|时长|FC\d|Link|\d{4,}\s*GB|$))"
)
_BODY_RELEASE_RE = re.compile(r"日期\s*[：:]\s*(\d{4})\s*[/\-\.]\s*(\d{1,2})\s*[/\-\.]\s*(\d{1,2})")
_BODY_RUNTIME_RE = re.compile(r"时长\s*[：:]\s*(\d{1,2}):(\d{2}):(\d{2})")
_BODY_SELLER_RE = re.compile(r"卖家\s*[：:]\s*\[?([^\]\s]+)\]?")
_BODY_FILE_SIZE_RE = re.compile(r"File\s*size\s*[：:]\s*([\d.]+)\s*(GB|MB|KB)", re.IGNORECASE)


def _news_text(html: Selector) -> str:
    """``div.news`` 的纯文本（子节点文本拼起来）。这一块里藏了 标签/卖家/日期/时长 等信息。"""
    # ::text 会把后代文本都展开成扁平字符串，正好是我们要的（br 也算）
    parts = html.css("div.news ::text").getall()
    if not parts:
        parts = html.css("div.news").xpath(".//text()").getall()
    return " ".join(p.strip() for p in parts if p and p.strip())


def _extract_body_meta(html: Selector) -> dict[str, object]:
    """从 ``div.news`` 文本里抓"标签/卖家/日期/时长/文件大小"。

    这些字段在 javarchive 文章正文里是**直接文本节点** + ``<br class="a5555">``，
    CSS 选不定位，只能拿整段 text() 再正则。

    实测 4981113 模板（每篇 FC2 文章都有）：

        标签：1980pt｜ハメ撮り｜素人｜中出し｜個人撮影
        卖家：[RED]
        日期：2026/09/21
        时长：01:18:04
        File size:5.4 GB

    没匹配到的字段不会出现在返回值里，调用方按需取。
    """
    text = _news_text(html)
    if not text:
        return {}

    out: dict[str, object] = {}

    m = _BODY_TAG_PREFIX_RE.search(text)
    if m:
        raw = m.group(1).strip().rstrip("｜|、;；。")
        # 用全角｜、半角|、中文顿号、分号都拆
        tags = [t.strip() for t in re.split(r"[｜|、;;]+", raw) if t.strip()]
        if tags:
            out["tags"] = tags

    m = _BODY_RELEASE_RE.search(text)
    if m:
        y, mo, d = m.groups()
        out["release"] = f"{y}-{int(mo):02d}-{int(d):02d}"

    m = _BODY_RUNTIME_RE.search(text)
    if m:
        h, mi, s = m.groups()
        out["runtime_seconds"] = int(h) * 3600 + int(mi) * 60 + int(s)

    m = _BODY_SELLER_RE.search(text)
    if m:
        out["seller"] = m.group(1).strip()

    m = _BODY_FILE_SIZE_RE.search(text)
    if m:
        out["file_size"] = f"{m.group(1)}{m.group(2).upper()}"

    return out


_METADATA_FIELDS_CACHE: set[str] | None = None


def _metadata_field_names() -> set[str]:
    """缓存 ``MediaMetadata.model_fields`` 名字集合。SDK 旧版本可能压根不是 Pydantic BaseModel，
    缓存 ``set()`` 让上层判断兼容。
    """
    global _METADATA_FIELDS_CACHE
    if _METADATA_FIELDS_CACHE is not None:
        return _METADATA_FIELDS_CACHE
    names: set[str] = set()
    model_fields = getattr(MediaMetadata, "model_fields", None)
    if isinstance(model_fields, dict):
        names = {str(name) for name in model_fields.keys()}
    elif hasattr(MediaMetadata, "__fields__"):  # 极老的 Pydantic v1
        names = {str(name) for name in getattr(MediaMetadata, "__fields__", {}).keys()}
    _METADATA_FIELDS_CACHE = names
    return names


def _filter_tags(tags: list[str], title: str) -> list[str]:
    """剔除"其实就是标题词"的标签。

    javarchive 的 keyword 区是把标题拆成词，直接当标签用会得到一堆
    ``FC2`` / ``PPV`` / 整句标题。这里的判据是**归一化后是否为标题的子串**——
    本站的 keyword 本来就是从标题派生的，所以一旦重合就是标题词而非题材标签；
    面包屑分类（如 ``AV Uncensored``）不在标题里，不受影响。
    """
    normalized_title = _squash(title)
    out: list[str] = []
    for tag in tags:
        normalized = _squash(tag)
        if normalized and normalized in normalized_title:
            continue
        out.append(tag)
    return out


def _all_text(html: Selector, selector: str) -> list[str]:
    out: list[str] = []
    for part in _split_selectors(selector):
        for expr in _text_exprs(part):
            try:
                found = [x for x in html.css(expr).getall() if x and x.strip()]
            except Exception:  # noqa: BLE001
                continue
            if found:
                out.extend(found)
                break
    return [item.strip() for item in out if item and item.strip()]


def _release(
    html: Selector, selector: str, cover: str, config: JavArchiveConfig
) -> str | None:
    """发布日期。

    优先用户指定的选择器；``release_from_cover_path`` 打开时退化到封面图路径里的日期。
    默认两条都取不到就返回 None——javarchive 文章页本身不提供配信日。
    """
    if selector.strip():
        value = (
            _first_attr(html, selector, "datetime")
            or _first_attr(html, selector, "content")
            or _first_text(html, selector)
        )
        if value:
            return value
    if config.release_from_cover_path and cover:
        m = _COVER_DATE_RE.search(cover)
        if m:
            return "-".join(m.groups())
    return None


# ---------- 插件入口 ----------


class Plugin(FilmSourcePlugin):
    """放在 ``{数据目录}/plugins/sources/esl1.javarchive/`` 下的 plugin.py 必须导出此类。"""

    config_model: ClassVar[type[BaseModel]] = JavArchiveConfig

    @classmethod
    def descriptor(cls) -> SourceDescriptor:
        return SourceDescriptor(
            id="esl1.javarchive",
            name="javarchive.com (FC2 兜底)",
            version="0.7.0",
            capabilities=frozenset({SourceCapability.FILM_METADATA}),
            # 0.7.0：javarchive 实测收录有码片（如 SSIS-001 的原版/流出/
            # モザイク破壊/字幕多版本），故开放有码/无码两条路由；
            # 同一番号多版本按内容路由（query.content_type）选择，
            # 文件名 -U/-HD/-4K 后缀可微调。
            content_types=frozenset({"fc2", "censored", "uncensored"}),
            metadata_fields=frozenset(
                {
                    "title",
                    "tags",
                    "release",
                    "runtime",
                    "poster_urls",
                    "thumb_urls",
                    "extrafanart",
                    "source_url",
                }
            ),
            urls=("https://javarchive.com", *_SEARCH_HOST_URLS),
            rate_limit=1.0,
        )

    def build(self, context: PluginContext, config: BaseModel) -> FilmSourceProvider:
        if not isinstance(config, JavArchiveConfig):
            raise TypeError("unexpected config type")
        return JavArchiveProvider(context, config)
