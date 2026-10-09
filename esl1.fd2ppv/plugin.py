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


# ---- 宿主 SDK 握手 ----

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


# ---------- 站点常量 ----------

BASE = "https://fd2ppv.cc"
IMAGE_HOST = "https://contents.fc2.com"


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
    drop_category_tag: bool = Field(
        default=True, title="去掉分类同名标签",
        description="标签里若出现与「カテゴリ」同名的项（如 未流出），写入前去掉。",
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


class Plugin(FilmSourcePlugin):
    """放在 ``{数据目录}/plugins/sources/esl1.fd2ppv/`` 下的 plugin.py 必须导出此类。"""

    config_model = Fd2PpvConfig

    @classmethod
    def descriptor(cls):
        return SourceDescriptor(
            id="esl1.fd2ppv",
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