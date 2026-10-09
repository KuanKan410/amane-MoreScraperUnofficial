# esl1.supjav —— supjav.com 元数据插件（amane）

给 amane 加一个 supjav.com 的影片元数据源。支持 **有码 / 无码 / FC2** 三条内容路由。

* 插件 id：`esl1.supjav`
* 版本：**v0.2.1**（2026-09-27 在真机实测校准，不再是推断）
* 提供字段：标题、封面、缩略图、演员、标签、发行日期、片商、来源 URL
* 不提供：时长、剧照墙（站点详情页真的没有，不编造）

---

## 1. 站点是怎么工作的（全部为实测结论）

| 环节 | 结论 | 依据 |
|---|---|---|
| 详情页 URL | `https://supjav.com/{站点自增 id}.html`（例：`/302946.html`） | 实测抓取 |
| 能不能由番号直连 | **不能**。id 是站点自己的编号，与 `HMN-625` 无关 | 实测 |
| 抓取路径 | 因此固定走两跳：**站内搜索 → 详情** | — |
| 站内搜索 | `https://supjav.com/?s=<番号>`（WordPress 风格），语言前缀 `/zh/`、`/ja/` | 实测 |
| 列表项 | `div.posts > div.post` → `a.img[href][title] > img.thumb[data-original]`；标题在 `div.con h3 a` | 实测 |
| **发行日期** | 在列表项 `div.con div.meta` 的**第一个文本节点**（`2024/10/23`）；**详情页里没有日期** | 实测 |
| 详情页标题 | `div.video-wrap h1`（整页只有一个 h1；「You May Also Like」用的是 h2） | 实测 |
| 详情页封面 | `div#player-wrap` 的 `background-image: url(...)`，或 `div.post-meta img`；两处都是**原图** | 实测 |
| 片商 / 演员 / 标签 | 走 URL 路径：`/category/maker/`、`/category/cast/`、`/tag/` | 实测 |
| 分类 | `div.cats p.cat a` | 实测 |
| 列表封面 | 带 CDN 后缀 `!320x216.jpg`，插件会**剥掉 `!` 之后的部分**还原原图 | 实测 |
| 详情页元数据 | 无 `og:*`、无 JSON-LD、无时长、无剧照 | 实测 |
| FC2 详情页 | **没有 Cast 区块**（演员为空是正确的，不是抓漏了） | 实测 |

### 1.1 FC2 番号的搜索写法（关键）

站点索引里 FC2 不带 `-PPV-`，直接按番号搜会 0 结果：

| 查询 | 结果数 |
|---|---|
| `?s=FC2-PPV-4981113` | 0 ❌ |
| `?s=FC2PPV 4981113` | 1 ✅ |
| `?s=4981113` | 1 ✅ |
| `?s=FC2 4981113` | 1 ✅ |

插件遇到 FC2 番号会自动改写成 `FC2PPV <数字>`，仍搜不到再退回纯 `<数字>`。

### 1.2 amane 的 FC2 番号是 `FC2-<纯数字>`，站内标题是 `FC2PPV <数字>`

amane 在 FC2 路由里传下来的番号是 **`FC2-3125926`**（没有 `PPV`），而 supjav 的标题
一律写成 **`FC2PPV 3125926 …`**。v0.2.0 之前番号校验正则是 `FC2[-_\s]*3125926`，
中间的 `PPV` 匹配不上，于是**明明搜到了条目却被判成错配**，`debug.log` 里表现为
「命中 0 条」。

v0.2.1 起，FC2 的 `FC2` / `PPV` 前缀在匹配时**全部视为可选**（FC2 的身份信息只在
那串数字上），下面这些写法都算命中：

```
FC2PPV 3125926   FC2-PPV-3125926   FC2 3125926   FC23125926   3125926
```

实测：`FC2-3125926` → `/190608.html`，`release=2022-12-07`，`studio=FC2PPV`。

### 1.2 ⚠ Cloudflare 托管挑战（为什么 v0.1.0 "找不到"）

整站挂在 Cloudflare **managed challenge** 后面。实测结论：

