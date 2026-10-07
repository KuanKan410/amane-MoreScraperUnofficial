"""amane 插件：fc2ppv-db.com（FC2 PPV 元数据数据库）

本文件里的每一个结论都是 2026-09-28 在本机真网 + 真实浏览器上量出来的，
不是照着 HTML 猜的。改选择器之前请先读这段。

站点形态
--------
* Next.js App Router，路由 ``/{locale}/videos/{id}``，locale 现有 ``ja``。
* **番号可以直接拼 URL**：``FC2-PPV-3125926`` → ``/ja/videos/3125926``。
  URL 里的数字**就是** FC2 的番号数字，所以**不需要搜索**，一跳直达详情页
  （这是最稳的发现方式，比站点搜索可靠得多）。
* ``robots.txt`` 暴露了 ``/api/`` 与 ``/*/videos/*``、``/*/age-verify``；
  sitemap 是 ``sitemapindex`` + 32 个 ``/sitemaps/N.xml``（N 最大 31）。

两道门
------
1. **Cloudflare managed challenge**：裸 curl_cffi（含各种 impersonate）一律 403
   ``Just a moment...``。唯一可行解是拉起本机**有头** Chrome（起来立刻最小化）
   拿 ``cf_clearance``，之后 1500 秒内复用。``cf_clearance`` 与 IP + UA 绑定，
   cookie 和 UA 必须成对保存、成对使用。
2. **年龄确认**：中间件会把未验证请求重定向到
   ``/{locale}/age-verify?returnTo=...``。好消息是只要请求带
   ``age-verified=true`` cookie 就**直通**，不需要模拟点击 —— 浏览器里点
   「はい、18歳以上です」之后写下的正是这个 cookie。

数据在哪（关键）
----------------
详情页 SSR 的「動画詳細情報」区块只显示 動画ID / 流出 / モザイク / 出演女優，
**没有日期、没有时长、没有卖家**。这些数据在 Next.js 的 RSC payload 里：:

    self.__next_f.push([1, "..."])
      → {"video":{"id":"3125926","title":"...",
         "releaseDate":"$D2026-03-26T00:00:00.000Z","duration":4078,
         "sellerId":"...","seller":{...},
         "actresses":[{"videoId":...,"actress":{"name":"ゆか",...}}],
         "thumbnailLocal":"thumbnails/31/3125926.webp", ...}}

两个坑：

* RSC 里的日期是 **``$D`` 前缀**（Next.js 的 Date 序列化，
  ``$D2026-03-26T00:00:00.000Z``），必须剥掉前两个字符才能用。
* ``releaseDate`` 出现 381 次里绝大部分是 **i18n 翻译键**
  （``"Video":{"releaseDate":"配信日"}``），不是真数据。别拿计数当证据。

封面
----
``og:image`` 在**详情页**是真的：
``https://d39j7zpbpqkw9s.cloudfront.net/thumbnails/31/3125926.webp``。
但首页 / 年龄确认页的 ``og:image`` 是 ``https://localhost:3000/opengraph-image?...``
的**无效值**（站点自己配错了），所以绝不能用首页的 og:image 兜底。

数据质量（7 个样本实测）
------------------------
标题 6/7、封面 6/7、日期 4/7、时长 4/7、卖家 4/7、女優 4/7。
部分条目 ``scrapeFailed=true``（站点自己抓失败了），这时
releaseDate / duration / seller 都是 null —— 插件如实留空，不编造。

批量刮削只起一个浏览器（0.3.0 修复）
------------------------------------
``worker.concurrency = 20`` 时，20 个 fetch 会在同一秒同时撞上挑战。旧实现每个
fetch 各起一个 Chrome（实测一批累计 87 次启动，机器卡死）。现在由进程级
``_SessionGate`` 统一收口：谁先过完挑战，``cf_clearance`` 立刻被同批的其它任务
复用；没赶上的在锁上等，醒来直接重试，**不会自己再起一个浏览器**。详见
``_SessionGate`` 与 ``_ensure_session`` 的注释。
"""

from __future__ import annotations

import asyncio
import base64  # noqa: F401 - 内联 CDP 客户端需要
import importlib
import json
import os  # noqa: F401 - 内联 CDP 客户端需要
import random
import re
import shutil  # noqa: F401 - 内联 CDP 客户端需要
import socket  # noqa: F401 - 内联 CDP 客户端需要
import struct  # noqa: F401 - 内联 CDP 客户端需要
import subprocess  # noqa: F401 - 内联 CDP 客户端需要
import sys
import tempfile
import threading
import time
import weakref
from pathlib import Path
from typing import Any, ClassVar
from urllib.request import ProxyHandler, Request, build_opener

from parsel import Selector
from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# 宿主 SDK：打包版（PyInstaller）里 amane.plugin 与 amane.plugins.api 是两个
# 不同的模块对象，直接 from amane.plugin import 会拿到另一个 FilmSourcePlugin，
# 于是加载时报 "must define a FilmSourcePlugin subclass named Plugin"。
# 所以按宿主 manager 自己的导入顺序排优先级，逐个 importlib.import_module。
# --------------------------------------------------------------------------

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

_API_PLAN = (
    ("amane.plugins.api", ("FilmSourcePlugin", "FilmSourceProvider", "PluginContext")),
    ("amane.plugins.models", ("SourceCapability", "SourceDescriptor")),
    ("amane.net.errors", ("FailureReason", "SourceError", "RequestError")),
    ("amane.net.http", ("WebClient",)),
    ("amane.crawlers.models", ("MediaMetadata", "SearchQuery", "FetchOptions")),
    ("amane.plugin", _SDK_API_NAMES),  # 兜底：作者 SDK 壳
)


def _load_host_api() -> tuple[dict[str, object], list[str], list[str]]:
    """按优先级凑齐插件 API，返回 (名字 -> 对象, 诊断行, 缺失的名字)。"""
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
    """把导入实况写进插件目录；任何异常都不允许影响加载。"""
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
            f"本插件用的 FilmSourcePlugin = {base!r}",
            f"宿主 manager 比对用的那个   = {canonical!r}",
            f"两者是同一对象              = {base is canonical}",
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

