# esl1.fc2ppvdb —— fc2ppv-db.com 插件

FC2 PPV 元数据数据库（<https://fc2ppv-db.com>）的 amane 影片元数据插件。
**只服务 fc2 路由**，版本 **v0.3.0**（2026-09-29 加入批量并发收口，实测校准）。

## 安装

1. 把整个目录放到 amane 的插件源目录：

   ```
   %LOCALAPPDATA%\Amane\plugins\sources\esl1.fc2ppvdb\
     plugin.py     ← 必需
     README.md
   ```

2. amane → **管理 → 插件 → 重新扫描**
3. 把 `esl1.fc2ppvdb` 加进 fc2 路由：

   ```toml
   [scraping.content_routes]
   fc2 = ["esl1.fc2cmadb", "esl1.javarchive", "esl1.supjav", "esl1.fc2ppvdb"]
   ```

> 没在路由里看到它？路由下拉只列出 `content_types` 声明了该类型的插件。
> 本插件已声明 `fc2`，若仍看不到，先确认重新扫描过（宿主会缓存描述符）。

## 这个站点是怎么工作的（实测）

### 番号可以直接拼 URL，不用搜索

```
FC2-PPV-3125926  →  https://fc2ppv-db.com/ja/videos/3125926
```

URL 里的数字**就是** FC2 的番号数字。所以本插件**不走站点搜索**，一跳直达详情页
—— 这是最稳的发现方式。（站点有 `/ja/search`，但 `robots.txt` 里
`Disallow: /*/search`，且没必要用。）

### 两道门

**1. Cloudflare managed challenge。** 裸 HTTP 一律 403 `Just a moment...`
（curl_cffi 各种指纹、无头 Chrome 都过不去）。

插件的做法：平时走普通 HTTP；撞到挑战就**临时拉起你本机的 Chrome**（起来立刻
最小化）过一次，拿到 `cf_clearance` 存进 `cf_session.json`，之后 **1500 秒**内
复用，不再起浏览器。也就是每 25 分钟最多闪一下浏览器窗口。

关键细节：`cf_clearance` 与**出口 IP + UA 绑定**，所以插件把 cookie 和 UA
**成对**保存、成对使用。手动改 `user_agent` 会让 cookie 立刻失效。

#### 批量刮削只起一个浏览器（v0.3.0 修复）

**旧版的问题**：amane 的 `worker.concurrency` 默认 20，一批 20 个番号会在同一秒
同时撞上挑战，而旧版的会话是**每个抓取任务私有的** —— 20 个任务各起一个 Chrome。
实测现场：debug.log 里同一秒出现 3~20 个不同的调试端口，一天累计 **87 次**浏览器
启动，机器 CPU / 内存直接被拖满。

**现在的做法**：进程级 `_SessionGate` 把「过一次挑战」收成一个临界区。

| 环节 | 行为 |
|---|---|
| 会话缓存 | 进程级、跨 provider 实例共享；谁过完挑战，同批的其它任务立刻复用 |
| 浏览器闸门 | 每个事件循环一把锁，同一时刻只放行**一个**任务去起 Chrome |
| 双检 | 等锁期间别人过完了 → 醒来直接拿结果，**绝不再起一个** |
| 惊群阻尼 | 放行前随机等 0.2~0.8 秒，避免等待者同时重试 |
| 失败冷却 | 过挑战失败后 20 秒内不再重复起浏览器，直接把原因报上去 |
| 顺序重试 | 一次放行内最多顺序试 2 次（同一时刻仍然只有 1 个 Chrome） |

实测（2026-09-29，真实站点 + 真实 Chrome，12 个番号并发、会话已清空）：

```
闸门放行：起浏览器过挑战（本进程累计启动 1 次 / 复用 0 次 / 拦下重复启动 0 次）
过挑战成功：cookies=['cf_clearance']
等锁期间拿到别的任务过好的会话（不起浏览器）   ← 11 次
```

进程监控：**浏览器实例峰值 = 1**（对应 12 个 Chrome 进程，即一个浏览器的进程树）。
旧版同一场景是 20 个实例 ≈ 200+ 进程。

另外：挑战过一次之后，进程内缓存让**后续整批**都无需再碰浏览器（实测 12 条
并发任务 0 次启动、0 次挑战）。

#### 顺手修掉的一个死循环

手动在设置里填了 `cookie` 的话，旧实现会把它当成"永远有效"，撞了挑战也不会去
起浏览器 —— 于是每个任务拿着同一条死 cookie 无限重试。现在：

* 手填的 cookie 只当**兜底种子**（内存里一条会话都没有时才用），浏览器刷出来的
  新 cookie **不会被它盖回去**；
* `_ensure_session()` 只有在 **cookie 确实换掉了**（新值 ≠ 刚被拒的那条）时才
  允许白嫖这次重试，并且白嫖次数封顶 `browser_attempts + 1`。

