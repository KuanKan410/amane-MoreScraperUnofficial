"""supfc2.com（FC2 素人片库）影片元数据插件（amane）。

站点结构（2026-09-29 用本机浏览器过 Cloudflare 后**实测**，非推断）
------------------------------------------------------------------

**① 整站在 Cloudflare Managed Challenge 后面。**
curl_cffi 任何指纹都只能拿到 ``cType: 'managed'`` 的挑战页（403），
无头 Chrome 也过不去；**只有有头 Chrome 能自动过关**。所以插件内置了一份
只用标准库写的极简 CDP 客户端：真起一个有头 Chrome（窗口立刻最小化）跑一遍
挑战 → 导出 ``cf_clearance`` + UA → 关掉浏览器，之后所有请求带着这串 cookie
走宿主的 curl_cffi。``cf_clearance`` 绑定「客户端 IP + UA」，所以 cookie 与
UA 必须成对使用（一起缓存、一起发送）。

**② 详情页可以**从番号直连**，这是本站最大的便利**（实测）::

    https://supfc2.com/detail/FC2-PPV-4973988/x      → 200 完整详情页
    https://supfc2.com/detail/FC2-PPV-4973988/aaaaa  → 200 同一页
    https://supfc2.com/detail/FC2-PPV-4973988        → 404
    https://supfc2.com/detail/4973988                → 404

也就是说：路径第二段必须是 ``FC2-PPV-<纯数字>``，第三段 slug **内容无所谓但
不能缺**（站点只拿它当 SEO 文本）。于是插件直接用番号拼 URL，**一次请求**就能
拿到全部字段，不需要搜索。不存在的番号返回 404（Laravel 的 Not Found 页）。

**③ 详情页结构**::

    <div class="player-ovl" id="ovl" style="background-image: url(https://storage202000.contents.fc2.com/...png);">
    <h1 class="product-name">标题</h1>
    <ul class="vendor-info list-style-none">
      <li class="ttt"><label>FC2&#039;s ID: </label><span class="detail">4973988</span></li>
      <li class="ttt"><label>Release Date: </label><span class="detail">2026-09-11</span></li>
      <li class="ttt"><label>Maker: </label><a href="/content/maker/18953/...">娘ガチャ</a></li>
      <li class="ttt"><label>Duration: </label><span class="detail">01:07:18</span></li>
      <li class="ttt"><label>Tag: </label><a href="/content/tag/60/...">おっぱい</a> …</li>
      <li class="ttt"><label>Genre: </label><span>UNKNOWN</span></li>
    </ul>
    <div class="mb-4">   ← Movie Description
      正文 … <a href="https://storage…-cdn.contents.fc2.com/…png" title="サンプル画像 (20).png"><img …></a>
    </div>

* **封面**：``div#ovl`` 的 ``style`` 里的 ``url(...)``。注意 ``og:image`` 有
  三个，第一个是站点自己的 logo（``/images/2supfc2.png``），已在黑名单里。
* **信息表**用 label→value 的方式读（``_info_map``），而不是给每个字段一条
  CSS 选择器：label 文本比 DOM 结构稳定。
* **剧照（サンプル画像）**只在正文里，是 ``div.mb-4`` 内指向 FC2 storage
  图片域名的 ``<a href>``。**不是每一部都有**（例如 4322094 就没有带
  ``サンプル画像`` 标题的图，4322094 的正文图 ``title="14.jpg"``）。
  顺序不能信：4973988 出图顺序是 20,28,27,25,16,23…，只有在标题里能解析出
  序号时才按序号排序。
* 有的条目正文里塞了隐藏的 base64 串（``MC5hN2Iz…``），取 plot 时要滤掉。

**④ 搜索只是兜底**：``GET /search?text=<番号>``。命中**唯一**一条时站点直接
301 跳到详情页；多条时留在列表页（``div.product-wrap`` 条目里有番号、日期、
时长、封面）。列表页解析只在直达 404 时才用得上。

已知边界
--------

* 站点只有「Maker」，没有女优字段，所以不产 actors。
* 有些番号（如 3125926 / 4551862）本站没收录 → 直达 404、搜索也搜不到，
  这是**数据缺失**不是插件问题（fc2ppv-db.com 上 3125926 被标 scrapeFailed）。
* 不存在的番号搜索会返回 **500 Server Error**（Laravel 报错页），插件按
  「没找到」处理，不抛异常。
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
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field


# ---------- 请求头 ----------
#
# Cloudflare 除了查 cookie 也看这套导航头；UA 必须与拿到 cf_clearance 时的
# 那个一致（见 _headers）。
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_BROWSER_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8,zh-CN;q=0.7",
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
# ``issubclass()`` 为假，宿主报 "plugin.py must define a FilmSourcePlugin ...
# subclass named Plugin"——即便插件代码完全正确。
#
# 解法与 supjav / fc2ppvdb / javarchive 插件一致：按优先级走
# ``importlib.import_module``，优先命中宿主自己在用的模块。

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

BASE = "https://supfc2.com"

# 详情页末段的 slug 站点完全不看，但**不能缺**（缺了就是 404）。
# 用一个固定字符串，这样 URL 可以由番号直接拼出来，省掉一次搜索请求。
DETAIL_SLUG = "x"

# 剧照 / 封面都在这个 FC2 自己的 storage 域上
IMAGE_HOST = "https://contents.fc2.com"


# ---------- 配置 ----------
#
# 所有旋钮都是标量（str/int/bool/float），宿主配置面板才能直接编辑。
# 中文标签写在 Field(title=..., description=...) 里：插件设置表单由 JSON Schema
# 渲染，插件专属字段没有内置 i18n 词条，标签取 ``schema.title``、说明取
# ``schema.description``。**字段名保持英文**（宿主的敏感字段脱敏按名字匹配
# ``token`` / ``api_key`` / ``secret``）。


class Supfc2Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # --- 基础 ---
    base_url: str = Field(
        default=BASE,
        title="站点基址",
        description="正常不用改，站长换域名时才需要。结尾不要带斜杠。",
    )
    detail_template: str = Field(
        default="{base}/detail/FC2-PPV-{digits}/" + DETAIL_SLUG,
        title="详情页模板",
        description="本站详情页可以从番号直连：路径第二段必须是 FC2-PPV-<纯数字>，"
        "末段 slug 内容无所谓但不能缺（否则 404）。{base} 是站点基址，{digits} 是番号数字。",
    )
    search_path: str = Field(
        default="/search?text={q}",
        title="站内搜索路径",
        description="只在直达 404 时才用的兜底。{q} 会替换成番号（站点自己的搜索框用 "
        "name=\"text\"，另一个表单用 name=\"q\"，两者都可用）。",
    )
    search_fallback: bool = Field(
        default=True,
        title="直达失败时改用搜索",
        description="直达 URL 返回 404（本站没收录）时，再用站内搜索找一遍，"
        "命中再进详情页。关掉可以省掉一次请求，代价是少量条目抓不到。",
    )

    # --- 解析选择器（都可改，日志会打印每个选择器的命中数）---
    title_selector: str = Field(
        default="h1.product-name::text, h1::text",
        title="标题选择器",
        description="逗号分隔、按顺序尝试。详情页正文标题是 h1.product-name。",
    )
    cover_selector: str = Field(
        default="div#ovl::attr(style), div.player-ovl::attr(style), "
        "meta[property='og:image']::attr(content)",
        title="封面选择器",
        description="封面写在播放器占位层的 background-image 里（插件会自动从 url(...) 抠出来）。"
        "注意 og:image 的第一个值是站点自己的 logo，已被黑名单挡掉。",
    )
    maker_selector: str = Field(
        default="ul.vendor-info a[href*='/content/maker/']::text",
        title="片商选择器",
        description="按 URL 路径匹配而不是 class 名：换皮时路径最不容易变。"
        "取不到时会再用信息表里的 Maker 一项。",
    )
    tags_selector: str = Field(
        default="ul.vendor-info a[href*='/content/tag/']::text",
        title="标签选择器",
        description="按 URL 路径匹配标签链接（本站 Tag 一栏里全是 /content/tag/ 链接）。",
    )
    info_selector: str = Field(
        default="ul.vendor-info li.ttt, li.ttt",
        title="信息表条目选择器",
        description="详情页那张「FC2's ID / Release Date / Maker / Duration / Tag」表。"
        "插件按 label 文本取值（Release Date → 发行日，Duration → 时长），比按 DOM 位置取稳。",
    )
    samples_selector: str = Field(
        default="#product-tab-description div.mb-4 a[href*='contents.fc2.com'], "
        "div.mb-4 a[href*='contents.fc2.com']",
        title="剧照选择器",
        description="剧照是正文里指向 FC2 storage 图片域名的链接。有的作品正文里没有图，"
        "那就是没有剧照（不是选择器错了）。",
    )
    plot_selector: str = Field(
        default="#product-tab-description div.mb-4, div.mb-4",
        title="简介选择器",
        description="「Movie Description」整块。插件会滤掉正文里隐藏的 base64 串。",
    )

    # --- 数据开关 ---
    include_tags: bool = Field(
        default=True,
        title="抓取标签",
        description="从详情页 Tag 一栏抓标签。",
    )
    include_studio: bool = Field(
        default=True,
        title="抓取片商",
        description="从详情页 Maker 一栏抓片商。",
    )
    include_plot: bool = Field(
        default=True,
        title="抓取简介",
        description="从详情页 Movie Description 抓简介正文。",
    )
    include_samples: bool = Field(
        default=True,
        title="抓取剧照",
        description="把正文里的サンプル画像放进 extrafanart（由宿主的 ResourceStore 下载）。",
    )
    drop_maker_tag: bool = Field(
        default=True,
        title="剔除与片商同名的标签",
        description="本站会把片商名也塞进标签里（むすめガチャ 之类），默认剔掉这个重复项。",
    )
    tags_max: int = Field(
        default=30,
        title="标签上限",
        description="最多保留多少个标签。",
    )
    tags_drop: str = Field(
        default="unknown,hd,video",
        title="剔除的标签",
        description="要剔除的标签，逗号分隔（大小写不敏感）。默认剔除 Genre 一栏的 UNKNOWN 占位值。",
    )
    samples_max: int = Field(
        default=30,
        title="剧照上限",
        description="最多保留多少张剧照。",
    )
    plot_max: int = Field(
        default=1200,
        title="简介字数上限",
        description="超过就截断。",
    )
    cover_allow_gif: bool = Field(
        default=True,
        title="允许 GIF 封面",
        description="允许 GIF 当封面。装饰性 GIF 已被 logo/banner 等黑名单拦掉。",
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
        description="把搜索页 / 详情页落盘到 debug 目录（按番号命名，重复运行覆盖）。",
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
# 注意：**别把 "challenge-platform" 当特征**！挑战页里有它，但正常页面也会被
# Cloudflare 塞一段 /cdn-cgi/challenge-platform/scripts/jsd/main.js（JSD 检测脚本），
# 拿它当判据会把正常页面误判成拦截页。
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
# 本站 og:image 的第一个值是 http://supfc2.com/images/2supfc2.png（站点自己的
# 分享图），所以要把站点自身 /images/ 路径也列进来。
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
    "2supfc2",
    "/images/2supfc2",
    "no-thumbnail",
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


# 正文里混着两种写法：直接指向 storage 的原图，以及
# ``//contents-thumbnail2.fc2.com/w1280/storage….contents.fc2.com/file/…png``
# （FC2 自己的缩略图服务放大到 1280）。后者缺协议头，交给宿主的
# ResourceStore 会下载失败，所以统一还原成**原图**并补全 https。
_THUMB_WRAP_RE = re.compile(
    r"^(?:https?:)?//contents-thumbnail2\.fc2\.com/w\d+/(?:https?://)?(.+)$", re.I
)


def _canonical_image_url(url: str) -> str:
    """``//host/a.png`` → ``https://host/a.png``；缩略图包装 → 原图地址。"""
    text = (url or "").strip()
    if not text:
        return ""
    m = _THUMB_WRAP_RE.match(text)
    if m:
        text = m.group(1)
    if text.startswith("//"):
        return "https:" + text
    if text and not text.lower().startswith(("http://", "https://")):
        return "https://" + text.lstrip("/")
    return text


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

    ``h1 a::text`` 这种自带伪元素的写法原样使用；否则退化成 ``::text``、
    ``后代文本``、``::attr(title)`` 三种写法——``<h1><a>标题</a></h1>``
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