FilmSourcePlugin = _API["FilmSourcePlugin"]
FilmSourceProvider = _API["FilmSourceProvider"]
MediaMetadata = _API["MediaMetadata"]
SourceDescriptor = _API["SourceDescriptor"]
SourceError = _API["SourceError"]
FailureReason = _API["FailureReason"]
SourceCapability = _API["SourceCapability"]


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

BASE = "https://fc2ppv-db.com"
# 站点 CDN。⚠️ 这个域名是手抄进来的，曾把 jz7 抄成 j7z（d39j7zp…），
# 结果拼出来的剧照/封面全部 SSL 握手失败。所以插件不单纯依赖它：
# _detect_cdn() 会先从当前页面里真实出现过的图片 URL 反推域名，推不出来才用这里。
CDN_BASE = "https://d39jz7pbpqkw9s.cloudfront.net"

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 年龄确认：带上它就不用点按钮了（浏览器点确认后写的正是这个 cookie）
AGE_COOKIE_NAME = "age-verified"
AGE_COOKIE_VALUE = "true"

# 挑战页特征。注意 **不要** 用 "challenge-platform" —— 正常页面也会引
# /cdn-cgi/challenge-platform/scripts/jsd/main.js，会把每个成功页误判成挑战。
_CHALLENGE_MARKERS: tuple[str, ...] = (
    "just a moment",
    "请稍候",
    "cf-chl",
    "checking your browser",
    "attention required",
    "challenges.cloudflare.com",
    "error 1020",
    "error 1015",
)

# 年龄确认页特征（中间件重定向后的落地页）
_AGE_MARKERS: tuple[str, ...] = (
    "年齢確認",
    "age-verify",
    "ageVerifyTitle",
)

# FC2 番号 → 数字。容忍 FC2-PPV-3125926 / FC2-3125926 / FC2PPV 3125926
_FC2_RE = re.compile(r"^FC2(?:[-_\s]?PPV)?[-_\s]*(\d{4,9})$", re.I)

_RSC_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*(".*?")\]\)', re.S)
_RSC_VIDEO_RE = re.compile(r'\{"video":\{')
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def _digits(number: str) -> str | None:
    """``FC2-PPV-3125926`` / ``FC2-3125926`` / ``3125926`` → ``"3125926"``。

    amane 在 fc2 路由里给的规范写法是 ``FC2-PPV-3125926`` 或 ``FC2-3125926``，
    而本站 URL 只认那串数字，所以全部归一成数字。
    """
    raw = (number or "").strip()
    if not raw:
        return None
    m = _FC2_RE.match(raw)
    if m:
        return m.group(1)
    # 已经只剩纯数字的情况
    if raw.isdigit() and 4 <= len(raw) <= 9:
        return raw
    # 兜底：取串里最后一段 4~9 位数字（FC2 番号数字位）
    tail = re.findall(r"\d{4,9}", raw.replace(" ", ""))
    return tail[-1] if tail else None


def _rsc_payload(html: str) -> str:
    """把页面里所有 ``self.__next_f.push([1, "..."])`` 拼成一个字符串。"""
    chunks: list[str] = []
    for raw in _RSC_PUSH_RE.findall(html or ""):
        try:
            chunks.append(json.loads(raw))
        except Exception:  # noqa: BLE001 - 坏块跳过即可
            continue
    return "".join(chunks)


def _extract_object(text: str, start: int) -> str | None:
    """从 ``start``（必须是 ``{``）开始，按括号配平取出一个 JSON 对象。"""
    depth = 0
    i = start
    in_str = False
    esc = False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        i += 1
    return None


def _rsc_video(html: str, digits: str) -> dict[str, Any] | None:
    """从 RSC payload 里取出主视频对象（按 id 匹配，避免拿到「関連動画」）。"""
    payload = _rsc_payload(html)
    if not payload:
        return None
    for m in _RSC_VIDEO_RE.finditer(payload):
        obj = _extract_object(payload, m.start())
        if not obj:
            continue
        try:
            data = json.loads(obj)
        except Exception:  # noqa: BLE001
            continue
        video = data.get("video") or {}
        if str(video.get("id")) == str(digits):
            return video
    return None


def _norm_rsc_date(value: Any) -> str | None:
    """``$D2026-03-26T00:00:00.000Z`` → ``2026-03-26``。"""
    if not isinstance(value, str):
        return None
    text = value[2:] if value.startswith("$D") else value
    m = _DATE_RE.search(text)
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _first_text(sel: Selector, css: str) -> str | None:
    if not css:
        return None
    for value in sel.css(css).getall():
        text = (value or "").strip()
        if text:
            return text
    return None