| 手段 | 结果 |
|---|---|
| `curl_cffi` 任意指纹（chrome / chrome131 / chrome136 / safari / firefox / edge…） | **403 `Just a moment...`** |
| 无头 Chrome（`--headless=new`，即使改 UA + 注入反自动化脚本） | **仍然被拦**（返回「请稍候…」） |
| **有头 Chrome，起来后立刻最小化窗口** | **通过** ✅ 拿到 `cf_clearance` |

`cf_clearance` 与 **出口 IP + 浏览器 UA 绑定**：换 UA 用同一个 cookie 照样 403，
所以插件把 cookie 和 UA **成对**保存、成对使用。

流程：

1. 正常 HTTP 请求；
2. 撞到挑战（403，或 HTTP 200 但正文是挑战页）→ **临时拉起本机 Chrome**（一次性临时 profile，CDP 驱动，起来即最小化），过一次挑战；
3. 拿到 `cf_clearance` → 写入 `cf_session.json`，**本次请求不消耗重试次数**，直接带上 cookie + 同一 UA 重试；
4. 之后 `session_ttl`（默认 1500 秒）内所有请求复用该会话，**不再启动浏览器**。

插件里的 CDP 客户端是**纯标准库手写**的（socket + struct + base64 + subprocess，自己拼 WebSocket 帧）——
打包版 amane 里装不了 `playwright` / `selenium` 这类第三方依赖。

---

## 2. 安装 / 升级

1. 目录：`%LOCALAPPDATA%\Amane\plugins\sources\esl1.supjav\plugin.py`（目录名必须与 id 一致）
2. amane → **管理 → 插件 → 重新扫描**
3. **把 `esl1.supjav` 加进内容路由**（不加的话插件不会被调用）：

   ```toml
   [scraping.content_routes]
   censored   = ["iqqtv", "dmm", "javdb", "javbus", "jav321", "esl1.supjav"]
   uncensored = ["iqqtv", "javdb", "javbus", "avsox", "esl1.supjav"]
   fc2        = ["esl1.fc2cmadb", "esl1.javarchive", "esl1.supjav"]
   ```

   v0.2.1 的 `content_types` 已包含 `fc2`，所以现在**能**被加进 fc2 路由了（这是本次修的问题之一）。

4. 首次刮削会弹一次浏览器窗口（约几秒，自动最小化）。拿到会话后的后续请求是纯 HTTP，很快。

---

## 3. 设置项（全部可在 amane 面板里改，共 41 项）

**常用**

| 字段 | 默认 | 说明 |
|---|---|---|
| `lang_path` | 空 | 留空 = 英文页；填 `zh` / `ja` 走对应语言版（标题语言随之变化，番号不受影响） |
| `search_path` | `/?s={q}` | 站内搜索路径 |
| `verify_number` | 开 | 候选正文里必须出现该番号才认，防止搜错 |
| `accept_search_result` | 开 | 详情页抓不动时用搜索结果的「标题+封面+日期」返回最小元数据 |
| `list_thumb_fallback` | 开 | 详情页没封面时用列表封面顶上 |
| `article_preference` | `4k, chinese, chn sub, subtitle, uncensored, reducing` | 同番号多版本时按此顺序挑版本（无方括号前缀的版本优先） |
| `strip_title_prefix` | **开** | 剥掉标题开头的 `[4K]` / `[REDUCING MOSAIC]` 这类方括号前缀 |
| `tags_drop` | `supjav,hd,video,jav` | 要剔除的标签，逗号分隔 |

**选择器**（默认已按实测结构校准，站点换皮时在面板里改即可）

| 字段 | 默认值 |
|---|---|
| `title_selector` | `div.video-wrap h1::text, div.archive-title h1::text, div.post-meta h2::text, h1::text` |
| `cover_selector` | `div#player-wrap::attr(style), div.post-meta img::attr(src), div#player-wrap img::attr(src), meta[property='og:image']::attr(content)` |
| `release_selector` | **空** —— 详情页没有日期，留空即可，日期从搜索列表取 |
| `maker_selector` | `div.cats a[href*='/maker/']::text, div.cats a[href*='/studio/']::text` |
| `cast_selector` | `div.cats a[href*='/cast/']::text, div.cats a[href*='/actress/']::text` |
| `tags_selector` | `div.tags a[href*='/tag/']::text, div.cats a[href*='/tag/']::text` |
| `category_selector` | `div.cats p.cat a::text` |
| `extras_selector` | 空（本站只有一张封面，没有剧照墙） |

