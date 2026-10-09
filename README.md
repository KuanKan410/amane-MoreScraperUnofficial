# amane-MoreScraperUnofficial

Amane 媒体库的非官方补充元数据源插件合集。基于 Amane 官方插件规范开发，覆盖 FC2 与 JAV（有码/无码）内容。

## 插件列表

| 插件 ID | 版本 | 支持内容类型 | 来源站点 | 说明 |
| --- | --- | --- | --- | --- |
| `esl1.fc2ppvdb` | 0.3.0 | `fc2` | fc2ppv-db.com | FC2 元数据。内置进程级浏览器复用，应对 Cloudflare 挑战 |
| `esl1.javarchive` | 0.7.0 | `fc2` / `censored` / `uncensored` | javarchive.com | 多合一兜底源，覆盖 FC2 与有码/无码 JAV，含番号后缀匹配修复 |
| `esl1.supfc2` | 0.1.0 | `fc2` | supfc2.com | FC2 素人内容，含剧照墙 |
| `esl1.supjav` | 0.2.1 | `fc2` / `censored` / `uncensored` | supjav.com | 通用 JAV 元数据，覆盖三条路由 |
| `unofficialscraper.fc2cmadb` | 0.1.0 | `fc2` | fc2cmadb.com | FC2 无码破解库。需本机浏览器登录一次（会话复用）；对「登录才可见」的条目标返回 404，插件撞 404 自动回落已登录浏览器兜底 |

## 各插件文档

每个插件都有独立的 README，介绍该站的实测细节、字段来源、配置与排错：

| 插件 | 文档 |
| --- | --- |
| `esl1.fc2ppvdb` | [esl1.fc2ppvdb/README.md](esl1.fc2ppvdb/README.md) |
| `esl1.javarchive` | [esl1.javarchive/README.md](esl1.javarchive/README.md) |
| `esl1.supfc2` | [esl1.supfc2/README.md](esl1.supfc2/README.md) |
| `esl1.supjav` | [esl1.supjav/README.md](esl1.supjav/README.md) |
| `unofficialscraper.fc2cmadb` | [unofficialscraper.fc2cmadb/README.md](unofficialscraper.fc2cmadb/README.md) |

## 安装方式

1. **下载仓库**：`git clone https://github.com/KuanKan410/amane-MoreScraperUnofficial.git`
2. **准备插件**：每个插件独立使用，只需对应文件夹内的 `plugin.py`，例如 `esl1.supjav/plugin.py`
3. **安装到 Amane**：
   - 方法一：Amane 界面 → **管理 → 插件 → 上传 zip**，只压缩单个插件文件夹
   - 方法二：手动复制插件文件夹到 Amane 数据目录的 `plugins/sources/` 下，再「重新扫描」或重启 amane
4. **加入内容路由**：安装后在 **设置 → 刮削 → 内容路由**，把插件 ID 勾进对应内容类型的列表（如 `fc2` 路由勾选 `esl1.supfc2`）

## 运行要求

- **Python 依赖**：仅用 Amane 自带的 `parsel` / `pydantic`，无需额外安装第三方包
- **浏览器支持**：
  - `esl1.fc2ppvdb` / `esl1.supfc2` / `esl1.supjav`：需要 Chrome / Edge 内核浏览器，用于通过 Cloudflare 挑战获取 `cf_clearance` 会话
  - `unofficialscraper.fc2cmadb`：需要浏览器来做**交互式登录**（fc2cmadb 要登录才能看，首次/会话过期会弹一次浏览器窗口让用户手动登录，之后按 `session_ttl` 复用会话）；未登录时刮多个未收录番号只弹一次，其余快速报「需先登录」
  - `esl1.javarchive`：不需要浏览器，纯静态搜索 + 解析
- **代理**：站点若被墙，需配代理（支持环境代理或 Amane 全局代理）

## 使用注意

1. **Cloudflare 与 cookie**：过 CF 依赖本机浏览器拿 `cf_clearance`。该 cookie 与**出口 IP + UA 成对绑定**，别手动填过期或别处的 cookie，留空让插件自己过挑战即可。
2. **番号后缀**：同人作品常出现 `COSH-218K` 这类「番号 + 画质后缀（`K` = 4K）」的名称，当前未完全识别。若刮削失败，先手动去掉后缀再重试。
3. **fc2cmadb 的登录墙伪装 404**：该站会把「登录才能看」的视频对匿名请求返回 404。插件撞 404 会先回落一次已登录浏览器确认真伪，浏览器也拿不到才算未收录。
4. **fc2cmadb 不提供**简介 / 剧照字段，但提供女优（延迟加载，插件会额外请求一次，映射到 actors；关掉 `include_actresses` 可省这次请求）。标题 / 封面 / 发行日 / 时长 / 片商 / 标签 / 女优均可取到。
5. **未命中返回 None**，不算失败；网络错误抛 `SourceError` 交 Amane 记录。
6. 本仓库为**非官方**社区贡献，不保证持续更新；使用前请核对插件版本与你的 amane 版本。

## 许可证

本仓库代码采用 MIT 许可证，各插件版权归原作者所有。