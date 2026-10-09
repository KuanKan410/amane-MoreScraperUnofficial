# unofficialscraper.fd2ppv —— fd2ppv.cc 插件（amane）

给 amane 加一个 fd2ppv.cc 的 FC2 元数据源。

* 插件 id：`unofficialscraper.fd2ppv`
* 版本：**v0.1.0**（2026-10-09 真机实测校准）
* 内容路由：仅 `fc2`
* 提供字段：标题、发行日、时长、片商、标签、女优、封面（`poster_urls` + `thumb_urls` 同值）、剧照墙（`extrafanart`）、来源 URL
* 来源站点：<https://fd2ppv.cc>

---

## 1. 这个站点是怎么工作的（全部为实测结论）

### 1.1 番号可以直接拼 URL，不用搜索

详情页是 `/articles/{数字}`，**数字就是 FC2 番号的数字**——至少在 FC2-PPV-4989610、
4989588 上成立。所以本插件**不走站内搜索**，一跳直达详情页：

```
FC2-PPV-4989610  →  https://fd2ppv.cc/articles/4989610
```

这是最稳的发现方式。

### 1.2 ⚠ Cloudflare managed challenge

详情页整片挂在 Cloudflare **托管挑战**后面。实测：

| 手段 | 结果 |
|---|---|
| curl_cffi 任意指纹 / 无头 Chrome | **403 挑战页**（拿不到数据） |
| **有头 Chrome，起来后立刻最小化** | **通过** ✅ 顺手把详情页 DOM 也取回来 |

`cf_clearance` 是 **HttpOnly** 的（`document.cookie` 读不到），只能靠 CDP 的
`Network.getCookies` 导出——所以插件内联了一份**纯标准库手写**的 CDP 客户端
（socket + struct + base64 + subprocess 自己拼 WebSocket 帧），不依赖
playwright / selenium（打包版 amane 里装不了这些第三方包）。

流程：

1. 平时走**普通 HTTP**；
2. 撞到挑战（403，或 HTTP 200 但正文是挑战页）→ **临时拉起本机 Chrome**（起来即最小化）
   直接导航到**详情页本身**——既过挑战又顺手把 DOM 取回来，省掉一次请求；
3. 拿到 `cf_clearance` → 连同 UA **成对**写入 `cf_session.json`，本次请求不消耗重试，
   直接拿 cookie + 同一 UA 重试；
4. `session_ttl`（默认 **3600 秒**）内所有请求复用该会话，**不再启动浏览器**。

关键：`cf_clearance` 与 **出口 IP + UA 绑定**。手填 `cookie` 却不同时配同名
`user_agent`、或普通 HTTP 与浏览器走不同出口（代理不一致），都会立刻失效。

### 1.3 详情页字段（服务端渲染的静态 HTML）

| 字段 | 来源（CSS 选择器 / 标签） | 实测（4989610） |
|---|---|---|
| 番号 | `.work-title` 文本 | `4989610` |
| 发行日 | JSON-LD `datePublished` / 页面日期 | `2026-10-09` |
| 时长 | `.work-meta`「収録時間」，`H:MM:SS` → 分钟 | `00:58:46` → `58` |
| 片商 | `.work-meta`「販売者」（值为 `不詳` 时取 `None`） | `ひらめき無無剣` |
| 女优 | `.artist-name` 文本（`不詳` 自动过滤） | `元アイドルちゃん`、`来栖ユリ` |
| 标签 | `.work-tags`（常为空 div，容错） | 常无 |
| 封面 | `.work-photos` 里**含 `contents.fc2.com` 的那条**（官方封面） | `storage201000.contents.fc2.com/…` |
| 剧照 | `.work-photos` 里**其余**条目（`xximgs.cc`，2–11 张） | 2 张 |

两个实测容易踩的坑（插件都已容错，绝不编造）：

1. `.work-tags` 常常是**空 div**（无标签的正常作品很多）；`.artist-name` 常为 `不詳`。
2. `.artist-tags` 是**女优画像的标签**（如「剛毛風俗在籍…」），**不是作品标签**，
   插件不会拿它当 `tags`。

### 1.4 封面 / 剧照的坑（还原包裹)

`.work-photos` 里的 FC2 官方封面**可能被 `contents-thumbnail2.fc2.com/w276/` 包裹**
（缩略包装），需还原成真实 storage 域名，否则图片 404。插件内置
`_canonical_image_url` 处理这种包裹（与 `esl1.supfc2` 同一套逻辑）。

封面写进 `poster_urls` / `thumb_urls`（同值）；剧照写进 `extrafanart`，封面不混进去。

### 1.5 实测对照（真机，2026-10-09）

| 番号 | 结果 |
|---|---|
| FC2-PPV-4989610 | 标题 / 发行日 2026-10-09 / 时长 58 / 片商 / 3 标签 / 女优 + 封面 1 + 剧照 2 |
| FC2-PPV-4989588 | 稀疏页（无标签、女优为 `不詳`），**不崩溃**，如实留空 |
| FC2-PPV-99999999 | 未收录 → 返回 `None`，无报错 |

在 amane 里实测，本插件补上了 fc2 官方源缺失的 **女优 / 发行日 / 时长** 字段。

---

## 2. 安装 / 升级