_STYLE_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.I)


def _extract_style_url(value: str) -> str:
    """``background-image: url(https://x/y.jpg);`` → ``https://x/y.jpg``。

    本站封面就写在播放器占位层 ``div#ovl`` 的 ``style`` 里，所以取到
    ``::attr(style)`` 之后必须再过一道这个。
    """
    text = (value or "").strip()
    if "url(" not in text.lower():
        return text
    match = _STYLE_URL_RE.search(text)
    return match.group(1).strip() if match else ""


_COVER_ATTRS = ("data-original", "data-src", "src", "content")


def _first_image_source(sel, selector: str, allow_gif: bool = True) -> str | None:
    """逐个选择器、逐个候选属性找第一张合格图片。"""
    for part in _split_selectors(selector):
        if _has_pseudo(part):
            for value in _safe_css(sel, part):
                value = _canonical_image_url(_extract_style_url(value))
                if value and _looks_like_image(value) and not _is_bad_image(value, allow_gif):
                    return value
            continue
        base = _bare(part)
        for attr in _COVER_ATTRS:
            for value in _safe_css(sel, f"{base}::attr({attr})"):
                value = _canonical_image_url(_extract_style_url(value))
                if value and _looks_like_image(value) and not _is_bad_image(value, allow_gif):
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


