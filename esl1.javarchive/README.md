# javarchive.com FC2 兜底插件（amane）

amane 的**第三方影片元数据来源插件**，把 [javarchive.com](https://javarchive.com/)
接成 FC2 番号的兜底源——当 `javdb / fc2ppvdb / fc2 / freejavbt` 全部失手时，
至少把 **封面 + 标题 + 剧照 + 分类 + 发布日期 + 时长** 补上。

* 插件 ID：`esl1.javarchive`
* 版本：**0.7.0**
* 声明类型：`content_types = {fc2, censored, uncensored}`
* 提供字段：`title` / `tags` / `release` / `runtime` / `poster_urls` / `thumb_urls` / `extrafanart` / `source_url`

---

## 零、0.7.0：支持有码 / 无码路由

实测 javarchive 不只收录 FC2：搜索 SSIS-001 可得到同一番号的多个版本——

| slug 标记 | 含义 |
|---|---|
| 无标记 | 原版 |
| reducing-mosaic | モザイク破壊（马赛克消减） |
| uncensored / leaked | 无码流出 |
| 6000kbps / fhd / encode720p | 高清转码 |
| -4k- / 4k- | 4K 版 |
| -summary- | 摘要剪辑版 |
| engsub / chinese-sub | 英/中字幕 |

插件通过 query.content_type 知道当前内容路由，按下面顺序选版本：

| 路由 | 优先级（从左到右） |
|---|---|
| censored（有码） | 原版 → reducing mosaic → 4K/FHD → 无码流出 → 转码/字幕 |
| uncensored（无码） | 无码流出 → reducing mosaic → FHD/4K → 转码/字幕 → 原版 |
| fc2 | 原有行为（原版优先） |

文件名后缀微调（番号之后）：-U 提升无码流出版；-HD / -4K 提升高清版。
目标版本不存在时自动落回该路由的默认优先级，不会因此无结果。

> 注意：命中的文章会按番号缓存；换路由或加 -U/-HD 后缀后想重选版本，需在 Amane 里清除该条缓存/重新刮削。

> 标记按子串识别，slug 自带 1080p/720p 等字样时可能被归为转码版；这只影响版本排序，不会匹配到别的番号。

---

## 一、结论先行：v0.6.0 老文章刮不到封面（FC2-597145）

用户报告 FC2-597145 刮不到封面。翻 debug 目录里 **212 篇真实文章页**做统计，
发现**站点有两套文章模板**，v0.5.0 只认了新的那套：

| 模板 | 封面位置 | 图床 | 命名 |
|---|---|---|---|
| **新文章** | `div.fisrst_sc img`（站点把 `first` 拼错了） | `img.javstore.net` | `{番号}pl.jpg` / `ps.jpg` |
| **老文章**（多为 2017 年前后） | **没有 `fisrst_sc`**，在 `div.Recipepod img[itemprop='image']` | `img3.javarchive.com` | `FC2PPV{番号}.jpg` |

`div.Recipepod` 是站点滥用 schema.org/Recipe 的容器，v0.3.0 曾判定它"没有可用
信息"——**其实它的 `itemprop="image"` 恰恰是唯一跨模板稳定的封面位置**。

同一轮统计还暴露第二个问题：**`.gif` 被无条件拉黑**。212 篇里有 7 篇封面**本身就
是 GIF 动图**（`1423962pl.gif`、`FC2PPV-3260300.gif`），而当初拉黑 GIF 想挡的装饰
动图（`/assets/top-view-3.gif`）其实已被黑名单的 `/assets/`、`top-view` 挡掉了。

### 真实样本回归（212 篇，非打桩）

| 版本 | 封面命中 | 命中率 |
|---|---|---|
| v0.5.0 | 168 / 212 | 79.2% |
| **v0.6.0** | **212 / 212** | **100%** |

本次修好 44 篇。597145 现在能取到
`http://img3.javarchive.com/images/2017/07/05/FC2PPV597145.jpg`。

**一个被否掉的"优化"**：不要加"封面 URL 必须含番号"的过滤。统计里有 18 篇封面
的文件名是时间戳 / 哈希（`1646037558.9.gif`、`3eC2ebcE64Fc7174pl.jpg`），不含番号
但**确实是正确封面**；加这条过滤会误杀 17 篇，只换来修好 1 篇站点自己存错图的。

另注：老模板文章同样没有剧照区和正文「日期：」行，这类番号只有
标题 + 封面 + 面包屑分类，属站点本身的数据缺失。

> v0.5.0 的 javstore 原图 404 自动复活、v0.4.0 的剧照 / 发布日期补齐，
> 见下面各自的章节。

v0.4.0 之后又发现一个隐性 bug：**javstore.net 部分原图会 404**。javarchive 文章页 HTML
仍指向那个 URL，但实际访问不到。用户实测案例：

```
原图 : https://img.javstore.net/images/2022/07/30/FC2PPV-3061625.jpg  → 404
-2   : https://img.javstore.net/images/2022/07/30/FC2PPV-3061625-2.jpg → 200
```

这是 javstore 的"派生图保留、原图清理"现象（估计是上游 CDN / storage GC 触发的），
不止 3061625 一例。v0.5.0 加 `cover_javstore_resurrect` 配置 + `_javstore_resurrect`
自动复活：

1. `fetch` 流程在 `_parse` 之后、构造 MediaMetadata 之前对封面 URL 做 HEAD 探测。
2. 只对 `*.javstore.net` 域生效（FC2/DMM 等 CDN 各自命名规则不同，不能瞎套）。
3. 探测到 404 时按 `-2` / `-3` 后缀再各 HEAD 一次；命中即替换原 URL；全失败保留原 URL。
4. 单次 HEAD 默认 4s 超时（`cover_resurrect_timeout`），最坏 ~12s。
5. 走 amane 的线程池（`asyncio.to_thread` 兼容实现 `_to_thread`），不阻塞事件循环。
6. 关掉 `cover_javstore_resurrect` 即可回到 v0.4.0 行为。

**默认开**——大多数情况下是"白做一次 HEAD"（< 0.5s），但少数 javstore 原图被删的
情况下能自动救回封面，不让用户手动改 URL。

v0.4.0 还顺手补齐了剧照、发布日期、真标签——见下表与下面章节。

### 真实网络实测结果

| 番号 | 标题 | 封面 | 剧照 | 日期 | 时长 | 备注 |
|---|---|---|---|---|---|---|
| FC2-1793751 | ✅ | ✅ | ✅ 6 张 | ✅ 2021-05-17 | — | v0.4.0 测试集 |
| FC2-4981113 | ✅ | ✅ | ✅ 6 张 | ✅ 2026-09-21 | ✅ 1h18m | v0.4.0 测试集（带剧照区的典型） |
| FC2-3061625 | ✅ | ✅ **复活** | — | — | — | v0.5.0：原图 404 → 自动换 -2 后缀 |
| FC2-597145 | ✅ | ✅ **老模板** | — | — | — | v0.6.0：老文章经 `Recipepod` 取到封面 |
| FC2-1423962 | ✅ | ✅ **GIF** | — | — | — | v0.6.0：封面本身是 GIF 动图 |
| FC2-1234567（不存在）| — | — | — | — | — | 正确抛错（带逐源原因），不静默 None |

### 选择器对照：v0.3.0 → v0.6.0

| 字段 | v0.3.0 | v0.4.0 | v0.5.0 | v0.6.0 | 实测值（4981113） |
|---|---|---|---|---|---|
| 标题 | `h1 a, h1, .first_des` | 同 | 同 | 同 | `FC2-PPV-4981113 新作。ようやく…` |
| 封面 | `… .news img:first-of-type`（**有剧照后会错抓剧照当封面**） | 收紧到 `.fisrst_sc` 系 | 同 + javstore 404 复活 | **再 + `.Recipepod img[itemprop='image']` 兜底 + 允许图床 GIF** | `img2.javstore.net/.../4981113pl.jpg` |
| 剧照 | — | `ul#lightgallery li, ul.highslide-gallery li` | 同 | 6 张 FC2 w1280 缩略图 |
| 标签 | 只取面包屑 | 面包屑 + `.news` 文本的 `标签：xxx｜yyy` | 同 | `[AV Uncensored, ハメ撮り, 素人, 中出し, 個人撮影]` |
| 日期 | **留空**（"本站没有"） | `.news` 文本的 `日期：YYYY/MM/DD` | 同 | `2026-09-21` |
| 时长 | — | `.news` 文本的 `时长：HH:MM:SS` | 同 | `4684s`（= 1h 18m 4s） |

---

## 二、v0.5.0 新坑：javstore 原图 404

### 1. 复活范围只在 javstore.net

`*.javstore.net` 是 javarchive 几乎所有封面的图床。**FC2 (`contents-thumbnail2.fc2.com`)、
DMM (`pics.dmm.co.jp`) 等其他 CDN 不复活**——它们各自有不同命名规则，瞎套反而会改坏。

### 2. 不在原 URL 后追加 `-1`

按 `-2`、`-3` 顺序试。`-1` 可能是原图副本或别的衍生图，跟井号逻辑无关，跳过不凑热闹。

### 3. 已带 `-N` 后缀的 URL 不再套

`.../FC2PPV-3061625-2.jpg` 已经带 -N 后缀就不重新探测。避免给`-1.jpg`再套一层变成`-2.jpg`。

### 4. 默认 HEAD 超时 4s × 3 次 = 最坏 12s

正常情况是"原图 200，一次 HEAD 就走"，< 0.5s。少数原图 404 + 复活 -2/-3 也失败，
会花完整超时（~12s），日志会有"封面复活失败"。

### 5. 不走 amane 代理栈

`urllib.request` 走的是系统默认栈，不走 `context.http_client`。javstore 是公开 CDN，
通常直连即可；如果你环境的 amane 配了强制代理而 stdlib urllib 不走，HEAD 会失败——
那 `cover_javstore_resurrect` 设为 `false` 即可，行为退化到 v0.4.0。

---

## 三、v0.4.0 的坑（仍在生效）

### 1. `.news img:first-of-type` 不能再当封面兜底

v0.3.0 的封面兜底是 `.news img:first-of-type`——但 v0.4.0 加了剧照区后，
`div.news` 里的第一张图就变成 lightgallery 的剧照，会把剧照当封面。
v0.4.0 把封面选择器收紧到 `.fisrst_sc` 系（`div.fisrst_sc img:first-of-type` /
`.news .fisrst_sc img` / `.fisrst_sc img`）——`.fisrst_sc` 不存在就让 `og:image` 接手。

### 2. 剧照数量受 `gallery_max` 限制

默认值 24 张。javarchive 抽样看到的最多一篇是 8 张，但**站点某天塞 100 张进来插件不会卡住**，
只取前 N 个写进 metadata。

### 3. `body_release` / `body_runtime` / `body_tags` 都有独立开关

正文文本里这些字段的格式以后可能被改，关掉就是回到 v0.3.0 的兜底逻辑。
生产环境**默认全开**就行，selftest 已经覆盖了"只关 body_release / release_from_cover_path
才起作用"这条路径。

### 4. `MediaMetadata.extrafanart` / `runtime` 字段名变化兼容

v0.4.0 写 `extrafanart` / `runtime` 之前会先查 `MediaMetadata.model_fields`
（兼容 Pydantic v1 `__fields__`）。如果宿主 SDK 旧版本没这两个字段，**插件自动跳过**，
不会让 SDK 旧版本崩。

---

## 三、四个老坑（v0.3.0 沿用至今）

### 1. `h1::text` 取不到标题

正文标题是 `<h1><a href="…">标题</a></h1>`，`::text` 只取**直接子文本节点**，
所以 `h1::text` 返回空。选择器必须写 `h1 a`（或 `h1 ::text`）。
插件内部做了三级退化：`::text` → 后代文本 ` ::text` → `::attr(title)`。

### 2. 第一张图不是封面

文章页 `<img>` 顺序是：站点 logo（`/upload/setting/logojavarchive2.png`）→
广告 banner（728×90 的 gif/jpg）→ 侧栏别人的缩略图 → 才是封面。
插件带图片黑名单（`favicon` / `logo` / `/upload/setting/` / `/assets/` / `banner` / `.gif`），
所以 `og_image_fallback` 打开也不会把 `/favicon.png` 当封面。

### 3. `span.news_date` 是别人的日期

v0.3.0 文档原话："这是侧栏里别人文章的日期"。**v0.4.0 不再用它**——日期改走
`div.news` 文本里的 `日期：YYYY/MM/DD`，更准。

### 4. 站内搜索返回**相对链接**

结果是 `/731705-fc2-ppv-1793751-…-pn.html`，必须 `urljoin`。
而且**每个页面都带一块「最新文章」**（站内搜索页上多达 200+ 条 `-pn.html` 链接），
所以不能"有链接就当结果"——必须靠 slug 里的 `fc2ppv<番号>` 来筛。

---

## 四、安装

1. 把 `plugin.py` 放到 amane 数据目录的插件目录下（**只需这一个文件**）：

   ```
   %LOCALAPPDATA%\Amane\plugins\sources\esl1.javarchive\plugin.py
   ```

2. amane →「管理 → 插件」→ 重新扫描 → 启用 **javarchive.com (FC2 兜底)**。

3. amane →「设置 → 影片刮削 → 内容路由 → FC2」，把 `esl1.javarchive`
   加到列表**末尾**（它是兜底源，别放前面）。

由于 0.7.0 开放了有码 / 无码路由，若想让有码 / 无码片子也走 javarchive 兜底，
可在内容路由中补充 `esl1.javarchive`（同样放各自列表末尾），示例：

```toml
[scraping.content_routes]
fc2        = ["esl1.fc2cmadb", "esl1.javarchive"]
censored   = ["dmm", "javdb", "javbus", "esl1.javarchive"]
uncensored = ["javdb", "javbus", "avsox", "esl1.javarchive"]
```

> 兜底原则不变：`esl1.javarchive` 永远放列表**末尾**，别排到主源前面。

不需要改任何代码，所有旋钮都在「管理 → 插件」的配置面板里。

---

## 五、验证

### 1. 逻辑自测（不需要 amane、不需要联网）

```bash
python selftest.py
```

**30 项**断言：18 项 v0.4.0 留下的（含 4981113 真实 HTML 提炼的剧照区 + 正文文本）
+ 8 项 v0.5.0 的 javstore 复活分支（原图 404 → -2/-3 命中、配置关、已带后缀、
非 javstore 域、网络异常等）
+ 4 项 v0.6.0 新增（老模板经 `Recipepod` 取封面、`fisrst_sc` 大图优先于兜底图、
GIF 策略单测、GIF 封面端到端开关）。依赖：`pip install pydantic parsel`。

> 夹具之外还有一层**真实样本回归**：把 debug 目录里 212 篇真实 `*.article.html`
> 喂给 `_first_image` 跑命中率。改选择器后务必重跑（见下面「四、验证」）。

### 2. 真实网络冒烟测试

```bash
python livetest.py FC2-1793751
python livetest.py --proxy http://127.0.0.1:7890 FC2-1793751   # 走代理
python livetest.py --no-search-engine FC2-1793751              # 只测站内搜索
```

它用真实网络跑完 plugin.py 的整条链路，打印结果并把页面落盘到 `livetest-out/`。
**这是排查"装到 amane 里却没结果"的第一手段**——如果它能通而 amane 不通，
问题在你的代理或 amane 的配置，不在插件。

> 注意：本脚本用 urllib / curl_cffi，amane 用 curl_cffi 浏览器指纹。
> 所以本脚本的结果是**下限参考**：它通了 amane 通常也通；它被 Cloudflare 拦，
> amane 里未必被拦（amane 指纹更好）。

### 3. 在 amane 里跑完之后

先看这个文件，它会逐条告诉你走了哪条路、每个选择器命中几个节点：

```
%LOCALAPPDATA%\Amane\plugins\esl1.javarchive\debug\debug.log
```

正常输出长这样：

```
站内搜索 q='1793751' -> 228 条文章链接
站内搜索命中：https://javarchive.com/731705-fc2-ppv-1793751-…-pn.html
选择器 title: h1 a=1, h1=1, .first_des=1
选择器 cover: div.fisrst_sc img:first-of-type=1, .news .fisrst_sc img=1, .fisrst_sc img=1
选择器 gallery: ul#lightgallery li=6, ul.highslide-gallery li=6
选择器 tags: .news_pro_tag a=0
选择器 category: ul[itemtype*='Breadcrumb'] a span[itemprop='title']=2
剧照：6 张
正文 meta：tags=4 release='2021-05-17' runtime=4684
命中：title='FC2 PPV 1793751 …' cover=有 剧照=6 release='2021-05-17'
```

同目录下还有 `{番号}.search.site.html` 和 `{番号}.article.html` 原始页面，
选择器失配时直接对着它改。

---

## 六、配置项

| 字段 | 默认 | 说明 |
|---|---|---|
| `site_search` | `true` | 站内搜索，**保持开启**。这是本站唯一可靠入口 |
| `site_query_template` | `{digits}` | 站内搜索查询串。纯数字结果最干净 |
| `search_engines` | `duckduckgo,duckduckgo_lite,mojeek,bing,google` | 仅站内搜索 0 条匹配时才用 |
| `search_use_proxy` | `true` | 外部搜索引擎走不走代理。**弹验证码多半是出口 IP 脏**，换一边试 |
| `searxng_base` | 空 | 自建 SearXNG 基址，填了就在 `search_engines` 里加 `searxng`（走 JSON，不弹验证码） |
| `index_file` | `index.csv` | 手工索引，每行 `FC2-PPV-xxxxxxx,https://javarchive.com/...` |
| `cache_resolved` | `true` | 命中过的 番号→URL 写进 `resolved.json`，同一部片只查一次 |
| `url_must_contain_number` | `true` | 要求 slug 里带 `fc2ppv<番号>`，挡掉串台结果 |
| `title_must_contain_number` | `true` | 标题必须含番号，否则丢弃（防写错数据） |
| `article_retries` | `2` | 只对 CF / 5xx / 超时重试（本站会间歇性返回 CF Error 1034） |
| `title_selector` | `h1 a, h1, .first_des` | |
| `cover_selector` | `div.fisrst_sc img:first-of-type, …` | v0.4.0 **不再**用 `.news img:first-of-type` 兜底，会把剧照当封面 |
| `category_selector` | `ul[itemtype*='Breadcrumb'] a span[itemprop='title']` | 分类来源；`Home` 自动剔除 |
| `tags_selector` | **空** | 见下 |
| `release_selector` | **空** | 见下 |
| `release_from_cover_path` | `false` | 用封面路径日期，有偏差，默认关 |
| `og_image_fallback` | `true` | 退到 og:image（自动跳过 favicon/logo） |
| `cover_allow_gif` | `true` | **v0.6.0 新增**。约 3% 的文章封面本身是 GIF 动图，开启后接受图床 `/images/` 下的 GIF；装饰动图与广告图仍被挡。关掉 = 回到「GIF 一律不要」 |
| `cover_javstore_resurrect` | `true` | **v0.5.0 新增**。javstore 部分原图 404（FC2-3061625 实测），自动加 `-2`/`-3` 后缀复活；网络/超时失败保留原 URL |
| `cover_resurrect_timeout` | `4.0` | **v0.5.0 新增**。单次 HEAD 超时（秒）。最坏 ~12s（原 + -2 + -3）|
| `gallery_selector` | `ul#lightgallery li, ul.highslide-gallery li` | **v0.4.0 新增**。剧照区 |
| `gallery_max` | `24` | **v0.4.0 新增**。剧照最大张数 |
| `body_tags` | `true` | **v0.4.0 新增**。从 `.news` 文本抽真标签 |
| `body_release` | `true` | **v0.4.0 新增**。从 `.news` 文本抽发布日期（比 `release_from_cover_path` 准） |
| `body_runtime` | `true` | **v0.4.0 新增**。从 `.news` 文本抽时长（秒） |
| `debug_dump` | `true` | 写 `debug/debug.log`，**排查先看它** |
| `debug_save_pages` | `true` | 把抓到的页面落盘成 HTML |
| `report_misses` | `true` | 找不到时抛错（带原因），而不是静默 `None` |

### 为什么 `tags_selector` 和 `release_selector` 默认是空的

* **标签**：本站 `news_pro_tag a` 是把**标题拆成词**，不是题材标签。
  唯一干净的标签来源有两个：①面包屑分类 → `['AV Uncensored']`；②v0.4.0 新增的
  `div.news` 文本里 `标签：xxx｜yyy` 这一行。默认走 ① + ② 两条路。
* **日期**：v0.3.0 时代文档说"本站没有"，**这是错的**——只对了一半。
  v0.4.0 起默认走 `body_release=True`，从正文文本里抽 `日期：YYYY/MM/DD`。
  想恢复 v0.3.0 的行为就把 `body_release=False` + `release_from_cover_path=False`。

---

## 七、已知限制

* **没有演员信息**。`span[itemprop='name']` 是作品标题，`div.Recipepod` 是滥用
  schema.org/Recipe 的容器。所以 `metadata_fields` 里不含 actor——
  这也意味着它**补不了 fc2ppvdb 挂掉后缺失的演员字段**。
* **剧照不是每篇都有**。没剧照区的文章 `extrafanart=[]`，amane 那侧就不会显示剧照墙
  （这是预期行为，不是 bug）。
* **文章 URL 拼不出来**。`{postid}-FC2-PPV-{番号}-pn.html` 里的 `postid` 是站点
  自增号（约 7 位数），与番号无可推导关系。所以必须靠站内搜索成功；
  站内搜索失败时只能用 `index.csv` 手工喂 URL。
* **同一部片可能有多篇**（实测 1793751 有原帖 + `[KBJ]` 转发），插件取第一条。
* **封面命名不统一**：`{番号}ps.jpg` / `{番号}pl.jpg` / `.png` / `.jpeg` 都有，
  且 http/https 混用（插件会统一升到 https，实测两种协议返回同一张图）。
* **站内搜索页很"肥"**：一次返回 200+ 条链接（含「最新文章」块），
  靠 slug 匹配筛选，所以 `url_must_contain_number` 不建议关。

---

## 八、排错

| 现象 | 先看哪里 |
|---|---|
| **amane 扫描后报 `plugin.py must define a FilmSourcePlugin or PlaybackPlugin subclass named Plugin`** | 看下面的"插件加载失败"专项诊断 |
| amane 里 `no data found` | `debug/debug.log` 是否有「站内搜索命中」 |
| 日志停在「站内搜索」 | 站点被墙/被 CF 拦，或 amane 的代理不通 → 试 `livetest.py --proxy …` |
| 「选择器 title: …=0」 | 站点改版了，对着 `debug/*.article.html` 改选择器 |
| 「剧照：0 张」但 HTML 里有 `ul#lightgallery` | 站点改了 class/id，对着 article.html 改 `gallery_selector` |
| 「剧照：6 张」但 amane 没显示剧照墙 | 宿主 `MediaMetadata` 没有 `extrafanart` 字段——升级 amane SDK，或自己补字段 |
| 封面加载失败 / 浏览器显示 404 | **v0.5.0**：javstore 原图被删，配置默认会自动复活 → `debug.log` 看是否有「封面复活：… → …-2.jpg」；如果全失败 → 设 `cover_javstore_resurrect=false` 退回 v0.4.0，或自己改 `index.csv` 喂正确 URL |
| 「标题不含番号，丢弃」 | 选了别的片，检查 `url_must_contain_number` |
| 全部引擎被拦 | 换出口 IP；或填 `searxng_base`；或用 `index.csv` 手工喂 |
| 想确认是插件问题还是环境问题 | 跑 `livetest.py`，它通了就说明是环境/配置问题 |

### 插件加载失败专项（`must define a FilmSourcePlugin ...`）

这是 amane **加载阶段**的报错：插件代码本身正确，但基类身份对不上。
排查三件套全已自带在该目录：
* **`import-report.txt`** —— 每次被 amane 加载都自动生成；告诉你 amane 拿到的是哪个 `FilmSourcePlugin`（见第 8 行）
* **`regression.py`** —— 子进程里手工构造"两条路径同时存在、FilmSourcePlugin 是不同对象"的最坏场景，断言插件是否正确选了宿主的那个（本地跑 `python regression.py`）
* **`selftest.py`** —— 30 项断言（含 import 路由 + javstore 复活 + 老模板封面 + GIF 策略）

常见原因（按出现频率排）：
1. **打包版 PyInstaller 静态图缺漏**（上游 issue #178），标准库插件加载失败
2. **机器上有两份 amane 安装**，`amane.plugin` 与 `amane.plugins.api` 解析到不同副本
3. **本地开发残留**：sys.path 里还有旧的 amane checkout

`plugin.py` 已经做了**双路径降级**——优先 `amane.plugins.api / amane.net.* / amane.crawlers.*`，再退回 `amane.plugin`。装好插件后**先重扫**，看 `import-report.txt` 里 "本插件用的 FilmSourcePlugin" 与 "宿主 manager 比对用的那个" 是否同一对象。若不是，说明宿主本身的两份类对象就不一致（重装 amane 桌面版解决）。

---

## 九、卸载

删掉 `%LOCALAPPDATA%\Amane\plugins\sources\esl1.javarchive\` 目录，
并从「内容路由 → FC2」里移除 `esl1.javarchive`。
