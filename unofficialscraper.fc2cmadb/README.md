# unofficialscraper.fc2cmadb —— fc2cmadb.com 插件（amane）

给 amane 加一个 fc2cmadb.com 的 FC2 元数据源。

* 插件 id：`unofficialscraper.fc2cmadb`
* 版本：**v0.1.0**
* 内容路由：仅 `fc2`
* 提供字段：标题、封面（`poster_urls` / `thumb_urls`）、发行日、时长、片商、标签、女优、来源 URL
* **不提供**：简介、剧照墙（站点详情页真没有，不编造）
* 来源站点：<https://fc2cmadb.com>（FC2 无码破解库）

---

## 1. 这个站点是怎么工作的

### 1.1 登录墙（本站最重要的事）

**fc2cmadb 把整站挂在登录后面**——未登录的匿名请求要么被重定向到登录页，要么更阴的：
**对本可访问的视频也伪装成 404**。这跟 Cloudflare 挑战是两码事，处理方式也不同。

插件的应对：

| 环节 | 行为 |
|---|---|
| 直连快路径 | 有缓存的已登录 cookie 时，先用 `use_http_client` 直接 HTTP 抓详情页 |
| 登录获取 | 没有会话 / 会话过期时，**弹一次本机浏览器（有头）让用户手动登录** |
| 会话复用 | 登录 cookie 进**进程级共享缓存**（`session_ttl`=3600s）——谁登录完，同批其它任务立刻拿去用 |
| 持久化 | 登录态写进固定 `browser-profile` 目录，**跨进程重启保留**，不用每次重登 |
| 防反复弹窗 | 同一会话内只弹**一次**登录窗；弹过一次还没拿到会话，后续 404/登录墙直接快速报「需先登录」，不再干等（否则刮一大批『本站没有』的番号时会每人弹一次） |
| 404 真伪判定 | 撞 404 先**回落一次已登录浏览器确认**：浏览器里也见不到才算「本站未收录」；浏览器若能见，说明只是你还没登录 |

> 未登录时刮多个**未收录**番号：只会弹**一次**登录窗，其余快速报「需先登录」，
> 不会每个都弹。

### 1.2 登录墙伪装 404（重点）

fc2cmadb 会把「登录才能看」的视频对**匿名/无会话**请求返回 404（真实站点里
`4302474` 这类番号在已登录浏览器可见、匿名却 404）。所以插件**不把 404 一棍子打死**：

1. HTTP 直连拿到 **404**；
2. 若还没登录 → 弹浏览器让用户登录，登录后再抓一次；
3. 已登录状态下仍 404 / 拿不到 → 判「本站未收录」，返回 `None`。

### 1.3 女优是延迟加载的

fc2cmadb 的女优区块走 Inertia **deferred 延迟加载**，首帧 HTML 里没有，需要**额外请求
一次**。所以关掉 `include_actresses` 可以省掉这次请求（没有女优需求的场景能更快）。

### 1.4 不提供简介 / 剧照

站点详情页没有简介正文、也没有剧照墙。插件**如实留空**，不会为了填字段去猜。

---

## 2. 安装 / 升级

1. 目录：`%LOCALAPPDATA%\Amane\plugins\sources\unofficialscraper.fc2cmadb\plugin.py`
   （目录名必须与 id 一致）
2. amane → **管理 → 插件 → 重新扫描**
3. 把 `unofficialscraper.fc2cmadb` 加进 fc2 路由：

   ```toml
   [scraping.content_routes]
   fc2 = ["unofficialscraper.fc2cmadb"]
   ```

4. 首次/会话过期时刮削会弹一个浏览器窗口，**手动登录一次**即可；之后 `session_ttl`
   内复用会话，不再弹。（注意该站需要能访问自身的网络环境/代理。）

---

## 3. 设置项（全部可在 amane 面板里改）

| 字段 | 默认 | 说明 |
|---|---|---|
| `use_http_client` | 开 | 先用缓存的已登录 cookie 走 HTTP 直连；拿不到才起浏览器 |
| `use_browser_fallback` | 开 | 直连失败 / 撞登录墙时起浏览器抓取 |
| `browser_path` | 空 | 留空自动找 Chrome / Edge |
| `browser_headless` | **关** | 登录流程必须有头（要在窗口里点），别开 |
| `browser_attempts` | 2 | 一次放行内顺序最多试几次 |
| `refresh_cooldown` | 20 | 全部尝试失败后的冷却秒数，期间不再重复起浏览器 |
| `session_ttl` | 3600 | 缓存的登录 cookie 多久后视为过期、需要重新登录 |
| `login_timeout` | 180 | 弹窗后等用户手动登录的最长秒数 |
| `cookie` | 空 | 可选，整条已登录 Cookie 头；留空则自动从浏览器取 |
| `user_agent` | 空 | 留空用会话里的 UA |
| `include_tags` | 开 | 抓标签 |
| `include_cover` | 开 | 抓封面 |
| `include_actresses` | 开 | 抓女优（需额外一次延迟请求；关掉可省） |
| `drop_writer_same_tag` | 开 | 剥掉与片商同名的标签 |
| `max_tags` | 30 | 标签上限，0 = 不限 |
| `verify_number` | 开 | 校验 `video_id` 与请求数字一致，防止错配 |
| `retries` | 3 | 请求失败后的最大重试次数 |
| `report_misses` | 开 | 未命中上报原因，而非静默空 |
| `debug_dump` / `debug_save_pages` | 开 | 写 `debug/debug.log`、页面落盘 |

---

## 4. 排错

```bash
%LOCALAPPDATA%\Amane\plugins\unofficialscraper.fc2cmadb\
    debug\debug.log
    debug\*.html
    browser-profile\        # 登录态（跨重启保留）
```

1. `debug/debug.log` —— 看是「需先登录」还是「该条目不存在」。
2. 一直要求登录 → 确认你确实在弹窗里**手动登录成功**了；看 `login_timeout` 是否够。
3. 任务报告的 `reason`：
   - `未登录 / 需先登录` → 本会话已弹过一次登录窗但没登录成功；确认登录后再试
   - `no_usable_metadata` → 页面拿到了但没解析出标题
   - `404 该条目不存在` → 已登录确认不是本站收录，属正常未命中

---

## 5. 已知限制

* 只覆盖 `fc2`。
* **需要登录**：刮削前先用浏览器登录一次（首次 / 会话过期）。
* 不提供简介、剧照。
* 女优延迟加载需一次额外请求；关掉 `include_actresses` 可省。
* 该站自身数据完整性不一，插件如实反映，不编造。

---

## 6. 自测

```bash
python selftest.py     # 离线单测，不需要 amane
```

登录墙伪装 404、会话共享、防反复弹窗等行为都有离线测试夹具锁定。