1. 目录：`%LOCALAPPDATA%\Amane\plugins\sources\unofficialscraper.fd2ppv\plugin.py`
   （目录名必须与 id 一致）
2. amane → **管理 → 插件 → 重新扫描**
3. 把 `unofficialscraper.fd2ppv` 加进 fc2 路由：

   ```toml
   [scraping.content_routes]
   fc2 = ["unofficialscraper.fc2cmadb", "unofficialscraper.fd2ppv"]
   ```

4. 首次刮削会闪一下浏览器窗口（最小化状态），拿一次 `cf_clearance`；之后
   `session_ttl` 内都是纯 HTTP，不再弹窗。

---

## 3. 设置项（全部可在 amane 面板里改）

**常用**

| 字段 | 默认 | 说明 |
|---|---|---|
| `include_cover` | 开 | 把官方封面写进 `poster_urls` / `thumb_urls` |
| `include_gallery` | 开 | 把剧照墙（xximgs 图）写进 `extrafanart` |
| `include_tags` | 开 | 把作品标签写进 `tags` |
| `include_actresses` | 开 | 把 `.artist-name` 写进 `actors`；「不詳」自动过滤 |
| `max_tags` | 0 | 标签上限，0 = 不限 |
| `drop_category_tag` | 开 | 剥掉与「カテゴリ」同名的标签项 |

**Cloudflare / 浏览器**

| 字段 | 默认 | 说明 |
|---|---|---|
| `session_ttl` | 3600 | `cf_clearance` 复用秒数，过期才重新起浏览器 |
| `browser_fallback` | 开 | 撞挑战时拉起本机 Chrome；关掉则撞墙直接失败 |
| `browser_headless` | **关** | 无头过不了 Cloudflare，别开 |
| `browser_path` | 空 | 留空自动找 Chrome / Edge |
| `browser_profile` | 空 | 留空用数据目录下 browser-profile |
| `browser_proxy` | 空 | 留空跟随 amane 网络设置；**须与普通 HTTP 同出口**（见下） |
| `challenge_timeout` | 45 | 单次过挑战等待上限（秒） |
| `cookie` | 空 | 手工填 `cf_clearance=...`；必须与 `user_agent` 配套 |
| `user_agent` | 空 | 手工指定 UA；过 CF 时须与 cookie 成对 |

**网络 / 诊断**

| 字段 | 默认 | 说明 |
|---|---|---|
| `request_retries` | 2 | 普通请求失败后的重试次数 |
| `request_retry_delay` | 1.5 | 重试起始间隔，逐次递增 |
| `report_misses` | 关 | 未命中抛 `SourceError` 让 Amane 记录，而非静默 None |
| `debug_dump` | 关 | 写 `debug/debug.log` |
| `debug_save_pages` | 关 | 页面落盘便于排查 |

> ⚠️ **代理一致性是关键**：`cf_clearance` 绑定出口 IP。若你给浏览器配了
> `http://127.0.0.1:7890` 而 amane **全局代理**没设（普通 HTTP 走直连），两者出口
> 不同，缓存的 cookie 对普通 HTTP 永远无效 → 每个任务都会重新起浏览器。要么两边都配
> 同一个代理，要么都走系统默认。

---

## 4. 排错

调试产物（`debug_dump` 开启后）：

```
%LOCALAPPDATA%\Amane\plugins\unofficialscraper.fd2ppv\
    debug\debug.log              # 每一步流程 + 浏览器诊断
    debug\chrome.log             # CDP / 浏览器过挑战的详细日志
    debug\cloudflare.last.html   # 最后一次过挑战拿到的页面（过没过一目了然）
    debug\<番号>.html            # 落盘的详情页原文
    cf_session.json              # cf_clearance + UA 会话
```

**排查顺序**

1. `debug/debug.log` —— 看有没有「过挑战成功：cookies=… cf_clearance=有」。
   没有说明挑战没过，看 `debug/cloudflare.last.html` 是不是「请稍候…」。
2. 一直反复弹浏览器 → 先确认**代理一致性**（见上）：浏览器和普通 HTTP 必须同出口。
   再确认本机装了 Chrome / Edge、`browser_path` 指对。
3. 详情页拿到了但字段是空的 → 看 `debug/<番号>.html` 里对应选择器有没有命中的数据。
4. 任务报告的 `reason`：
   - `cloudflare_challenge` → 浏览器没过挑战，调大 `challenge_timeout` 或用 `browser_path`
   - `no_usable_metadata` → 页面拿到了但没解析出标题，看保存的 HTML

---

## 5. 已知限制

* 只覆盖 `fc2`（这个站只有 FC2 内容）。
* 标签、女优常为空属站点自身数据问题，插件如实留空，不编造。
* 首次使用会闪一下浏览器（最小化）用于拿 `cf_clearance`；之后 `session_ttl`
  内不再出现。Cloudflare 偶尔提前作废 cookie（批量时更常见），表现为突然后继 403，
  插件会自动重新起浏览器过一次，无需干预。
* `browser_proxy` 与 amane 全局代理必须同出口，否则缓存失效、每个任务都弹浏览器。

---

## 6. 开发自测

```bash
python -m pytest tests/test_fd2ppv_parse.py -q   # 25 项，离线、不需要 amane
```

解析与 `_get` 编排（含浏览器兜底路由）都有离线单测，夹具基于真实抓取的页面。