# ---------- 番号 ----------

# FC2 系列番号：FC2-PPV-4981113 / FC2PPV 4981113 / FC2 4981113 / 纯数字
_FC2_RE = re.compile(r"^FC2(?:[-_\s]?PPV)?[-_\s]*(\d{4,9})$", re.I)
_DIGITS_RE = re.compile(r"^\s*(\d{4,9})\s*$")


def _fc2_digits(number: str) -> str | None:
    """``FC2-PPV-4981113`` / ``FC2 4981113`` / ``4981113`` → ``"4981113"``。

    纯数字也算：amane 的 FC2 路由虽然通常给 ``FC2-3125926``，但手工刮削、
    文件名解析都可能只给出数字。
    """
    text = (number or "").strip()
    m = _FC2_RE.match(text)
    if m:
        return m.group(1)
    m = _DIGITS_RE.match(text)
    return m.group(1) if m else None


def _canonical(digits: str) -> str:
    return f"FC2-PPV-{digits}"


def _safe_name(number: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]", "_", number or "") or "unknown"


# ---------- 日期 / 时长 ----------

_DATE_RE = re.compile(r"(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})")


def _norm_date(raw: str) -> str | None:
    m = _DATE_RE.search(raw or "")
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


_TIME_LEN_RE = re.compile(
    r"(?:runtime|duration|length|収録時間|再生時間|时长|時間)?\s*[:：]?\s*"
    r"(?:(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?|(\d{1,3})\s*(?:min|分|mins|minutes?))",
    re.I,
)


