"""unofficialscraper.fd2ppv 的单元测试（只测纯逻辑；浏览器相关靠实机验证）。"""

import asyncio

import pytest


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


# ---------- _get 编排（离线：脚本化 fake HTTP + stub 浏览器）----------
#
# _get 的退避重试 / 404 短路 / 挑战页识别 / 浏览器兜底「只兜一次」都不需要
# 真实网络。这里把 self._http 换成脚本化 fake，再 stub 掉 _browser_fetch_page。

CHALLENGE_HTML = "<html><head><title>Just a moment...</title></head></html>"


class _ScriptedGetHttp:
    """按预设序列返回响应；元素为 str 则返回，为 Exception 则抛出并记录次数。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def get_html(self, url, headers=None, cookies=None):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _StubGetBrowser:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def __call__(self, url):
        self.calls += 1
        return self.result


def _get_provider(tmp_path, responses, browser_result=None, request_retries=2):
    p = _provider(tmp_path, request_retries=request_retries)
    http = _ScriptedGetHttp(responses)
    browser = _StubGetBrowser(browser_result)
    p._http = http
    p._browser_fetch_page = browser
    return p, http, browser


def test_get_first_hit_requests_once_no_browser(tmp_path):
    p, http, browser = _get_provider(tmp_path, [DETAIL_HTML])
    text, err = _run(p._get("https://fd2ppv.cc/articles/4989610"))
    assert text == DETAIL_HTML
    assert err is None
    assert http.calls == 1
    assert browser.calls == 0


def test_get_http_404_short_circuits_no_retry_no_browser(tmp_path):
    p, http, browser = _get_provider(
        tmp_path, [PLUGIN.SourceError(http_status=404)]
    )
    text, err = _run(p._get("u"))
    assert text is None
    assert err is not None and "404" in err
    assert http.calls == 1              # 404 不重试
    assert browser.calls == 0            # 也不起浏览器


def test_get_challenge_triggers_browser_fallback_success(tmp_path):
    p, http, browser = _get_provider(
        tmp_path, [CHALLENGE_HTML], browser_result=DETAIL_HTML
    )
    text, err = _run(p._get("u"))
    assert text == DETAIL_HTML           # 返回浏览器兜底给的 HTML
    assert err is None
    assert http.calls == 1
    assert browser.calls == 1


def test_get_challenge_after_browser_fail_retries_pure_http_only(tmp_path):
    # 三次都是挑战页；浏览器兜底失败后，后续重试只能走纯 HTTP：
    # 断言浏览器兜底「恰好只被调用一次」（即使 request_retries > 1）。
    p, http, browser = _get_provider(
        tmp_path, [CHALLENGE_HTML] * 3, browser_result=None
    )
    text, err = _run(p._get("u"))
    assert text is None
    assert err is not None
    assert http.calls == 3               # 每次重试都还是发 HTTP
    assert browser.calls == 1            # 只兜底一次


def test_get_retries_on_transient_exception(tmp_path):
    p, http, browser = _get_provider(tmp_path, [RuntimeError("boom"), DETAIL_HTML])
    text, err = _run(p._get("u"))
    assert text == DETAIL_HTML
    assert err is None
    assert http.calls == 2               # 首次网络异常，第二次成功
    assert browser.calls == 0