**Cloudflare / 浏览器**

| 字段 | 默认 | 说明 |
|---|---|---|
| `browser_fallback` | 开 | 撞挑战时拉起本机 Chrome。关掉则只会报 `cloudflare_challenge` |
| `browser_path` | 空 | 留空自动找 Chrome / Edge；找不到时手工填 exe 路径 |
| `browser_headless` | **关** | 无头模式实测过不了挑战，保持关闭 |
| `browser_proxy` | 空 | 留空沿用 amane 的代理设置 |
| `challenge_timeout` | 45.0 | 单次过挑战的等待上限（秒） |
| `session_ttl` | 1500 | `cf_clearance` 复用时长（秒），过期才重新起浏览器 |
| `cookie` | 空 | 手工粘贴 Cookie 时用（优先级高于磁盘会话）；**必须和 `user_agent` 配套** |
| `user_agent` | 空 | 手工指定 UA；留空则用会话里保存的那个 |
| `browser_profile` | 空 | 留空用一次性临时 profile；填路径则复用固定 profile（能延长会话寿命，但会被站点记住指纹） |

**诊断**

| 字段 | 默认 | 说明 |
|---|---|---|
| `debug_dump` | 开 | 写 `debug/debug.log` |
| `debug_save_pages` | 开 | 页面落盘（校准选择器时把整个 debug 目录发给开发者） |
| `cache_resolved` | 开 | 命中一次后写 `resolved.json`，下次跳过搜索直达详情页 |
| `index_file` | 开 | 支持手写 `debug/index.csv`（每行 `番号,详情页URL`） |
| `report_misses` | 开 | 找不到时抛带原因的错误而不是静默返回空 |

---

## 4. 排错

调试产物：

```
%LOCALAPPDATA%\Amane\plugins\esl1.supjav\
    debug\debug.log              # 每一步 + 每个选择器的命中数
    debug\<番号>.*.html          # 落盘的原始页面
    debug\cloudflare.last.html   # 最后一次过挑战拿到的页面（过没过一目了然）
    cf_session.json              # cf_clearance + UA 会话
    browser-profile\             # 浏览器临时 profile
    import-report.txt            # 插件基类导入诊断
```

`debug.log` 里两条关键行：

```
过挑战成功：cookies=['cf_clearance'] cf_clearance=有 ...
命中 HMN-625: title='HMN-625 ...' release=2024-10-18 runtime=None studio=Honnaka tags=7 actors=1 poster=1
```

**排查顺序**

1. `debug/debug.log` —— 先看走到了哪一步、选择器命中数全是 `=0` 说明站点换皮。
   ⚠️ 如果看到「命中 0 条」但同一行写着「页内详情页链接共 N 条」（N>0），
   那是**番号校验把搜到的条目判成错配了**，把那个 `<番号>.search.html` 发给开发者。
2. 一直卡挑战 → 确认本机装了 Chrome / Edge；看 `debug/cloudflare.last.html`，
   如果是「请稍候…」说明没过（多半是浏览器被后台限速），可把 `browser_headless` 关掉、
   把 `challenge_timeout` 调大，或填 `browser_path` 指定到能正常上网的那个浏览器。
3. 任务报告的 `reason`：
   * `cloudflare_challenge` / `cloudflare_blocked` / `ip_banned` → 换出口 IP 或代理
   * `parse_error` → 页面结构变了，按命中数改选择器
   * `timeout` → 调大 `network.timeout` / `challenge_timeout`
4. 加载失败（报错提到 `FilmSourcePlugin`）→ 看 `import-report.txt`。

---

## 5. 手工兜底索引

个别番号站内搜索搜不到时，可以写死 URL：

```
%LOCALAPPDATA%\Amane\plugins\esl1.supjav\debug\index.csv
# 番号,详情页URL
HMN-625,https://supjav.com/302946.html
```

---

## 6. 开发用

```bash
python selftest.py     # 23 项，全部通过（离线、不需要 amane）
```

夹具是**真实抓取的页面**（搜索页 / 详情页 / FC2 详情页 / 空结果页 / 挑战页 / 带 JSD 脚本的真页面），
不是替身。改动选择器后跑一遍即可确认没打歪；挑战链路用 monkeypatch 替换 `browser_fetch` 验证。
