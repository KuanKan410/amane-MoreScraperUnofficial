"""esl1.fd2ppv 的单元测试（只测纯逻辑；浏览器相关靠实机验证）。"""


def test_descriptor_is_well_formed():
    desc = PLUGIN.Plugin.descriptor()
    assert desc.id == "esl1.fd2ppv"
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