**2. 年龄确认。** 中间件会把未验证请求重定向到 `/ja/age-verify?returnTo=...`。
好消息是只要请求带 `age-verified=true` cookie 就**直通**，不用模拟点击 ——
浏览器里点「はい、18歳以上です」后写下的正是这个 cookie。插件每次请求都会带上。

### 数据只在 RSC payload 里（重点）

详情页 SSR 的「動画詳細情報」区块只显示 **動画ID / 流出 / モザイク / 出演女優**，
**没有日期、没有时长、没有卖家**。这些在 Next.js 的 RSC payload 里：

```html
<script>self.__next_f.push([1,"...{\"video\":{\"id\":\"4869722\",\"title\":\"…\",
  \"releaseDate\":\"$D2026-03-26T00:00:00.000Z\",\"duration\":4078,
  \"sellerId\":\"4610perper\",\"seller\":{\"name\":\"しろーとペロペロ\"},
  \"actresses\":[{\"actress\":{\"name\":\"ゆか\"}}], …}}…"])</script>
```

两个坑：

* RSC 里的日期是 **`$D` 前缀**（Next.js 的 Date 序列化），插件负责剥掉。
* 页面里 `releaseDate` 这个字符串会出现几百次，但**绝大多数是 i18n 翻译键**
  （`"Video":{"releaseDate":"配信日"}`），不是真数据。插件按 `id` 精确匹配主视频
  对象，不会把翻译键或「関連動画」当成主视频。

### 封面

详情页的 `og:image` 是真的：

```
https://d39jz7pbpqkw9s.cloudfront.net/thumbnails/48/4869722.webp
```

⚠️ 但**首页 / 年龄确认页**的 `og:image` 是 `https://localhost:3000/opengraph-image?...`
——站点自己配错了。插件会挡掉这类无效封面，并在拿不到 `og:image` 时用
`cdn_base + thumbnailLocal` 拼出来。

### CDN 域名（v0.2.1 修的坑）

站点 CDN 是 **`https://d39jz7pbpqkw9s.cloudfront.net`**（注意是 `jz7`）。

曾经把它手抄成 `d39j7zpbpqkw9s`（`j7z`）——这两个字符串肉眼几乎无法分辨，
而错的那个域名 **SSL 握手直接失败**，于是所有靠拼接的图片（剧照、
无 `og:image` 时的封面兜底）一张都下不来。

所以插件现在**不单纯依赖这个常量**：`_detect_cdn()` 会先从当前页面里真实出现过
的图片 URL（`/thumbnails/`、`/samples/`、`/faces/`、`/sellers/` 路径）数出用得最多的
那个域名，用它来拼；只有页面里完全没有图片时才回退到 `cdn_base` 配置。
也就是说**即使 `cdn_base` 填错，剧照 URL 依然是对的**。

## 数据质量（实测 7 个样本）

| 字段 | 命中 |
|---|---|
| 标题 | 6/7 |
| 封面 | 6/7 |
| 配信日 | 4/7 |
| 时长 | 4/7 |
| 卖家 | 4/7 |
| 出演女優 | 4/7 |

部分条目站点自己标记了 `scrapeFailed=true`（它没从 FC2 抓成功），这时
`releaseDate` / `duration` / `seller` 全是 null。**插件如实留空，绝不编造。**

女優列表在 SSR 里是客户端渲染的（CSS 选择器抓不到），只能从 RSC 的 `actresses`
数组取 —— 这也是 `use_rsc` 默认开启的原因。

### 标签（タグ）

也在 RSC 里，字段是 **`productTags`**，结构 `productTags[].tag.name`：

```json
"productTags":[{"videoId":"4973988","tagId":"…","tag":{"name":"素人","videoCount":95091}}]
```

页面「タグ」区块里每个标签其实是站内筛选链接 `/ja/videos?tags=<urlencoded>`，
两种取法结果一致，插件 **RSC 优先、DOM 兜底**。

⚠️ 两个细节：

* 站点会把**卖家名也塞进标签**（如 4973988 的标签里有「むすめガチャ」，而它的
  卖家正好叫这个）。默认 `drop_seller_tag` 会剔除，避免和 `studio` 重复；想保留
  就把它关掉。
* 页面里还有个指向 FC2 的外链 `adult.contents.fc2.com/.../?tag=...`，文字是
  「FC2で見る」。它**不是标签**，选择器用站内路径 `/ja/videos?tags=` 开头把它排除了。

### 剧照（サンプル画像）

RSC 字段是 **`images`**，按 `sortOrder` 排序：

```json
"images":[{"imageLocal":"samples/49/4973988/000.webp","sortOrder":0,"imageUrl":"…"}]
```

* `imageLocal` 是站点 CDN 上的 webp（原尺寸），用它拼 `cdn_base` 得到
  `https://d39jz7pbpqkw9s.cloudfront.net/samples/49/4973988/000.webp`；