def _all_texts(sel: Selector, css: str) -> list[str]:
    if not css:
        return []
    out: list[str] = []
    for value in sel.css(css).getall():
        text = (value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _is_bad_image(url: str) -> bool:
    """挡掉图标 / 占位图 / 站点自己配错的 localhost og:image。"""
    if not url:
        return True
    low = url.lower()
    if low.startswith(("data:", "javascript:")):
        return True
    # 站点首页 og:image 是 https://localhost:3000/opengraph-image?...（配错）
    if "localhost" in low or "127.0.0.1" in low:
        return True
    for marker in ("/favicon", "/icon.svg", "/apple-icon", "logo", "/assets/"):
        if marker in low:
            return True
    return False


def _abs_url(url: str, base: str = BASE) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    return base.rstrip("/") + "/" + url.lstrip("/")


# 站点 CDN 上的路径特征（用来把站点 CDN 与其它图床区分开）
_CDN_MARKERS = ("/thumbnails/", "/samples/", "/faces/", "/sellers/")
_CDN_HOST_RE = re.compile(r"https://([A-Za-z0-9.\-]+)(/[^?#\"'\s]*)")


def _detect_cdn(sel: Selector) -> str:
    """从当前页面里真实出现过的图片 URL 反推 CDN 根地址。

    ``thumbnailLocal`` / ``imageLocal`` 都是相对路径，必须拼上 CDN 域名才能用。
    域名本来只能手抄常量 —— 而手抄会抄错（本站域名 jz7 极易看成 j7z），
    抄错后拼出来的 URL 全部 SSL 握手失败、图片一张都下不来。
    所以这里优先从页面自己的图片里数出用得最多的那个 CDN 域名，推不出来才回退常量。
    """
    counts: dict[str, int] = {}
    for raw in sel.css("img::attr(src)").getall():
        match = _CDN_HOST_RE.match((raw or "").strip())
        if not match:
            continue
        if not any(marker in match.group(2) for marker in _CDN_MARKERS):
            continue
        host = match.group(1).lower()
        counts[host] = counts.get(host, 0) + 1
    if not counts:
        return ""
    return "https://" + max(counts, key=lambda key: counts[key])


# --------------------------------------------------------------------------
# 进程级「浏览器闸门」（批量刮削不再开几十个 Chrome）
#
# 2026-09-29 实测现场：amane 的 ``worker.concurrency = 20``，一次批量刮 20 个番号时
# 20 个 fetch 几乎同时撞上 Cloudflare 挑战。修复前每个 fetch 各起一个 Chrome ——
# debug.log 里同一秒出现 3~20 个不同 port，一批下来累计起了 87 次浏览器，
# 机器 CPU/内存直接拉满。
#
# 根因有两个，缺一不可：
#   1. ``self._session`` 是**每个 provider 实例私有的**：A 实例刚过完挑战拿到的
#      cf_clearance，B 实例看不见，于是 B 又去起一个浏览器。
#   2. ``_refresh_session()`` 没有任何并发控制：20 个协程同时冲进去，就是 20 个 Chrome。
#
# 闸门同时解决这两件事：
#   * **进程级会话缓存**（按 data_dir 隔离）：谁过完挑战，所有实例立刻复用；
#   * **每个事件循环一把 asyncio.Lock**：同一时刻只放行一个 fetch 去起浏览器，
#     其余在锁上等待，醒来后直接拿结果，绝不自作主张再起一个；
#   * **失败冷却**：过挑战失败后一段时间内不再重复起浏览器，把原因老实报上去
#     （宁可 20 条一起快速失败，也不要 20 个 Chrome 一起把机器拖死）。
# --------------------------------------------------------------------------


class _SessionGate:
    """进程级会话 / 浏览器互斥协调器。跨 provider 实例共享。"""

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        # 事件循环 → 锁。用弱引用，避免循环被回收后 key 残留（id 会被复用）。
        self._locks: weakref.WeakKeyDictionary[Any, asyncio.Lock] = (
            weakref.WeakKeyDictionary()
        )
        self._sessions: dict[str, dict[str, Any]] = {}
        self._failed_at: dict[str, float] = {}
        self.launches = 0
        self.reuses = 0
        self.already_open = 0  # 省下来的浏览器次数（差点又起一个）

    def lock_for_current_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with self._mutex:
            lock = self._locks.get(loop)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[loop] = lock
            return lock

    def session(self, key: str, ttl: float) -> dict[str, Any] | None:
        """取进程级缓存里仍然有效的会话；过期 / 不存在返回 None。"""
        with self._mutex:
            data = self._sessions.get(key)
        if not data or not data.get("cookie_header"):
            return None
        if time.time() - float(data.get("ts", 0)) > ttl:
            return None
        return dict(data)

    def any_session(self, key: str) -> dict[str, Any] | None:
        """不看有效期，取缓存里最新的那条（用来在冷却期搏一把）。"""
        with self._mutex:
            data = self._sessions.get(key)
        return dict(data) if data else None

    def publish(self, key: str, session: dict[str, Any]) -> None:
        """把一条新会话放进进程缓存（谁过完挑战，大家都看得见）。"""
        with self._mutex:
            self._sessions[key] = dict(session)

    def note_launch(self) -> None:
        with self._mutex:
            self.launches += 1

    def note_success(self, key: str) -> None:
        with self._mutex:
            self._failed_at.pop(key, None)

    def note_reuse(self, spared: bool = False) -> None:
        with self._mutex:
            self.reuses += 1
            if spared:
                self.already_open += 1

    def cooldown_left(self, key: str, cooldown: float) -> float:
        with self._mutex:
            last = self._failed_at.get(key)
        if not last:
            return 0.0
        return max(0.0, cooldown - (time.time() - last))

    def note_failure(self, key: str) -> None:
        with self._mutex:
            self._failed_at[key] = time.time()

    def stats(self) -> tuple[int, int, int]:
        with self._mutex:
            return self.launches, self.reuses, self.already_open

    def reset(self) -> None:
        """测试用：清空进程级状态。"""
        with self._mutex:
            self._sessions.clear()
            self._failed_at.clear()
            self.launches = self.reuses = self.already_open = 0


_GATE = _SessionGate()


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------


class Fc2PpvDbConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(
        default=BASE,
        title="站点根地址",
        description="一般不用改。站点换域名时才填新地址（不要带结尾斜杠）。",
    )
    locale: str = Field(
        default="ja",
        title="语言目录",
        description="详情页路径里的语言段，如 ja。站点目前主要用 ja。",
    )
    cdn_base: str = Field(
        default=CDN_BASE,
        title="封面 CDN 前缀",
        description="封面图所在的 CloudFront 域名，用于拼接 thumbnailLocal。",
    )
    age_cookie: str = Field(
        default=f"{AGE_COOKIE_NAME}={AGE_COOKIE_VALUE}",
        title="年龄确认 cookie",
        description="格式 name=value。带上它即可跳过年龄确认页，不用模拟点击。",
    )
    use_rsc: bool = Field(
        default=True,
        title="优先解析 RSC 数据",
        description="详情页的日期/时长/卖家只在 Next.js 的 RSC payload 里，关掉就只能拿到标题和封面。",
    )
    title_selector: str = Field(
        default="h1::text",
        title="标题选择器（兜底）",
        description="RSC 解析不到标题时才用。默认取 h1。",
    )
    cover_selector: str = Field(
        default="meta[property='og:image']::attr(content)",
        title="封面选择器",
        description="详情页 og:image 是真实 CDN 地址。注意首页的 og:image 是无效值，插件会自动挡掉。",
    )
    actor_selector: str = Field(
        default="a[href*='/ja/actresses/']::text",
        title="女優选择器（兜底）",
        description="RSC 解析不到出演女優时才用。注意替换语言段以匹配你的 locale。",
    )
    tags_selector: str = Field(
        default="a[href^='/ja/videos?tags=']::text",
        title="标签选择器（兜底）",
        description=(
            "RSC 里取不到 productTags 时才用。「タグ」区块里每个标签都是站内筛选链接，"
            "取链接文字即可。注意替换语言段以匹配你的 locale。"
        ),
    )
    drop_seller_tag: bool = Field(
        default=True,
        title="去掉与卖家同名的标签",
        description="站点会把卖家名也塞进标签里（如「むすめガチャ」）。开启后剔除，避免和片商字段重复。",
    )
    max_tags: int = Field(
        default=30,
        title="标签数量上限",
        description="最多保留多少个标签，按页面顺序取前 N 个。填 0 表示不限。",
    )
    sample_selector: str = Field(
        default="img[src*='/samples/']::attr(src)",
        title="剧照选择器（兜底）",
        description=(
            "RSC 里取不到 images 时才用。「サンプル画像」区块的图片，"
            "形如 samples/49/4973988/000.webp。"
        ),
    )
    fetch_samples: bool = Field(
        default=True,
        title="抓取剧照",
        description="把「サンプル画像」写入 extrafanart。关闭则只取封面。",
    )
    max_samples: int = Field(
        default=20,
        title="剧照数量上限",
        description="最多保留多少张剧照，按页面顺序取前 N 张。填 0 表示不限。",
    )
    verify_number: bool = Field(
        default=True,
        title="校验番号",
        description="确认 RSC 里取到的视频 id 与请求的番号数字一致，防止拿错条目。",
    )
    browser_fallback: bool = Field(
        default=True,
        title="撞挑战时启动浏览器",
        description="站点挂 Cloudflare 挑战，纯 HTTP 过不去。开启后会自动拉起本机 Chrome 拿 cf_clearance。",
    )
    browser_path: str = Field(
        default="",
        title="浏览器路径",
        description="留空则自动找本机 Chrome / Edge。找不到时在这里填完整路径。",
    )
    browser_headless: bool = Field(
        default=False,
        title="无头模式",
        description="无头 Chrome 会被 Cloudflare 识破，默认关闭（有头 + 立即最小化）。除非你知道后果，否则别开。",
    )
    browser_singleton: bool = Field(
        default=True,
        title="批量时只起一个浏览器",
        description=(
            "开启后同一时刻只允许一个抓取任务去启动浏览器过挑战，其余任务等待并复用结果。"
            "关闭会让每个任务各起一个 Chrome（批量刮削时机器会被拖死），除非有特殊理由否则别关。"
        ),
    )
    browser_attempts: int = Field(
        default=2,
        title="过挑战尝试次数",
        description="一次放行内最多顺序尝试几次过挑战。顺序执行，不会同时开多个浏览器。",
    )
    refresh_cooldown: float = Field(
        default=20.0,
        title="过挑战失败冷却（秒）",
        description=(
            "全部尝试都失败后，这段时间内其它任务不再重复启动浏览器，而是直接报失败原因。"
            "调 0 等于关掉冷却（不推荐）。"
        ),
    )
    browser_proxy: str = Field(
        default="",
        title="浏览器代理",
        description="留空则跟随 amane 的网络设置。浏览器出网和 amane 不一致时才填，如 http://127.0.0.1:7892。",
    )
    challenge_timeout: float = Field(
        default=45.0,
        title="过挑战超时（秒）",
        description="等待 Cloudflare 挑战通过的最长时间。机器慢或网络差可调到 60。",
    )
    session_ttl: float = Field(
        default=1500.0,
        title="会话有效期（秒）",
        description="cf_clearance 的复用时长。过期才会再次启动浏览器。",
    )
    cookie: str = Field(
        default="",
        title="手动 cookie",
        description="可选。整条 Cookie 头，用于绕过自动取会话。留空则自动获取。",
    )
    user_agent: str = Field(
        default="",
        title="User-Agent",
        description="留空则用会话里保存的 UA。cf_clearance 与 UA 绑定，手动改会让 cookie 失效。",
    )
    retries: int = Field(
        default=3,
        title="重试次数",
        description="单次抓取的最大尝试次数。过挑战重试试用但不计入次数。",
    )
    debug_dump: bool = Field(
        default=True,
        title="写调试日志",
        description="在插件数据目录 debug/debug.log 记录每一步，排查时最有用。",
    )
    debug_save_pages: bool = Field(
        default=True,
        title="保存抓到的页面",
        description="把详情页原文存到 debug/ 下，便于事后核对选择器。",
    )
    report_misses: bool = Field(
        default=True,
        title="报告未命中原因",
        description="站点确实没有该番号时抛出原因，而不是静默返回空。",
    )


# --------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------


class Fc2PpvDbProvider(FilmSourceProvider):  # type: ignore[misc]
    """按番号直接拼详情页 URL，解析 RSC payload 取元数据。"""

    def __init__(self, context: Any, config: Fc2PpvDbConfig) -> None:
        self._ctx = context
        self._http = context.http_client
        self._cfg = config
        self._data_dir = Path(context.data_dir)
        self._debug_dir = self._data_dir / "debug"
        self._session: dict[str, Any] = {}

    # ---------- 诊断 ----------

    def _log(self, line: str) -> None:
        if not self._cfg.debug_dump:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with (self._debug_dir / "debug.log").open("a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] {line}\n")
        except Exception:  # noqa: BLE001 - 日志失败绝不能影响抓取
            pass

    def _dump(self, name: str, text: str) -> None:
        if not self._cfg.debug_save_pages:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            (self._debug_dir / name).write_text(text or "", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # ---------- 会话 ----------

    def _session_key(self) -> str:
        """会话 / 冷却的共享键：同一个 data_dir 下所有 provider 实例共用一条。"""
        return str(self._data_dir)

    def _session_path(self) -> Path:
        return self._data_dir / "cf_session.json"

    def _read_disk_session(self) -> dict[str, Any] | None:
        path = self._session_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 半截 JSON / 文件正被写
            return None
        if isinstance(data, dict) and data.get("cookie_header"):
            return data
        return None

    def _cookie_header(self) -> str:
        return (self._session.get("cookie_header") or "").strip()

    def _populate_session(self) -> None:
        """把当前能拿到的最新会话装进 ``self._session``（尽力而为，不判有效期）。

        顺序：内存 → 进程缓存 → 磁盘；配置里手填的 cookie 只作为**兜底种子**
        （内存里一条都没有时才用）。这一点很关键：手填 cookie 一旦被浏览器
        刷出来的新 cookie 取代，就不能在下一次 ``_populate_session()`` 时又把它
        盖回去 —— 否则会一直拿着那条死 cookie 空转。
        """
        key = self._session_key()
        ttl = float(self._cfg.session_ttl)
        best_ts = (
            float(self._session.get("ts", 0)) if self._session.get("cookie_header") else 0.0
        )
        if not best_ts and self._cfg.cookie.strip():
            self._session = {
                "cookie_header": self._cfg.cookie.strip(),
                "user_agent": self._cfg.user_agent.strip() or _DEFAULT_UA,
                "ts": time.time(),
                "manual": True,
            }
            return
        if best_ts and time.time() - best_ts <= ttl:
            return  # 内存里那条还新鲜，不用再翻别处
        shared = _GATE.any_session(key)
        if shared and float(shared.get("ts", 0)) > best_ts:
            self._session = shared
            best_ts = float(shared.get("ts", 0))
            self._log("会话来源：进程缓存（同一批的其它任务已过挑战）")
        disk = self._read_disk_session()
        if disk and float(disk.get("ts", 0)) > best_ts:
            self._session = disk
            self._log("会话来源：磁盘缓存")

    def _adopt_shared_session(self, ttl: float) -> bool:
        """有没有一条**仍然有效**的会话？有就装上并返回 True（意味着不用起浏览器）。

        手填 cookie（``manual``）不算「有效会话」：它只是种子，能不能用要撞一次才知道。
        否则它会骗过调用方，让 ``_get()`` 拿着同一条死 cookie 无限重试。
        """
        key = self._session_key()
        best: dict[str, Any] = {}
        for item in (self._session, _GATE.session(key, ttl), self._read_disk_session() or {}):
            if not isinstance(item, dict) or not item.get("cookie_header"):
                continue
            if item.get("manual"):
                continue
            if time.time() - float(item.get("ts", 0)) > ttl:
                continue
            if float(item.get("ts", 0)) > float(best.get("ts", 0)):
                best = item
        if not best:
            return False
        self._session = dict(best)
        _GATE.publish(key, best)  # 顺手回填进程缓存，供其它实例复用
        return True

    def _write_session(self, session: dict[str, Any]) -> None:
        """原子落盘：先写临时文件再替换，避免别的进程读到半截 JSON。"""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            path = self._session_path()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            self._log(f"会话写盘失败：{exc}")

    async def _ensure_session(self, url: str, *, failed_cookie: str = "") -> bool:
        """确保手上有可用的 Cloudflare 会话；批量并发时只放行一个浏览器。

        ``failed_cookie`` 是本次刚被拒的那条 Cookie 头。只有当会话**确实被换掉了**
        （新 cookie ≠ 旧 cookie）才返回 True —— 这是「不会拿着同一条死 cookie
        空转」的硬保证。返回 False 表示过挑战这条路走不通，调用方应当把原因抛出去
        （而不是静默返回 None）。
        """
        if not self._cfg.browser_fallback:
            return False
        cfg = self._cfg
        key = self._session_key()
        ttl = float(cfg.session_ttl)

        def renewed() -> bool:
            """手上这条是不是换过的新 cookie？"""
            return bool(self._cookie_header()) and self._cookie_header() != failed_cookie.strip()

        # ① 快路径：已经有有效会话（自己 / 进程缓存 / 磁盘）→ 直接复用
        if self._adopt_shared_session(ttl) and renewed():
            _GATE.note_reuse()
            self._log("复用已有 Cloudflare 会话（不起浏览器）")
            return True

        if not cfg.browser_singleton:
            # 关掉单例保护 = 回到旧行为（每个任务各起一个），仅供诊断对比
            for attempt in range(1, max(1, int(cfg.browser_attempts)) + 1):
                _GATE.note_launch()
                if await self._refresh_session(url) and renewed():
                    return True
                if attempt < cfg.browser_attempts:
                    await asyncio.sleep(1.5)
            return False

        # ② 抢闸门：同一时刻只有一个任务能去起浏览器，其余在锁上等结果
        lock = _GATE.lock_for_current_loop()
        async with lock:
            # 双检 —— 等锁期间别人可能已经过完挑战了
            if self._adopt_shared_session(ttl) and renewed():
                _GATE.note_reuse(spared=True)
                self._log("等锁期间拿到别的任务过好的会话（不起浏览器）")
                return True

            left = _GATE.cooldown_left(key, float(cfg.refresh_cooldown))
            if left > 0:
                self._log(f"过挑战刚失败过，冷却中（还剩 {left:.0f}s）→ 本次不再起浏览器")
                return False

            # 惊群阻尼：锁一放开，等待者会同时醒来重试，错开一点点
            await asyncio.sleep(random.uniform(0.2, 0.8))
            if self._adopt_shared_session(ttl) and renewed():
                _GATE.note_reuse(spared=True)
                self._log("阻尼等待期间拿到会话（不起浏览器）")
                return True

            attempts = max(1, int(cfg.browser_attempts))
            launches, reuses, spared = _GATE.stats()
            self._log(
                f"闸门放行：起浏览器过挑战（本进程累计启动 {launches + 1} 次 / "
                f"复用 {reuses} 次 / 拦下重复启动 {spared} 次）"
            )
            for attempt in range(1, attempts + 1):
                _GATE.note_launch()
                if await self._refresh_session(url) and renewed():
                    return True
                if attempt < attempts:
                    self._log(f"过挑战第 {attempt} 次没过，顺序再试一次")
                    await asyncio.sleep(1.5)
            _GATE.note_failure(key)
            self._log(
                f"过挑战 {attempts} 次都没过 → 进入 {float(cfg.refresh_cooldown):.0f}s 冷却，"
                "冷却期内其它任务不再重复起浏览器"
            )
            return False

    async def _refresh_session(self, url: str) -> bool:
        """起本机浏览器过一次 Cloudflare，拿到 cf_clearance 后写进程缓存 + 落盘。"""
        if not self._cfg.browser_fallback:
            return False
        self._log(f"Cloudflare 挑战：起浏览器过挑战（{url}）")
        profile = Path(tempfile.mkdtemp(prefix="fc2ppvdb-"))
        try:

            def _run() -> dict[str, Any]:
                return browser_fetch(
                    url,
                    profile_dir=profile,
                    browser_path=self._cfg.browser_path.strip() or None,
                    headless=self._cfg.browser_headless,
                    proxy=self._cfg.browser_proxy.strip() or None,
                    challenge_timeout=self._cfg.challenge_timeout,
                    minimize=True,
                    log_path=self._debug_dir / "chrome.log",
                )

            result = await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001
            self._log(f"过挑战异常：{type(exc).__name__}: {exc}")
            return False
        finally:
            shutil.rmtree(profile, ignore_errors=True)

        for line in result.get("diag") or []:
            self._log(f"  浏览器诊断 {line}")
        if result.get("blocked") or not result.get("cookie_header"):
            self._dump("cloudflare.last.html", result.get("raw_html") or "")
            self._log("过挑战失败：挑战没过（页面已存 cloudflare.last.html）")
            return False

        session = {
            "cookie_header": result["cookie_header"],
            "user_agent": result.get("user_agent") or _DEFAULT_UA,
            "ts": time.time(),
        }
        self._session = session
        key = self._session_key()
        _GATE.publish(key, session)  # 先给进程里其它等待者，再落盘
        _GATE.note_success(key)
        self._write_session(session)
        cookies = (
            list((result.get("cookies") or {}).keys())
            if isinstance(result.get("cookies"), dict)
            else [c.get("name") for c in (result.get("cookies") or [])]
        )
        self._log(f"过挑战成功：cookies={cookies}")
        return True

    def _cookie_dict(self) -> dict[str, str]:
        out: dict[str, str] = {}
        header = (self._session.get("cookie_header") or "").strip()
        for part in header.split(";"):
            if "=" in part:
                key, value = part.split("=", 1)
                key = key.strip()
                if key:
                    out[key] = value.strip()
        # 年龄确认 cookie 每次都带上（它不依赖 Cloudflare 会话）
        cookie = (self._cfg.age_cookie or "").strip()
        if "=" in cookie:
            key, value = cookie.split("=", 1)
            out[key.strip()] = value.strip()
        return out

    def _headers(self) -> dict[str, str]:
        ua = (self._session.get("user_agent") or self._cfg.user_agent or "").strip()
        return {
            "User-Agent": ua or _DEFAULT_UA,
            "Accept-Language": "ja,zh-CN;q=0.9,en;q=0.8",
        }

    # ---------- 抓取 ----------

    def _is_challenge_body(self, text: str) -> bool:
        low = (text or "")[:6000].lower()
        return any(marker in low for marker in _CHALLENGE_MARKERS)

    def _is_age_body(self, text: str) -> bool:
        head = (text or "")[:6000]
        return any(marker in head for marker in _AGE_MARKERS)

    def _is_challenge_error(self, exc: BaseException) -> bool:
        reason = str(getattr(exc, "reason", "") or "")
        status = getattr(exc, "http_status", None)
        if reason in ("cloudflare_challenge", "cloudflare_blocked"):
            return True
        return status == 403

    async def _get(self, url: str) -> str:
        """取页面；撞挑战就过挑战后重试（重试不消耗次数，但**有上限**）。

        三个不变量：
        * 过挑战走 ``_ensure_session()`` 而不是直接起浏览器 —— 批量刮削时几十个
          任务同时撞挑战，闸门保证只有一个真的去起 Chrome；
        * 只有 ``_ensure_session()`` 明确说「cookie 换过了」才白嫖这次重试，
          否则会拿着同一条死 cookie 无限循环；
        * 白嫖次数上限 ``browser_attempts + 1``，再多就直接报挑战失败。
        """
        attempts = max(1, int(self._cfg.retries))
        max_refresh = max(1, int(self._cfg.browser_attempts)) + 1
        index = 0
        refreshes = 0
        while index < attempts:
            index += 1
            self._populate_session()
            used_cookie = self._cookie_header()
            try:
                text = await self._http.get_html(
                    url, headers=self._headers(), cookies=self._cookie_dict()
                )
            except SourceError as exc:  # type: ignore[misc]
                if not self._is_challenge_error(exc):
                    raise
                if not await self._ensure_session(url, failed_cookie=used_cookie):
                    raise
            except Exception as exc:  # noqa: BLE001
                if not self._is_challenge_error(exc):
                    raise
                if not await self._ensure_session(url, failed_cookie=used_cookie):
                    raise
            else:
                if self._is_challenge_body(text):
                    self._dump("cloudflare.200.html", text)
                    if not await self._ensure_session(url, failed_cookie=used_cookie):
                        raise SourceError(  # type: ignore[misc]
                            FailureReason.CLOUDFLARE_CHALLENGE,
                            detail=f"{url}：HTTP 200 但正文是挑战页",
                            url=url,
                        )
                elif self._is_age_body(text):
                    self._dump("age-gate.html", text)
                    raise SourceError(  # type: ignore[misc]
                        FailureReason.AGE_VERIFICATION,
                        detail=f"{url}：落到年龄确认页（age-verified cookie 没生效）",
                        url=url,
                    )
                else:
                    return text

            # 走到这里说明刚过完挑战 → 这次重试不计入 retries，但总量要封顶
            refreshes += 1
            if refreshes > max_refresh:
                raise SourceError(  # type: ignore[misc]
                    FailureReason.CLOUDFLARE_CHALLENGE,
                    detail=f"{url}：连续 {refreshes} 次过挑战后仍停在挑战页",
                    url=url,
                )
            index -= 1
        raise SourceError(  # type: ignore[misc]
            FailureReason.CLOUDFLARE_CHALLENGE,
            detail=f"{url}：重试 {attempts} 次仍停在挑战页",
            url=url,
        )

    # ---------- 解析 ----------

    def _parse(self, number: str, digits: str, html: str, url: str) -> dict[str, Any]:
        sel = Selector(text=html)
        cfg = self._cfg

        video: dict[str, Any] = {}
        if cfg.use_rsc:
            video = _rsc_video(html, digits) or {}
            self._log(f"RSC 主视频对象：{'命中' if video else '未命中'}")

        if cfg.verify_number and video and str(video.get("id")) != str(digits):
            self._log(f"番号校验失败：RSC id={video.get('id')} 期望 {digits}")
            video = {}

        # 标题：RSC 的 title 不带番号前缀；h1 带。优先 RSC，兜底 h1。
        title = (video.get("title") or "").strip() or None
        if not title:
            title = _first_text(sel, cfg.title_selector)
        if title:
            title = re.sub(r"\s+", " ", title).strip()

        # 日期：只在 RSC 里（$D 前缀）
        release = _norm_rsc_date(video.get("releaseDate"))

        # 时长：站点给秒，amane 要分钟
        runtime: int | None = None
        duration = video.get("duration")
        if isinstance(duration, (int, float)) and duration > 0:
            runtime = max(1, int(round(float(duration) / 60)))

        # 卖家 / 片商
        seller = video.get("seller") or {}
        studio = None
        if isinstance(seller, dict):
            studio = (seller.get("name") or seller.get("id") or "").strip() or None
        if not studio:
            seller_id = (video.get("sellerId") or "").strip()
            studio = seller_id or None

        # 出演女優
        actors: list[str] = []
        for item in video.get("actresses") or []:
            if not isinstance(item, dict):
                continue
            actress = item.get("actress") or {}
            name = ""
            if isinstance(actress, dict):
                name = (actress.get("name") or "").strip()
            if not name:
                name = (item.get("name") or "").strip()
            if name and name not in actors:
                actors.append(name)
        if not actors:
            actors = _all_texts(sel, cfg.actor_selector)

        # 拼接相对路径要用的 CDN 根地址：优先取页面自己图片里的域名
        cdn = (_detect_cdn(sel) or cfg.cdn_base).rstrip("/")

        # 封面：og:image → thumbnailUrl → CDN + thumbnailLocal
        cover = _first_text(sel, cfg.cover_selector)
        if _is_bad_image(cover or ""):
            cover = None
        if not cover:
            thumb_url = (video.get("thumbnailUrl") or "").strip()
            if thumb_url and not _is_bad_image(thumb_url):
                cover = _abs_url(thumb_url)
        if not cover:
            local = (video.get("thumbnailLocal") or "").strip()
            if local:
                cover = cdn + "/" + local.lstrip("/")
                if _is_bad_image(cover):
                    cover = None

        plot = (video.get("description") or "").strip() or None

        # 标签：优先 RSC 的 productTags[].tag.name（与页面「タグ」区块一致，但带 id
        # 更结构化）；RSC 不可用 / 关闭 / 站点没给标签时，退回 DOM 的筛选链接文字。
        # 站点自己没标标签时这里为 0 条，属正常（如 4869722）。
        tags: list[str] = []
        for item in video.get("productTags") or []:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag") or {}
            name = ""
            if isinstance(tag, dict):
                name = (tag.get("name") or "").strip()
            if not name:
                name = (item.get("name") or "").strip()
            if name and name not in tags:
                tags.append(name)
        if not tags:
            tags = _all_texts(sel, cfg.tags_selector)
        if cfg.drop_seller_tag and studio:
            tags = [t for t in tags if t != studio]
        if cfg.max_tags > 0:
            tags = tags[: cfg.max_tags]

        # 剧照：优先 RSC 的 images[]（带 sortOrder，比 DOM 顺序可靠）。
        # imageLocal 是站点 CDN 上的 webp（原尺寸），imageUrl 是 FC2 的 w480 缩略图。
        # 与封面（thumbnails/）不同路径，不会重复。
        samples: list[str] = []
        if cfg.fetch_samples:
            images = video.get("images")
            if isinstance(images, list):
                ordered = sorted(
                    (i for i in images if isinstance(i, dict)),
                    key=lambda d: (
                        d.get("sortOrder")
                        if isinstance(d.get("sortOrder"), (int, float))
                        else 9999
                    ),
                )
                for item in ordered:
                    local = (item.get("imageLocal") or "").strip()
                    image = ""
                    if local:
                        image = cdn + "/" + local.lstrip("/")
                    else:
                        image = _abs_url((item.get("imageUrl") or "").strip())
                    if not image or _is_bad_image(image) or image in samples:
                        continue
                    samples.append(image)
            if not samples:
                for raw in sel.css(cfg.sample_selector).getall():
                    image = _abs_url((raw or "").strip())
                    if not image or _is_bad_image(image) or image in samples:
                        continue
                    samples.append(image)
            if cfg.max_samples > 0:
                samples = samples[: cfg.max_samples]

        self._log(
            "选择器命中: "
            f"title[{cfg.title_selector}={len(sel.css(cfg.title_selector).getall())}] "
            f"cover[{cfg.cover_selector}={len(sel.css(cfg.cover_selector).getall())}] "
            f"actor[{cfg.actor_selector}={len(sel.css(cfg.actor_selector).getall())}] "
            f"tags[{len(tags)}] samples[{len(samples)}] "
            f"| RSC: release={release} runtime={runtime} studio={studio} actors={len(actors)}"
        )
        self._log(f"CDN 根地址：{cdn}")
        if samples:
            self._log(f"剧照首张：{samples[0]}")

        return {
            "number": number,
            "title": title,
            "release": release,
            "runtime": runtime,
            "studio": studio,
            "actors": actors,
            "tags": tags,
            "plot": plot,
            "cover": cover,
            "samples": samples,
            "source_url": url,
            "external_id": digits,
        }

    # ---------- 入口 ----------

    async def fetch(self, query: Any, options: Any = None) -> Any | None:
        number = (getattr(query, "number", "") or "").strip()
        self._log(f"=== 开始 {number} data_dir={self._data_dir}")
        digits = _digits(number)
        if not digits:
            self._log(f"番号无法解析出 FC2 数字：{number!r}")
            return None

        cfg = self._cfg
        url = f"{cfg.base_url.rstrip('/')}/{cfg.locale.strip('/')}/videos/{digits}"
        try:
            html = await self._get(url)
        except SourceError:  # type: ignore[misc]
            raise
        except Exception as exc:  # noqa: BLE001
            raise SourceError(  # type: ignore[misc]
                FailureReason.NETWORK, detail=f"{url}: {type(exc).__name__}: {exc}", url=url
            )

        self._dump(f"{digits}.html", html)
        data = self._parse(number, digits, html, url)

        if not data.get("title"):
            msg = f"详情页没有取到标题（番号 {number} 可能不存在）"
            self._log(f"未命中 {number}：{msg}")
            if cfg.report_misses:
                raise SourceError(  # type: ignore[misc]
                    FailureReason.NO_USABLE_METADATA, detail=msg, url=url
                )
            return None

        kwargs: dict[str, Any] = {
            "number": number,
            "title": data["title"],
            "release": data["release"],
            "runtime": data["runtime"],
            "studio": data["studio"],
            "actors": data["actors"],
            "tags": data["tags"],
            "source_url": url,
            "external_id": digits,
        }
        if data.get("cover"):
            kwargs["poster_urls"] = [data["cover"]]
            kwargs["thumb_urls"] = [data["cover"]]
        if data.get("samples"):
            # 剧照写 extrafanart；封面不混进去，避免重复
            kwargs["extrafanart"] = data["samples"]
        if data.get("plot"):
            kwargs["plot"] = data["plot"]

        meta = MediaMetadata(**kwargs)  # type: ignore[misc]
        self._log(
            f"命中 {number}: title={data['title']!r} release={data['release']} "
            f"runtime={data['runtime']} studio={data['studio']} "
            f"actors={len(data['actors'])} tags={len(data['tags'])} "
            f"poster={1 if data.get('cover') else 0} fanart={len(data['samples'])}"
        )
        return meta


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------


class Plugin(FilmSourcePlugin):  # type: ignore[misc]
    config_model: ClassVar[type[BaseModel]] = Fc2PpvDbConfig

    @classmethod
    def descriptor(cls) -> Any:
        return SourceDescriptor(  # type: ignore[misc]
            id="esl1.fc2ppvdb",
            name="FC2PPV Database (fc2ppv-db.com)",
            version="0.3.0",
            capabilities=frozenset({SourceCapability.FILM_METADATA}),
            # 声明 fc2，否则在 fc2 路由的下拉里根本看不到这个插件
            content_types=frozenset({"fc2"}),
            urls=(BASE, CDN_BASE),
            rate_limit=1.0,
            metadata_fields=frozenset(
                {
                    "title",
                    "release",
                    "runtime",
                    "studio",
                    "actors",
                    "tags",
                    "plot",
                    "poster_urls",
                    "thumb_urls",
                    "extrafanart",
                    "source_url",
                }
            ),
        )

    def build(self, context: Any, config: BaseModel) -> FilmSourceProvider:  # type: ignore[misc]
        if not isinstance(config, Fc2PpvDbConfig):
            raise TypeError("unexpected config type")
        return Fc2PpvDbProvider(context, config)


# --------------------------------------------------------------------------
# 下面是内联的、只用标准库实现的 Chrome DevTools Protocol 客户端。
#
# 为什么不用 playwright / selenium：打包版 amane（PyInstaller）里装不了这些
# 第三方包，而 Cloudflare 的挑战**只有真实浏览器能过**（curl_cffi 任何指纹
# 都是 403，无头 Chrome 也会被识破）。所以只能自己拼 WebSocket 帧驱动本机
# Chrome。这段代码与 supjav 插件里的那份是同一份实现。
# --------------------------------------------------------------------------

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
    # 本机回环的调试端口必须**绕过代理**：urllib 默认会读 http_proxy 环境变量，
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
        # 等调试接口就绪。真实原因必须带到异常里 —— 早期版本在这里裸吞异常，
        # 结果一个 NameError / 代理故障都伪装成"调试接口没有就绪"，极难排查。
        deadline = time.time() + 15
        last: BaseException | None = None
        while time.time() < deadline:
            try:
                _json_get(f"http://127.0.0.1:{self.port}/json/version", timeout=3)
                return self
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(0.3)
        raise WSError(
            f"调试接口没有就绪（port={self.port}）："
            f"{type(last).__name__}: {last}" if last else "调试接口没有就绪"
        )

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
