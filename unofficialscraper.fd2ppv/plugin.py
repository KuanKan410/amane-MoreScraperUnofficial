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