* `imageUrl` 是 FC2 的 **w480 缩略图**，只在 `imageLocal` 缺失时兜底。

剧照写进 amane 的 **`extrafanart`** 字段（不是 `thumb_urls`），封面不混进去。
DOM 里对应 `img[src*='/samples/']`（如 `alt="Sample 1"`），作为 RSC 失效时的兜底。

### 实测对照

| 番号 | 标签 | 剧照 | 说明 |
|---|---|---|---|
| FC2-4973988 | 16（剔卖家后） | 10 | 数据齐全 |
| FC2-4322094 | 9 | 10 | 数据齐全 |
| FC2-4869722 | 0 | 6 | 站点没给标签，属正常 |
| FC2-3125926 | 0 | 0 | 站点 `scrapeFailed`，如实留空 |

## 配置项（全部有中文说明，在插件设置页可直接改）

常用的几个：

| 字段 | 默认 | 说明 |
|---|---|---|
| `locale` | `ja` | 路径里的语言段 |
| `cdn_base` | `https://d39jz7pbpqkw9s.cloudfront.net` | CDN 前缀，仅当页面里一张图都没有时才用它 |
| `age_cookie` | `age-verified=true` | 年龄确认 cookie |
| `use_rsc` | 开 | 关掉就只剩标题 + 封面（日期/时长/卖家都没了） |
| `browser_fallback` | 开 | 关掉则撞挑战就直接失败 |
| `browser_headless` | 关 | **无头过不了 Cloudflare**，别开 |
| `browser_path` | 空 | 自动找 Chrome/Edge；找不到时填完整路径 |
| `browser_proxy` | 空 | 留空跟随 amane 网络设置 |
| `challenge_timeout` | 45 | 机器慢可调到 60 |
| `session_ttl` | 1500 | cf_clearance 复用秒数 |
| `browser_singleton` | 开 | 批量时只起一个浏览器（**核心修复**，别关） |
| `browser_attempts` | 2 | 一次放行内最多顺序试几次；也是每次抓取白嫖重试的上限 |
| `refresh_cooldown` | 20 | 过挑战失败后的冷却秒数，期间不再重复起浏览器 |
| `drop_seller_tag` | 开 | 剔除与卖家同名的标签（避免和片商重复） |
| `max_tags` | 30 | 标签上限，0 = 不限 |
| `fetch_samples` | 开 | 抓取剧照写进 `extrafanart` |
| `max_samples` | 20 | 剧照上限，0 = 不限 |
| `report_misses` | 开 | 站点没有该番号时报原因，而不是静默空 |

封面/标题/女優的选择器都可改（站点改版时不用动代码）。
注意 `actor_selector` 默认含 `/ja/actresses/`，如果你改了 `locale` 要同步改。

## 排查

按顺序看：

1. `debug/debug.log`（插件数据目录下）—— 记录番号、URL、RSC 是否命中、
   各选择器命中数。一行 `=0` 就是"选择器失效"的信号。
2. `debug/<番号数字>.html` —— 抓到的原文。
3. `debug/cloudflare.last.html` + `debug/chrome.log` —— 过挑战失败时的现场。
4. 任务报告里的 `reason`：
   - `cloudflare_challenge` → 浏览器没过挑战，看 `cloudflare.last.html`
     是不是「请稍候…」；可调大 `challenge_timeout` 或用 `browser_path` 指定浏览器
   - `age_verification` → `age-verified` cookie 没生效
   - `no_usable_metadata` → 页面拿到了但没解析出标题，看选择器命中数

插件数据目录：`%LOCALAPPDATA%\Amane\plugins\esl1.fc2ppvdb\`

## 已知限制

* 只覆盖 fc2（这个站只有 FC2 内容）。
* 站点自身数据不全（见上表），本插件不会为了"填满"而猜值。
* 首次使用会闪一下浏览器窗口（最小化状态），用于拿 `cf_clearance`。
  之后约 25 分钟内不再出现。
* Cloudflare 偶尔会提前作废 `cf_clearance`（批量请求时更常见），
  表现为突然 403 —— 插件会自动重新起浏览器过一次，不需要干预。
* 过挑战**失败**时（比如挑战真的过不去），整批任务会在 20 秒冷却期内快速失败
  并报 `cloudflare_challenge`。这是刻意的：宁可快速失败一次，也不要几十个
  Chrome 一起把机器拖死。冷却过后下一批会重新尝试。

## 自测

```bash
python selftest.py        # 34 项，不需要 amane，不需要联网
```

其中专门锁死批量行为的 5 项：20 并发只起 1 个浏览器、过挑战失败只顺序试 N 次、
关闭 `browser_singleton` 才回到旧行为、跨实例共享会话、无效手填 cookie 不死循环。

想看新旧对比可以跑 `_bench_concurrency.py`（旧版 20 次 → 新版 1 次）。
