from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import random
import re
import shutil
import socket
import struct
import subprocess
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

BASE_URL = "https://fc2cmadb.com"
# FC2 番号 → 数字。容忍 FC2-PPV-3125926 / FC2-3125926 / FC2PPV 3125926
_FC2_RE = re.compile(r"^FC2(?:[-_\s]?PPV)?[-_\s]*(\d{4,9})$", re.I)

# Data-Page 抽取相关正则和函数
_DATA_PAGE_RE = re.compile(r'<script data-page="app" type="application/json">(.*?)</script>', re.S)

def _data_page(html: str) -> dict | None:
    m = _DATA_PAGE_RE.search(html or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None

def _article(html: str) -> dict | None:
    dp = _data_page(html)
    if not dp:
        return None
    props = dp.get("props")
    if not isinstance(props, dict):
        return None
    art = props.get("article")
    return art if isinstance(art, dict) else None

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


def _detail_url(digits: str, base: str = BASE_URL) -> str:
    """FC2 数字 → 详情页。本站 video_id 就是番号数字。"""
    return f"{base.rstrip('/')}/articles/{digits}"

# --------------------------------------------------------------------------
# 类型占位
# --------------------------------------------------------------------------

class Fc2CmDbConfig(BaseModel):  # type: ignore[misc]
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(default=BASE_URL, title="站点根地址", description="一般不用改。")
    detail_template: str = Field(default="{base}/articles/{digits}",
        title="详情页模板", description="支持 {base} / {digits} 占位。")
    use_http_client: bool = Field(default=True, title="先用 HTTP 直连",
        description="用缓存的登录 cookie 走 curl_cffi 直连；直连拿不到数据才起浏览器。")
    use_browser_fallback: bool = Field(default=True, title="浏览器兜底",
        description="直连失败/撞登录墙时起浏览器抓取。")
    browser_path: str = Field(default="", title="浏览器路径",
        description="留空自动找 Chrome/Edge。")
    browser_headless: bool = Field(default=False, title="无头模式",
        description="登录流程必须有头（要在窗口里登录），除非你知道后果否则别开。")
    browser_attempts: int = Field(default=2, title="浏览器尝试次数",
        description="一次放行内顺序最多试几次。")
    refresh_cooldown: float = Field(default=20.0, title="浏览器失败冷却（秒）",
        description="全部尝试失败后，这段时间不再重复起浏览器。")
    session_ttl: float = Field(default=3600.0, title="会话有效期（秒）",
        description="缓存的登录 cookie 多久后视为过期、需要重新登录。")
    login_timeout: float = Field(default=180.0,
        title="等登录超时（秒）", description="弹出浏览器后等用户手动登录的最长时间。")
    cookie: str = Field(default="", title="手动 cookie",
        description="可选。整条已登录 Cookie 头。留空则自动从浏览器取。")
    user_agent: str = Field(default="", title="User-Agent",
        description="留空用会话里的 UA。")
    include_tags: bool = Field(default=True, title="抓标签", description="是否抓取作品的标签信息")
    include_cover: bool = Field(default=True, title="抓封面", description="是否抓取作品的封面图片")
    drop_writer_same_tag: bool = Field(default=True,
        title="去掉与片商同名的标签",
        description="本站会把片商名也塞进标签，开启则剔除避免和片商字段重复。")
    max_tags: int = Field(default=30, title="标签数量上限", description="0=不限，设置最大保留的标签数量")
    verify_number: bool = Field(default=True, title="校验番号",
        description="确认 article.video_id 与请求数字一致，防止拿到错误的作品条目。")
    retries: int = Field(default=3, title="重试次数", description="请求失败后的最大重试次数")
    report_misses: bool = Field(default=True, title="报告未命中原因", description="遇到未收录作品时是否上报原因")
    debug_dump: bool = Field(default=True, title="写调试日志", description="是否将调试信息写入日志文件")
    debug_save_pages: bool = Field(default=True, title="保存抓到的页面", description="是否将抓取到的页面内容保存到debug目录")


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


def _norm_duration(value) -> int | None:
    """'01:45:04' → 105；'00:50' → 50；'90' → 90；非法/None → None。amane 要分钟。"""
    if isinstance(value, int):
        return value if value > 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    parts = [p for p in text.split(":")]
    try:
        if len(parts) == 3:                      # H:MM:SS
            h, m, s = (int(x) for x in parts)
            return h * 60 + m if h >= 0 and 0 <= m < 60 else None
        if len(parts) == 2:                      # H:MM 或 MM:SS
            h, m = (int(x) for x in parts)
            return h * 60 + m if h >= 0 and 0 <= m < 60 else None
        if text.isdigit():                       # 纯分钟
            return int(text) if int(text) > 0 else None
    except Exception:  # noqa: BLE001
        pass
    return None


def _parse_article(article: dict, cfg: "Fc2CmDbConfig") -> dict:
    title = (article.get("title") or "").strip() or None
    release = (article.get("release_date") or "").strip() or None
    runtime = _norm_duration(article.get("duration"))
    writer = article.get("writer") or {}
    studio = (writer.get("name") or "").strip() if isinstance(writer, dict) else None
    tags = []
    for t in article.get("tags") or []:
        if not isinstance(t, dict):
            continue
        name = (t.get("name") or "").strip()
        if name and name not in tags:
            tags.append(name)
    if cfg.drop_writer_same_tag and studio:
        tags = [t for t in tags if t != studio]
    if cfg.max_tags > 0:
        tags = tags[: cfg.max_tags]
    cover = (article.get("image_url") or "").strip() or None
    if cover and _is_bad_image(cover):
        cover = None
    ext = article.get("video_id")
    return {
        "title": title, "release": release, "runtime": runtime, "studio": studio,
        "tags": tags, "cover": cover,
        "external_id": str(ext) if ext is not None else None,
    }


class _SessionGate:
    """进程级会话 / 浏览器互斥协调器。跨 provider 实例共享。

    职责（与 supfc2 / fc2ppvdb 同一份实现，逐字复用）：
    * **单浏览器**：同一时刻只放行一个浏览器，避免并发时 20 个 Chrome 抢同
      一个 profile。
    * **会话共享**：谁过完了登录，登录 cookie 进进程级缓存，大家都在用。
    * **失败冷却**：一次放行内全部尝试失败后，一段时间内不再重复起浏览器，
      把原因老实报上去（宁可一起快速失败，也不要 20 个 Chrome 把机器拖死）。
    """

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
        """把一条新会话放进进程缓存（谁过完登录，大家都看得见）。"""
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


class Fc2CmDbProvider(FilmSourceProvider):  # type: ignore[misc]
    """快路径：用缓存的登录 cookie 走 curl_cffi 直连详情页，解析出 MediaMetadata。

    本任务（T5）只实现「直连 + 解析」这一段；会话/浏览器的完整逻辑留给任务 6-7。
    """

    def __init__(self, context, config):
        self._ctx = context
        self._http = context.http_client
        self._cfg = config
        self._data_dir = Path(context.data_dir)
        self._debug_dir = self._data_dir / "debug"
        self._session: dict[Any, Any] = {}

    # ---------- 诊断：日志与存页，失败绝不干扰抓取 ----------

    def _log(self, line: str) -> None:
        if not self._cfg.debug_dump:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with (self._debug_dir / "debug.log").open("a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] {line}\n")
        except Exception:  # noqa: BLE001
            pass

    def _dump(self, name: str, text: str) -> None:
        if not self._cfg.debug_save_pages:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            (self._debug_dir / name).write_text(text or "", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # ---------- 会话 / 请求 ----------

    def _default_ua(self) -> str:
        return self._cfg.user_agent.strip() or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

    def _cookie_header(self):
        return (self._session.get("cookie_header") or "").strip()

    def _cookie_dict(self):
        out = {}
        for part in self._cookie_header().split(";"):
            if "=" in part:
                k, _, v = part.partition("=")
                if k := k.strip(): out[k] = v.strip()
        manual = (self._cfg.cookie or "").strip()
        if manual:
            for part in manual.split(";"):
                if "=" in part:
                    k, _, v = part.partition("=")
                    if k := k.strip(): out[k] = v.strip()
        return out

    def _headers(self):
        ua = (self._session.get("user_agent") or self._cfg.user_agent or "").strip()
        return {"User-Agent": ua or self._default_ua(),
                "Accept-Language": "ja,zh-CN;q=0.9,en;q=0.8"}

    def _read_disk_session(self):
        path = self._data_dir / "session.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        return data if isinstance(data, dict) and data.get("cookie_header") else None

    def _populate_session(self):
        """尽量把当前能用的会话装进 self._session：内存 → 磁盘；配置手填 cookie 作兜底种子。"""
        ttl = float(self._cfg.session_ttl)
        fresh = bool(self._cookie_header()) and time.time() - float(self._session.get("ts", 0)) <= ttl
        if fresh:
            return
        if self._cfg.cookie.strip():
            self._session = {"cookie_header": self._cfg.cookie.strip(),
                             "user_agent": self._cfg.user_agent.strip() or self._default_ua(),
                             "ts": time.time(), "manual": True}
            return
        disk = self._read_disk_session()
        if disk:
            self._session = disk

    def _write_session(self, session: dict[str, Any]) -> None:
        """原子落盘：先写临时文件再替换，避免别的进程读到半截 JSON。"""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            path = self._data_dir / "session.json"
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            self._log(f"会话写盘失败：{exc}")

    def _is_login_wall(self, html: str) -> bool:
        # R2 裁定：只在页面根本拿不到 Inertia data-page 的 article 时才判登录墙。
        # 未收录页同样有 article 节点（title 为空），那种情况交给 fetch 按
        # NO_USABLE_METADATA 处理，绝不能因缺 title/video_id 就误判成登录墙。
        return _article(html) is None

    def _session_key(self) -> str:
        """会话缓存的进程级 key：用 data_dir 隔离，不同源目录各算各的。"""
        return str(self._data_dir)

    def _profile_dir(self) -> Path:
        """持久化浏览器 profile 目录：登录态（cookie/登录记录）跨重启保留。"""
        return self._data_dir / "browser-profile"

    async def _try_reuse(self, url: str, cached: dict[str, Any]) -> dict | None:
        """等锁期间别人已把会话回填进进程缓存：用它的 cookie 再取一次正文。

        复用时**不再起浏览器**，并登记一次"省下的浏览器"（GATE.already_open）。
        若缓存里的会话已被踢下线（cookie 失效），返回 None，由调用方退回正常起
        浏览器的放行流程。
        """
        self._log("等锁期间拿到会话，不起浏览器（进程级缓存复用）")
        _GATE.note_reuse(spared=True)
        self._session = dict(cached)  # 装进当前实例，直连时用它的 cookie/UA
        html = await self._http.get_html(url, headers=self._headers(),
                                         cookies=self._cookie_dict())
        if self._is_login_wall(html):
            self._log("进程缓存会话已失效，仍需起浏览器")
            return None
        return {"html": html,
                "cookie_header": cached.get("cookie_header") or "",
                "user_agent": cached.get("user_agent") or self._default_ua(),
                "reused": True}

    async def _go_browser(self, url) -> dict:
        """起持久化 profile 的 Chrome 拿 auth + cookie；一次放行内按 browser_attempts 顺序重试。

        - 全程持有进程级互斥锁，同一时刻只放行一个浏览器。
        - 冷却期内（上一次整轮失败后 refresh_cooldown 秒内）不再重复起浏览器，
          直接把原因报上去——宁可一起快速失败，也不要把机器拖死。
        - 全部尝试成功后调用方拿到的 result 含 {html, cookies, cookie_header, user_agent…}。
        """
        cfg = self._cfg
        key = self._session_key()
        lock = _GATE.lock_for_current_loop()
        async with lock:
            # 双检：等锁期间别人可能已把登录态回填进进程缓存——这种情况下直接复用，
            # 不再起一个新的浏览器（"一批只起一个浏览器"的硬保证）。会话仍有效就复用，
            # 若缓存会话已失效则退回正常起浏览器的放行流程。
            cached = _GATE.session(key, float(cfg.session_ttl))
            if cached:
                reused = await self._try_reuse(url, cached)
                if reused is not None:
                    return reused
            left = _GATE.cooldown_left(key, float(cfg.refresh_cooldown))
            if left > 0:
                self._log(f"浏览器流程刚失败过，冷却中（还剩 {left:.0f}s）")
                raise SourceError(FailureReason.UNEXPECTED,  # type: ignore[misc]
                                  detail=f"{url}: 浏览器冷却中，稍后再试", url=url)
            profile = self._profile_dir()
            profile.mkdir(parents=True, exist_ok=True)  # 持久！登录态跨重启保留
            attempts = max(1, int(cfg.browser_attempts))
            last_error: SourceError | None = None
            for _ in range(attempts):
                try:
                    result = await asyncio.to_thread(
                        browser_auth_fetch, url, profile_dir=profile,
                        browser_path=cfg.browser_path.strip() or None,
                        headless=cfg.browser_headless,
                        login_timeout=float(cfg.login_timeout),
                        log_path=self._debug_dir / "chrome.log",
                        diag=[],
                    )
                except Exception as exc:  # noqa: BLE001
                    self._log(f"浏览器异常：{type(exc).__name__}: {exc}")
                    last_error = SourceError(FailureReason.NETWORK,  # type: ignore[misc]
                                             detail=f"{url}: 浏览器异常：{exc}", url=url)
                    continue
                if not result.get("ok"):
                    self._dump("loginwall.last.html", result.get("raw_html") or "")
                    last_error = SourceError(FailureReason.AGE_VERIFICATION,  # type: ignore[misc]
                                             detail=f"{url}: 弹出浏览器后未能在 "
                                                    f"{cfg.login_timeout:.0f}s 内登录",
                                             url=url)
                    continue
                _GATE.note_success(key)
                return result
            # 整轮全部失败：记冷却，再把最后一次的原因抛上去
            _GATE.note_failure(key)
            raise last_error if last_error else SourceError(
                FailureReason.AGE_VERIFICATION,  # type: ignore[misc]
                detail=f"{url}: 浏览器尝试耗尽", url=url)

    async def _browser_fetch_article(self, url) -> str:
        """快路径拿不到数据时，走持久化浏览器；成功则回填会话缓存并返回解析用 HTML。"""
        if not self._cfg.use_browser_fallback:
            raise SourceError(FailureReason.NO_USABLE_METADATA,  # type: ignore[misc]
                              detail=f"{url}: 未开浏览器兜底", url=url)
        result = await self._go_browser(url)
        session = {"cookie_header": result["cookie_header"],
                   "user_agent": result.get("user_agent") or self._default_ua(),
                   "ts": time.time()}
        self._session = session
        _GATE.publish(self._session_key(), session)
        self._write_session(session)
        self._log("已通过浏览器取得登录会话并回填 session.json")
        return result["html"]

    def _detail_url(self, digits: str) -> str:
        tpl = self._cfg.detail_template.strip() or "{base}/articles/{digits}"
        return tpl.format(base=self._cfg.base_url.rstrip("/"), digits=digits)

    async def _get(self, url: str, *, allow_retry: bool = True) -> str:
        for attempt in range(max(1, int(self._cfg.retries))):
            self._populate_session()
            try:
                text = await self._http.get_html(
                    url, headers=self._headers(), cookies=self._cookie_dict())
            except SourceError as exc:  # type: ignore[misc]
                if getattr(exc, "http_status", None) == 429:
                    raise SourceError(FailureReason.RATE_LIMITED,  # type: ignore[misc]
                                      detail=f"{url}: HTTP 429 限流", url=url)
                raise
            if self._is_login_wall(text):
                self._dump("loginwall.html", text)
                if allow_retry:
                    return await self._browser_fetch_article(url)  # 任务 7 接线
                raise SourceError(FailureReason.AGE_VERIFICATION,  # type: ignore[misc]
                                  detail=f"{url}: 登录墙（未登录或 cookie 失效）", url=url)
            return text
        raise SourceError(FailureReason.NETWORK,  # type: ignore[misc]
                          detail=f"{url}: 重试耗尽", url=url)

    # ---------- 抓取入口 ----------

    async def fetch(self, query, options=None):
        number = (getattr(query, "number", "") or "").strip()
        self._log(f"=== 开始 {number} data_dir={self._data_dir}")
        digits = _digits(number)
        if not digits:
            self._log(f"番号解析不出 FC2 数字：{number!r}")
            return None
        url = self._detail_url(digits)
        try:
            html = await self._get(url)
        except SourceError:  # type: ignore[misc]
            raise
        except Exception as exc:  # noqa: BLE001
            raise SourceError(FailureReason.NETWORK,  # type: ignore[misc]
                              detail=f"{url}: {type(exc).__name__}: {exc}", url=url)
        self._dump(f"{digits}.html", html)
        art = _article(html)
        if not art:
            raise SourceError(FailureReason.NO_USABLE_METADATA,  # type: ignore[misc]
                              detail=f"{url}: 页面里没有 article", url=url)
        if self._cfg.verify_number and str(art.get("video_id")) != str(digits):
            self._log(f"番号校验失败：video_id={art.get('video_id')} 期望 {digits}")
            raise SourceError(FailureReason.NO_USABLE_METADATA,  # type: ignore[misc]
                              detail=f"{url}: 取到的是别的番号", url=url)
        data = _parse_article(art, self._cfg)
        if not data["title"]:
            if self._cfg.report_misses:
                raise SourceError(FailureReason.NO_USABLE_METADATA,  # type: ignore[misc]
                                  detail=f"{url}: 没有标题（可能未收录）", url=url)
            return None
        return self._build_meta(number, digits, url, data)

    def _build_meta(self, number, digits, url, data):
        kwargs = {"number": number, "title": data["title"], "release": data["release"],
                  "runtime": data["runtime"], "studio": data["studio"],
                  "tags": data["tags"], "source_url": url, "external_id": digits}
        if self._cfg.include_cover and data.get("cover"):
            kwargs["poster_urls"] = [data["cover"]]
            kwargs["thumb_urls"] = [data["cover"]]
        meta = MediaMetadata(**kwargs)  # type: ignore[misc]
        self._log(f"命中 {number}: title={data['title']!r} release={data['release']} "
                  f"runtime={data['runtime']} studio={data['studio']} tags={len(data['tags'])}")
        return meta

class Plugin(FilmSourcePlugin):  # type: ignore[misc]
    config_model = Fc2CmDbConfig
    @classmethod
    def descriptor(cls):
        return SourceDescriptor(  # type: ignore[misc]
            id="unofficialscraper.fc2cmadb", name="FC2CMADB (fc2cmadb.com)", version="0.1.0",
            capabilities=frozenset({SourceCapability.FILM_METADATA}),
            content_types=frozenset({"fc2"}),
            urls=(BASE_URL,), rate_limit=1.0,
            metadata_fields=frozenset({"title","release","runtime","studio","tags",
                                        "poster_urls","thumb_urls","source_url","external_id"}),
        )
    def build(self, context, config):  # type: ignore[misc]
        return Fc2CmDbProvider(context, config)


# --------------------------------------------------------------------------
# 下面是内联的、只用标准库实现的 Chrome DevTools Protocol 客户端。
#
# 为什么不用 playwright / selenium：打包版 amane（PyInstaller）里装不了这些
# 第三方包，而 Cloudflare 的挑战**只有真实浏览器能过**（curl_cffi 任何指纹
# 都是 403，无头 Chrome 也会被识破）。所以只能自己拼 WebSocket 帧驱动本机
# Chrome。这段代码与 supjav / supfc2 插件里的那份是同一份实现（逐字复用）。
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


def looks_blocked(html: str) -> bool:
    low = (html or "").lower()
    return any(marker in low for marker in CHALLENGE_MARKERS)


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
        status_line = head.split(b"\r\n")[0]  # 3.10 的 f-string 不接受反斜杠，先取出来
        if b" 101 " not in status_line:
            raise WSError(f"握手失败：{status_line!r}")
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


# ---------- 交互式登录 / 抓 cookie（慢路径入口） ----------


def browser_auth_fetch(url, *, profile_dir, browser_path=None, headless=False,
                       proxy=None, login_timeout=180.0, log_path=None, diag=None) -> dict:
    """用持久化 profile 的 Chrome 打开 url，直到拿到含 article 的 data-page。

    返回 {"html", "cookies", "cookie_header", "user_agent", "url", "ok", "raw_html", "diag"}。
    - 若页面已登录 → 直接取数据。
    - 若未登录（登录墙）→ 保持窗口可见（不可无头/不可最小化），等用户手动登录，
      轮询 data-page 直到出现 article，再抓 cookie。
    """
    diag = diag if diag is not None else []
    with Browser(profile_dir, browser_path=browser_path, headless=headless,
                 proxy=proxy, log_path=log_path) as browser:
        diag.append(f"browser={browser.exe} headless={headless} port={browser.port}")
        cdp = browser.open_tab("about:blank")
        try:
            cdp.call("Page.enable", timeout=15)
            cdp.call("Runtime.enable", timeout=15)
            try:
                cdp.call("Page.addScriptToEvaluateOnNewDocument",
                         {"source": STEALTH_JS}, timeout=10)
            except Exception:  # noqa: BLE001
                pass
            cdp.call("Page.navigate", {"url": url}, timeout=30)
            PROBE = (
                "(() => { const el=document.querySelector('script[data-page=\"app\"]');"
                " if(!el) return JSON.stringify({ok:false,authed:false});"
                " try{const d=JSON.parse(el.textContent);"
                "   const a=(d.props&&d.props.article)||null;"
                "   const has=!!a;"
                "   const ok=has&& !!(a.title||a.video_id);"
                "   return JSON.stringify({ok:ok, authed:has,"
                "     article: has? el.textContent : null});"
                " }catch(e){ return JSON.stringify({ok:false,authed:false,err:String(e)});}"
                " })()"
            )
            deadline = time.time() + float(login_timeout)
            last_html = ""
            authed = False
            last_logged = 0.0  # R5：登录墙提示别每轮刷屏，>2s 才追加一条
            while time.time() < deadline:
                time.sleep(1.2)
                try:
                    state = cdp.evaluate(PROBE)
                except Exception:  # noqa: BLE001
                    continue
                try:
                    parsed = json.loads(state or "{}")
                except Exception:  # noqa: BLE001
                    continue
                if parsed.get("authed"):
                    # F1：article 节点出现即视为“已登录抓到页面”（无论 title 是否有值），
                    # 立即 break 并置本地 authed=True，避免对未收录页空转满 login_timeout。
                    try:
                        last_html = cdp.evaluate("document.documentElement.outerHTML") or ""
                    except Exception:  # noqa: BLE001
                        pass
                    authed = True
                    break
                now = time.time()
                if now - last_logged > 2.0:  # 已在登录墙：等用户登录，但别刷屏
                    diag.append("登录墙：等待用户在浏览器里登录…")
                    last_logged = now
            if not authed:
                diag.append(f"login_timeout 内未完成登录（url={url}）")
                # 兜底：就算没登录，也把手头页面抓下来，让调用方判断
                try:
                    last_html = cdp.evaluate("document.documentElement.outerHTML") or ""
                except Exception:  # noqa: BLE001
                    pass
            cookies = []
            try:
                cookies = cdp.call("Network.getCookies", {"urls": [url]}, timeout=15).get("cookies") or []
            except Exception:  # noqa: BLE001
                pass
            ua = None
            try:
                ua = cdp.evaluate("navigator.userAgent")
            except Exception:  # noqa: BLE001
                pass
        finally:
            cdp.close()
    header = "; ".join(f"{c.get('name')}={c.get('value')}" for c in cookies if c.get("name"))
    ok = authed and last_html and (_article(last_html) is not None)
    return {"html": last_html if ok else None, "ok": ok, "cookies": cookies,
            "cookie_header": header, "user_agent": ua, "url": url,
            "raw_html": last_html, "diag": diag}
