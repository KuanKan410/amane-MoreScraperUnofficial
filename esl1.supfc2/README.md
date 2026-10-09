# esl1.supfc2 —— supfc2.com 插件（amane）

FC2 素人片库 [supfc2.com](https://supfc2.com/) 的影片元数据源。
返回：标题、封面、发行日、时长、片商（Maker）、标签、简介、**剧照**。

安装目录：`{amane 数据目录}/plugins/sources/esl1.supfc2/plugin.py`
（Windows 上通常是 `%LOCALAPPDATA%\Amane\plugins\sources\esl1.supfc2\plugin.py`）

---

## 装完之后必须做两件事

1. **管理 → 插件 → 重新扫描**（宿主的插件描述是缓存的，不扫看不到新插件）
2. 把 `esl1.supfc2` 加进 **FC2 路由**：

   ```toml
   [scraping.content_routes]
   fc2 = ["fc2", "esl1.fc2ppvdb", "esl1.javarchive", "esl1.supjav", "esl1.supfc2"]
   ```

   也可以在 设置 → 抓取 的路由编辑里勾选。**如果 FC2 路由里选不到这个插件**，
   说明宿主的插件缓存还没刷新，回到第 1 步重新扫描。

装完建议先刮一个已知番号验证：`FC2-4973988`（应有 17 个标签、30 张剧照、
发行日 2026-09-11、时长 67 分钟、片商 娘ガチャ）。

---

## 站点结构（2026-09-29 实测）

### 详情页可以从番号**直连**

```
https://supfc2.com/detail/FC2-PPV-4973988/x        → 200  完整详情页
https://supfc2.com/detail/FC2-PPV-4973988/aaaaa    → 200  同一个页面
https://supfc2.com/detail/FC2-PPV-4973988          → 404  ← 末段 slug 不能省
https://supfc2.com/detail/4973988                  → 404
https://supfc2.com/detail/FC2-PPV-1234567/x        → 404  本站没收录
```

路径第二段必须是 `FC2-PPV-<纯数字>`；第三段是 SEO slug，**内容无所谓但不能缺**。
所以插件直接用番号拼 URL，**一次请求**拿全部字段，不需要搜索。

`detail_template` 里 `{base}` / `{digits}` 可改，默认
`{base}/detail/FC2-PPV-{digits}/x`。

### 字段在哪

| 字段 | 位置 |
|---|---|
| 标题 | `h1.product-name` |
| 封面 | `div#ovl` 的 `style="background-image: url(...)"` |
| 发行日 / 时长 / 片商 / 标签 | `ul.vendor-info li.ttt` → `<label>Release Date: </label><span class="detail">…` |
| 简介 + 剧照 | `#product-tab-description div.mb-4`（Movie Description 那块） |

信息表按 **label 文本**取值（`_info_map`），不按 DOM 位置 —— 换主题、插字段都不会错位。

两个坑：

* **`og:image` 的第一个值是站点自己的分享图**（`/images/2supfc2.png`），
  已在黑名单里；封面优先取 `div#ovl`。
* **剧照是正文里指向 storage 图片域名的 `<a href>`**，不是独立的剧照墙。
  有的作品正文里根本没有图，那就是没有剧照（不是选择器坏了）。
  实测 `4973988` 有 32 个（22 张 `サンプル画像 (N).png` + 10 个没标题的缩略图
  大图），`4981113` 有 7 个，`4322094` 的正文图标题是 `14.jpg` 这种。
  出图顺序是乱的（20,28,27,25,16,23,…），只有在每项 title 都能解析出序号时才排序。
* 正文里混着 `//contents-thumbnail2.fc2.com/w1280/storage….png` 这种**缺协议头的
  包装地址**，插件会还原成原图并补全 `https://`，否则宿主的下载器会失败。

### Cloudflare Managed Challenge

整站在 CF 托管挑战后面。`curl_cffi` 任何指纹都是 403，无头 Chrome 也过不去，
**只有有头 Chrome 能自动过关**。所以插件内置了一份只用标准库写的极简 CDP 客户端：

> 起一个有头 Chrome（窗口立刻最小化）→ 跑一遍挑战 → 导出 `cf_clearance` + UA
> → 关掉浏览器 → 之后所有请求带着这串 cookie 走宿主的 curl_cffi。

`cf_clearance` 绑定「客户端 IP + UA」，所以 cookie 与 UA 一起缓存、一起发送。
有效期由 `session_ttl` 控制（默认 1500 秒），也就是说**大约每 25 分钟才会弹一次
浏览器窗口**（而且是最小化的）。不想让它弹窗就在设置里手工填 cookie + UA。

---

## 设置项说明（都在插件设置面板里，不用改代码）

| 分组 | 字段 | 说明 |
|---|---|---|
| 基础 | `detail_template` | 详情页模板，`{base}` / `{digits}` |
| | `search_path` | 站内搜索路径，**只在直达 404 时才用的兜底** |
| | `search_fallback` | 关掉可以省一次请求，代价是少量条目抓不到 |
| 解析 | `title_selector` / `cover_selector` / `maker_selector` / `tags_selector` | 逗号分隔、按顺序尝试 |
| | `info_selector` | 那张信息表的条目选择器 |
| | `samples_selector` / `plot_selector` | 剧照 / 简介所在容器 |
| 数据 | `include_*` | 各字段开关 |
| | `drop_maker_tag` | 剔除与片商**同名**的标签（精确匹配；`娘ガチャ` ≠ `むすめガチャ`，后者不会被误剔） |
| | `tags_drop` | 额外要剔除的标签，默认含 `unknown`（Genre 栏的占位值） |
| | `samples_max` / `plot_max` | 上限，默认 30 / 1200 |
| Cloudflare | `browser_fallback` 等 | 见上一节 |

---

## 排查

**第一步永远看这个日志**：

```
{amane 数据目录}/plugins/esl1.supfc2/debug/debug.log
```

里面有每一步（番号 → 用了哪条路径 → 最终 URL → 解析结果），还有一行
**每个选择器的命中数**：

```
选择器命中: title[h1.product-name::text=1]  cover[div#ovl::attr(style)=1] ...
```

一水的 `=0` 就是「站点换皮了」，把对应选择器在浏览器 F12 里确认后填进设置即可。
`debug/` 下还有抓下来的原始页面（`4973988.direct.html` 等）。

任务报告里的失败原因：

| reason | 含义 |
|---|---|
| `no_usable_metadata` | 抓到了但解析不出字段，或者本站没收录这个番号（detail 里会写清） |
| `cloudflare_*` / `ip_banned` | 挑战没过 —— 看 `debug/chrome.log` 和 `debug/cloudflare.*.html`；换个 `browser_profile` 目录，或手工填 cookie |
| `timeout` | 调大 `network.timeout` / 重试 |

### 本站没收录的番号

`3125926` / `4551862` 这类在本站搜不到（直达 404、搜索也 0 条），这是**数据缺失**
不是插件问题 —— fc2ppv-db.com 上 `3125926` 也被标了 `scrapeFailed`。
不存在的番号走搜索时站点会返回 **500**（Laravel 报错页），插件按「没找到」处理，
不会重试也不会抛异常。

---

## 开发者

```bash
python selftest.py        # 97 项，不需要 amane，也不需要联网
```

夹具在 `fixtures/`，全是真实抓下来的页面（已裁剪）：`4973988.html`、
`4322094.html`、`404.html`、`search.html`、`cloudflare.html`。

回归重点：

* 直达 URL 的末段 slug 不能省（省了就是 404）
* 封面不能取到站点分享图 `/images/2supfc2.png`
* 挑战页不能静默返回 `None`：必须起浏览器过挑战再重试，且 cookie 与 UA 成对
* 404 / 500 不重试
* `_json_get` 必须绕过 `http_proxy`（回环调试端口被代理劫持的 bug 真的发生过）