def _parse_runtime_minutes(text: str) -> int | None:
    """``01:07:18`` / ``78:04`` / ``118 min`` / ``118分`` → 分钟。

    与 supjav 版本不同：这里允许**不带** ``Duration:`` 前缀，因为调用方传进来的
    已经是信息表里那个值本身（``01:07:18``）。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    m = _TIME_LEN_RE.search(raw)
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
    else:  # MM:SS
        total = h * 60 + mi
    minutes = round(total / 60)
    return minutes if 0 < minutes < 1000 else None


# ---------- 信息表（label → 值）----------

_LABEL_KEY_RE = re.compile(r"[^a-z0-9]+")


def _label_key(label: str) -> str:
    """``Release Date:`` → ``releasedate``（信息表按它匹配，换语言也不怕）。"""
    return _LABEL_KEY_RE.sub("", (label or "").lower())


def _info_map(sel, selector: str) -> dict[str, list[str]]:
    """把详情页那张信息表读成 ``{labelkey: [值...]}``。

    本站的表是 ``<li class="ttt"><label>Release Date: </label><span class="detail">
    2026-09-11</span></li>``。按 label 文本取值比按 DOM 位置稳：换主题、插一行
    新字段都不会错位。
    """
    out: dict[str, list[str]] = {}
    for part in _split_selectors(selector):
        for node in _safe_nodes(sel, part):
            labels = [t for t in _safe_css(node, "label::text") if t.strip()]
            key = _label_key(_squash(" ".join(labels)))
            if not key:
                continue
            values: list[str] = []
            for expr in ("span.detail::text", "span::text", "a::text"):
                for value in _safe_css(node, expr):
                    value = _squash(value)
                    if value:
                        values.append(value)
            if key not in out:
                out[key] = values
            else:
                for value in values:
                    if value not in out[key]:
                        out[key].append(value)
    return out


def _safe_nodes(sel, expr: str) -> list:
    try:
        return list(sel.css(expr))
    except Exception:  # noqa: BLE001
        return []


# ---------- 剧照 ----------

_SAMPLE_INDEX_RE = re.compile(r"(\d+)")


def _sample_index(title: str) -> int | None:
    """``サンプル画像 (20).png`` → 20；``14.jpg`` → 14；没有数字返回 None。"""
    m = _SAMPLE_INDEX_RE.search((title or "").strip())
    return int(m.group(1)) if m else None


def _sample_urls(sel, selector: str, limit: int) -> list[str]:
    """正文里指向图片的链接 → 剧照列表。

    顺序不能信：实测 4973988 的出图顺序是 20,28,27,25,…,3,10,8,7（全乱的），
    所以**每项都能解析出序号时才按序号排**（sort 稳定，同序保持原序）；正文里
    混着没有 title 的图时就不能排了，保持文档顺序。

    URL 也要归一化：正文里同时有 ``//contents-thumbnail2.fc2.com/w1280/…``
    这种缺协议头的包装地址，不补全 https 宿主下载不了。
    """
    urls: list[str] = []
    index: dict[str, int | None] = {}
    for part in _split_selectors(selector):
        for node in _safe_nodes(sel, _bare(part)):
            href = _canonical_image_url(node.attrib.get("href") or "")
            if not href or not _looks_like_image(href) or _is_bad_image(href):
                continue
            if href in index:
                continue
            index[href] = _sample_index(node.attrib.get("title") or "")
            urls.append(href)
        if urls:
            break
    if len(urls) > 1 and all(index.get(u) is not None for u in urls):
        urls.sort(key=lambda u: index.get(u) or 0)
    return urls[: max(0, limit)]


# ---------- 简介 ----------

# 正文里被塞进来的隐藏 base64 串（站点防盗链的痕迹），取简介时要滤掉。
_PLOT_JUNK_RE = re.compile(r"^[A-Za-z0-9+/]{16,}={0,2}$")


def _plot_text(sel, selector: str, limit: int) -> str | None:
    for part in _split_selectors(selector):
        base = _bare(part)
        chunks: list[str] = []
        for value in _safe_css(sel, f"{base} ::text"):
            text = (value or "").strip()
            if not text or _PLOT_JUNK_RE.match(text):
                continue
            chunks.append(_squash(text))
        if chunks:
            plot = _squash(" ".join(chunks))
            return plot[:limit] if limit > 0 else plot
    return None


def _metadata_field_names() -> set[str]:
    """探测宿主 MediaMetadata 支持哪些字段（兼容 pydantic v1 / v2 与旧版 amane）。

    ``extrafanart`` 在新版 SDK 里有，老版本没有。少了字段时跳过赋值，绝不因
    缺字段而崩。
    """
    try:
        fields = getattr(MediaMetadata, "model_fields", None)  # pydantic v2
        if callable(fields) or fields is None:
            fields = getattr(MediaMetadata, "__fields__", {})  # pydantic v1
        return set(fields.keys())
    except Exception:  # noqa: BLE001
        return set()


# ---------- 提供器 ----------


class Supfc2Provider(FilmSourceProvider):
    """番号直连详情页 →（404 时）站内搜索兜底。"""

    def __init__(self, context: PluginContext, config: Supfc2Config) -> None:
        self._http = context.http_client
        self._web: WebClient = context.web_client
        self._config = config
        self._data_dir = Path(context.data_dir)
        self._debug_dir = self._data_dir / "debug"
        # Cloudflare 会话（cf_clearance + 拿它时用的 UA），内存 + 磁盘各存一份
        self._session: dict | None = None
        self._session_lock: asyncio.Lock | None = None

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
        digits = _fc2_digits(number)
        if not digits:
            self._log(f"跳过：{number} 不是 FC2 番号（本站只收录 FC2-PPV-<数字>）")
            return None
        self._log(f"=== 开始 {number} digits={digits} data_dir={self._data_dir}")

        notes: list[str] = []

        # 1) 直达详情页（本站可以由番号直接拼 URL，这是最快的路径）
        detail_url = self._detail_url(digits)
        self._log(f"直达详情页：{detail_url}")
        meta = await self._fetch_detail(digits, detail_url, notes, "direct")
        if meta is not None:
            return meta

        # 2) 兜底：站内搜索（站点没收录 / 直达 404 时才走）
        if self._config.search_fallback:
            found = await self._discover(digits, notes)
            for url in found[:3]:
                if url == detail_url:
                    continue
                self._log(f"搜索命中，进详情页：{url}")
                meta = await self._fetch_detail(digits, url, notes, "search")
                if meta is not None:
                    return meta

        return self._miss(number, notes or ["直达 404，站内搜索也没有命中"])

    # ---------- 详情页 ----------

    async def _fetch_detail(
        self,
        digits: str,
        url: str,
        notes: list[str],
        stage: str,
    ) -> MediaMetadata | None:
        text, err = await self._get(url)
        if err:
            notes.append(f"{stage} {url} 抓取失败：{err}")
            return None
        if not text:
            notes.append(f"{stage} {url} 空响应")
            return None
        if _is_challenge(text):
            notes.append(f"{stage} {url} 撞到人机验证/反爬页")
            return None
        if self._config.debug_save_pages:
            self._dump(f"{_safe_name(digits)}.{stage}.html", text)
        if "vendor-info" not in text and "product-name" not in text:
            notes.append(f"{stage} {url} 不是详情页（站点可能改版）")
            return None
        return self._parse(digits, url, text, notes)

    def _parse(
        self,
        digits: str,
        url: str,
        html: str,
        notes: list[str],
    ) -> MediaMetadata | None:
        from parsel import Selector

        sel = Selector(text=html)
        cfg = self._config
        self._log_selectors(sel)

        info = _info_map(sel, cfg.info_selector)
        self._log("信息表: " + ", ".join(f"{k}={v[:2]}" for k, v in info.items()))

        # 站点自己写的 ID 与我们要的番号必须一致，避免拿到「同号不同片」
        page_id = (info.get("fc2sid") or [""])[0]
        id_digits = _fc2_digits(page_id) or ""
        if id_digits and id_digits.lstrip("0") != digits.lstrip("0"):
            notes.append(f"详情页番号不符：页面 {page_id} ≠ 目标 {digits}")
            self._log(f"番号不符，放弃：{page_id} != {digits}")
            return None

        title = _first_text(sel, cfg.title_selector)
        cover = _first_image_source(sel, cfg.cover_selector, cfg.cover_allow_gif)

        release = None
        for key in ("releasedate", "release", "date"):
            for value in info.get(key) or []:
                release = _norm_date(value)
                if release:
                    break
            if release:
                break

        runtime = None
        for key in ("duration", "runtime", "length"):
            for value in info.get(key) or []:
                runtime = _parse_runtime_minutes(value)
                if runtime:
                    break
            if runtime:
                break

        studio = None
        if cfg.include_studio:
            studio = _first_text(sel, cfg.maker_selector)
            if not studio:
                maker = info.get("maker") or []
                studio = maker[0] if maker else None

        tags: list[str] = []
        if cfg.include_tags:
            tags = _unique(_safe_css(sel, cfg.tags_selector))
            drop = {t.strip().lower() for t in (cfg.tags_drop or "").split(",") if t.strip()}
            if cfg.drop_maker_tag and studio:
                drop.add(studio.strip().lower())
            tags = [t for t in tags if t.strip().lower() not in drop][: max(0, cfg.tags_max)]

        samples: list[str] = []
        if cfg.include_samples:
            samples = _sample_urls(sel, cfg.samples_selector, cfg.samples_max)

        plot = None
        if cfg.include_plot:
            plot = _plot_text(sel, cfg.plot_selector, cfg.plot_max)

        self._log(
            f"解析结果: title={'有' if title else '无'} cover={'有' if cover else '无'} "
            f"release={release} runtime={runtime} studio={studio} "
            f"tags={len(tags)} samples={len(samples)} plot={'有' if plot else '无'}"
        )

        if not any([title, cover, release, runtime, studio, tags, samples, plot]):
            notes.append(f"{url} 页面解析不出任何字段（选择器可能失效）")
            return None

        fields = _metadata_field_names()
        kwargs: dict[str, object] = {
            "number": _canonical(digits),
            "title": title,
            "release": release,
            "runtime": runtime,
            "studio": studio,
            "tags": tags,
            "plot": plot,
            "source_url": url,
            "external_id": digits,
        }
        if cover:
            if "poster_urls" in fields:
                kwargs["poster_urls"] = [cover]
            if "thumb_urls" in fields:
                kwargs["thumb_urls"] = [cover]
        if samples and "extrafanart" in fields:
            kwargs["extrafanart"] = samples
        return MediaMetadata(**kwargs)

    # ---------- 发现（兜底）----------

    async def _discover(self, digits: str, notes: list[str]) -> list[str]:
        from parsel import Selector

        url = self._search_url(digits)
        text, err = await self._get(url)
        if err:
            notes.append(f"站内搜索失败：{err}")
            return []
        if self._config.debug_save_pages:
            self._dump(f"{_safe_name(digits)}.search.html", text)
        if _is_challenge(text):
            notes.append("站内搜索撞到人机验证/反爬页")
            return []
        sel = Selector(text=text)
        wanted = re.compile(r"/detail/FC2-PPV-0*" + digits + r"(?![0-9])", re.I)
        found: list[str] = []
        for href in _safe_css(sel, "a::attr(href)"):
            if wanted.search(href or ""):
                if href not in found:
                    found.append(href)
        self._log(f"站内搜索 {url}: 命中 {len(found)} 条详情页链接")
        if not found:
            notes.append(f"站内搜索没找到 FC2-PPV-{digits}")
        return found

    def _detail_url(self, digits: str) -> str:
        base = self._config.base_url.rstrip("/")
        template = self._config.detail_template or "{base}/detail/FC2-PPV-{digits}/x"
        return template.replace("{base}", base).replace("{digits}", digits)

    def _search_url(self, query: str) -> str:
        base = self._config.base_url.rstrip("/")
        path = (self._config.search_path or "/search?text={q}").strip()
        if not path.startswith("/"):
            path = "/" + path
        return (base + path).replace("{q}", quote(query))

    # ---------- 网络 ----------

    async def _get(self, url: str) -> tuple[str | None, str | None]:
        """带退避重试的 GET。

        404 / 500 不重试（确定无此番号）。**撞上 Cloudflare 挑战时会先过一次
        挑战（起本机浏览器拿 cf_clearance）再重试**，且这次过挑战不算重试次数。
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
                if status in (404, 500):
                    return None, f"HTTP {status}：{url}"
                if reason in ("cloudflare_challenge", "cloudflare_blocked") or status == 403:
                    challenged = True
                last_err = f"HTTP {status or '-'} {reason}"
            except Exception as exc:  # noqa: BLE001 - 任何异常都要落到诊断里
                last_err = f"{type(exc).__name__}: {exc}"

            if challenged and refresh_left > 0:
                refresh_left -= 1
                self._session = None  # 旧的 clearance 可能过期了，清掉重拿
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

    # ---------- 诊断 ----------

    def _miss(self, number: str, notes: list[str]):
        detail = "; ".join(notes) if notes else "没有任何可用来源"
        self._log(f"未命中 {number}：{detail}")
        if self._config.report_misses:
            raise SourceError(FailureReason.NO_USABLE_METADATA, detail=f"supfc2: {detail}")
        return None

    def _log_selectors(self, sel) -> None:
        """打印每个选择器的命中数——站点换皮时这是最快的定位方式。"""
        cfg = self._config
        parts = []
        for name, selector in (
            ("title", cfg.title_selector),
            ("cover", cfg.cover_selector),
            ("info", cfg.info_selector),
            ("maker", cfg.maker_selector),
            ("tags", cfg.tags_selector),
            ("samples", cfg.samples_selector),
            ("plot", cfg.plot_selector),
        ):
            counts = []
            for part in _split_selectors(selector)[:3]:
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


