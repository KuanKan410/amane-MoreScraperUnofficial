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