# ---------- 入口 ----------


class Plugin(FilmSourcePlugin):
    """放在 ``{数据目录}/plugins/sources/esl1.supfc2/`` 下的 plugin.py 必须导出此类。"""

    config_model: ClassVar[type[BaseModel]] = Supfc2Config

    @classmethod
    def descriptor(cls) -> SourceDescriptor:
        return SourceDescriptor(
            id="esl1.supfc2",
            name="supfc2.com (FC2 素人片库)",
            version="0.1.0",
            capabilities=frozenset({SourceCapability.FILM_METADATA}),
            # 本站只收录 FC2。**想接到别的路由就改这一行**：没声明的类型在路由
            # 选择器里根本选不到这个源（就是那个「网站能搜 FC2，插件却加不进
            # FC2 路由」的问题）。
            content_types=frozenset({"fc2"}),
            metadata_fields=frozenset(
                {
                    "title",
                    "tags",
                    "release",
                    "runtime",
                    "studio",
                    "plot",
                    "poster_urls",
                    "thumb_urls",
                    "extrafanart",
                    "source_url",
                }
            ),
            urls=(BASE, IMAGE_HOST),
            rate_limit=1.0,
        )

    def build(self, context: PluginContext, config: BaseModel) -> FilmSourceProvider:
        if not isinstance(config, Supfc2Config):
            raise TypeError("unexpected config type")
        return Supfc2Provider(context, config)


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
#
#  ⚠ 复制这一段时**必须连它的 import 一起复制**：曾经漏掉
#  ``from urllib.request import ...``，结果 _json_get 抛 NameError，又被
#  就绪循环的 ``except Exception`` 吞掉，15 秒后才以「调试接口没有就绪」
#  冒出来，完全看不出原因。
# ============================================================================

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
