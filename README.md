<div align="center">

<img src="static/logo.png" width="96" alt="Music Monitor">

# Music Monitor

**自托管的音乐歌单监控下载器 · 支持 QQ 音乐与网易云音乐**

[![Release](https://img.shields.io/github/v/release/yiyu12138/Music-monitor?label=Release&color=10b981)](https://github.com/yiyu12138/Music-monitor/releases/latest)
[![fnOS](https://img.shields.io/badge/飞牛_fnOS-原生应用-2563eb)](https://github.com/yiyu12138/Music-monitor/releases/latest)
[![Docker](https://img.shields.io/badge/Docker-支持-0db7ed?logo=docker&logoColor=white)](#方式二docker)
[![Python](https://img.shields.io/badge/Python-3.12-3776ab?logo=python&logoColor=white)](requirements.txt)

[功能](#-功能) · [预览](#-界面预览) · [安装](#-安装) · [下载源](#-下载源) · [使用](#-使用) · [常见问题](#-常见问题) · [更新记录](CHANGELOG.md)

</div>

在网页上登录 **QQ 音乐 / 网易云音乐**，浏览歌单、搜索、在线试听、下载歌曲；**监控歌单，有新歌自动下载**，并自动写入标签、封面与歌词。适合部署在 NAS 或家用服务器上长期运行。

> 本项目基于 [huohen92/QQMusic-monitor](https://github.com/huohen92/QQMusic-monitor) 二次开发，在其基础上新增了网易云音乐支持、重做了整体界面，并提供飞牛 fnOS 原生应用包。

## ✨ 功能

| | QQ 音乐 | 网易云音乐 |
|---|:---:|:---:|
| 登录 | QQ 扫码 · 微信扫码 · 手机号 | 扫码 · 手机号 · Cookie |
| 歌单（自建 / 收藏 / 我喜欢） | ✅ | ✅ |
| 搜索 · 试听 · 单曲 / 整单下载 | ✅ | ✅ |
| 歌单监控，新歌自动下载 | ✅ | ✅ |
| 自动最高音质 | 母带 → 全景声 → FLAC → … | 超清母带 → Hi-Res → 无损 → … |
| 标签 · 封面 · 歌词 · .lrc | ✅ | ✅ |

- **自定义下载源**：兼容洛雪音乐（LX Music）自定义源脚本，支持链接 / 文件 / 粘贴导入；可选只用官方、只用下载源，或先官方再回退下载源
- **一份配置管两个平台**：并发数、检查间隔、保存位置、标签歌词、通知都在同一个配置页，保存后立即生效，无需重启
- **不重复下载**：按目标目录里的实际文件判断「本地已存在」，命中即跳过，也可以一键覆盖重下
- **任务管理**：排队 / 下载中的任务可取消，失败可重试；QQ 每日限额用完后自动冷却重试
- **通知**：企业微信机器人 / Bark / Webhook，推送下载结果、歌单更新与登录失效
- **界面**：浅色 / 深色 / 跟随系统；桌面侧边导航、手机底部标签栏；两个平台共用一个底部播放条

## 📷 界面预览

![QQ 音乐](screenshots/main-page.png)

<table>
  <tr>
    <td width="72%"><img src="screenshots/netease-dark.png" alt="网易云音乐（深色）"></td>
    <td width="28%"><img src="screenshots/mobile-netease.png" alt="手机端"></td>
  </tr>
  <tr>
    <td align="center">网易云音乐 · 深色模式</td>
    <td align="center">手机端</td>
  </tr>
</table>

## 🚀 安装

| | 方式一：飞牛原生应用 | 方式二：Docker |
|---|---|---|
| 适合 | 飞牛 fnOS 用户 | 任意 Linux / NAS / 服务器 |
| 运行方式 | 飞牛系统自带的 Python 3.12，不用 Docker | 容器 |
| 管理 | 应用中心里启动 / 停止 / 设置 / 卸载 | `docker compose` |
| 音乐目录 | 安装向导里直接填宿主机路径 | 卷映射 |

### 方式一：飞牛 fnOS 原生应用

可以直接到 **[Releases](https://github.com/yiyu12138/Music-monitor/releases/latest)** 下载预编译好的 `music-monitor_<版本>_x86_native.fpk`。

### 方式二：Docker

```bash
git clone https://github.com/yiyu12138/Music-monitor.git
cd Music-monitor
docker compose up -d --build
```

启动后访问 `http://<服务器地址>:6696`。默认把当前目录下的 `./data` 和 `./downloads` 挂进容器：

| 宿主机 | 容器内 | 作用 |
|---|---|---|
| `./data` | `/app/data` | 登录凭证、设备指纹、配置、任务历史、监控歌单 |
| `./downloads` | `/app/downloads` | 下载的音乐，可以直接改成 NAS 上已有的音乐目录 |

更新：`git pull && docker compose up -d --build`

<details>
<summary>不用 compose，直接 docker run</summary>

```bash
docker build -t music-monitor .
docker run -d --name Music-monitor --restart unless-stopped \
  -p 6696:6696 -e TZ=Asia/Shanghai \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/downloads:/app/downloads \
  music-monitor
```

</details>

> [!WARNING]
> `data/` 里是两个平台的登录凭证（`qq_cookie.json`、`ncm_cookie.json`），请勿分享或提交到代码库。

## 🔌 下载源

「配置 → 下载源」可以导入洛雪音乐（LX Music）格式的自定义源。社区整理的源可以在 **[pdone/lx-music-source](https://github.com/pdone/lx-music-source)** 找到：复制下面的链接，在「链接导入」里粘贴并点「添加」即可。

| 源 | QQ 音乐 | 网易云 | 导入链接 |
|---|:---:|:---:|---|
| 全豆要（聚合音源） | ✅ | ✅ | `https://raw.githubusercontent.com/pdone/lx-music-source/main/qdy/latest.js` |
| 长青 SVIP 音源 | ✅ | ✅ | `https://raw.githubusercontent.com/pdone/lx-music-source/main/changqing/latest.js` |
| 幻音音源 | ✅ | ✅ | `https://raw.githubusercontent.com/pdone/lx-music-source/main/huanyin/latest.js` |
| Huibq | ✅ | ✅ | `https://raw.githubusercontent.com/pdone/lx-music-source/main/huibq/latest.js` |
| 聚合 API 接口 | ✅ | ❌ | `https://raw.githubusercontent.com/pdone/lx-music-source/main/juhe/latest.js` |

> 上表是 2026-10-05 在本项目里实测的结果（能否加载、能否取到 320k 链接）。源随时可能失效或恢复，请以实际为准；建议添加 2～3 个作为备份，越靠上越优先。

<details>
<summary>该仓库里其它源的情况</summary>

| 源 | 结果 |
|---|---|
| 六音（sixyin） | 能加载，但取不到链接，且不提供网易云 |
| 野花（flower） | 能加载，取链时源服务器返回错误 |
| ikun | 不提供 QQ 音乐；网易云接口的域名当前无法解析 |
| 独家音源（lx） | 源服务器握手失败，无法使用 |
| 野草（grass） | 只提供酷我音乐，本项目用不上 |

</details>

- 访问 `raw.githubusercontent.com` 不稳定时，可以在链接前加加速前缀，例如 `https://ghproxy.net/` + 原链接；也可以把 `.js` 下载下来，用「本地文件」导入
- 「配置 → 下载 → **下载渠道**」决定怎么用：**两者都用**（默认，先官方账号，取不到再回退下载源）、**只用官方**、**只用下载源**
- 使用下载源时按列表顺序尝试已启用的源，每个源按 Hi-Res → 无损 → 320k → 128k 依次尝试。一个源都不加，行为与以前完全一样
- 选「只用下载源」或「两者都用」且有可用下载源时，不登录也能下载单曲（浏览歌单仍需登录）
- 源脚本来自第三方，会向第三方服务器发起请求，请只添加你信任的源。本项目不内置、不分发任何源脚本

## 📖 使用

1. 左侧（手机端在底部）切换 **QQ 音乐** / **网易云音乐**，未登录时页面中央就是登录卡片，扫码即可
2. 点开歌单查看歌曲：试听、单曲下载，或「全部下载」（本地已有的会自动跳过）
3. 点歌单旁的「监控」，之后这个歌单新增的歌会自动下载
4. 两个平台的下载、保存位置、标签歌词、通知都在「配置」页；运行输出在「日志」页
5. 想用第三方音源下载：见上面的 [🔌 下载源](#-下载源)

**保存位置规则**：单曲 / 搜索下载放到「单曲下载目录」；歌单默认放到 `<下载根目录>/<歌单名>/`，关闭「歌单按歌单名建子文件夹」后全部平铺到下载根目录。每个监控歌单还可以单独指定目录、开启「按日期建文件夹」。

<details>
<summary>数据目录里的文件</summary>

| 文件 | 内容 |
|---|---|
| `config.json` | 两个平台共用的配置 |
| `qq_cookie.json` · `device.json` | QQ 音乐登录态与设备指纹 |
| `ncm_cookie.json` | 网易云登录态 |
| `download_tasks.json` · `ncm_tasks.json` | 下载任务历史 |
| `monitored_playlists.json` · `ncm_monitored.json` | 监控中的歌单 |

</details>

**迁移**：从 huohen92/QQMusic-monitor 或 Docker 部署迁过来，把原来的 `data/` 内容放进新的数据目录即可保留登录态、监控歌单和任务历史。

## ❓ 常见问题

<details>
<summary><b>网易云提示「当前网络被风控」/ 返回 -462</b></summary>

这是网易云对服务器 IP 的风控。程序会自动在网页 / PC 客户端 / 安卓客户端三套接口之间切换重试；仍被拦截时改用 **Cookie 登录**：浏览器登录 `music.163.com` → F12 → 应用（Application）→ Cookie → 复制 `MUSIC_U` 粘贴进来。

</details>

<details>
<summary><b>下载源添加失败 / 显示「加载失败」</b></summary>

- 提示「下载脚本失败」：NAS 访问 GitHub 不稳定，换成加速链接（`https://ghproxy.net/` + 原链接）重试，或下载 `.js` 后用「本地文件」导入
- 提示「这是网页内容，不是脚本」：用的是 GitHub 的网页地址，要用 `raw.githubusercontent.com` 开头的原始链接
- 加载成功但下载时取不到链接：源本身已失效，换一个源即可（参考上面的 [🔌 下载源](#-下载源) 表格）

</details>

<details>
<summary><b>QQ 音乐下载失败或一直在重试</b></summary>

QQ 单个账号每天约有 190 首的下载限额，用完后任务会进入冷却，按「限额冷却后重试间隔」自动重试。VIP 歌曲需要会员账号。

</details>

<details>
<summary><b>Bark 通知收不到</b></summary>

多半是 `device_key` 复制错了（多带了 `/` 或少一位）。在配置页粘贴完整的 key 后点「发送测试」验证。

</details>

<details>
<summary><b>端口被占用 / 容器名冲突</b></summary>

Docker 改端口映射即可（如 `"8669:6696"`）；同名容器先 `docker rm -f Music-monitor`。安装飞牛原生应用前，也请先停掉占用同一端口的容器。

</details>

<details>
<summary><b>能用多进程（--workers）启动吗</b></summary>

不能。下载任务状态和监控批次保存在进程内存里，多进程会导致状态不一致。

</details>

## 🔨 开发

```bash
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 6696
```

<details>
<summary>模块说明</summary>

| 文件 | 作用 |
|---|---|
| `main.py` | FastAPI 入口与 QQ 音乐接口，挂载网易云路由 |
| `qq_music.py` | QQ 音乐：登录、歌单、搜索、歌词、播放链接（基于 `qqmusic-api-python`） |
| `netease_api.py` | 网易云接口客户端：weapi / eapi 加密、三通道风控切换、三种登录方式 |
| `netease_app.py` | 网易云业务：歌单、搜索、播放代理、下载队列、歌单监控（`/api/ncm/*`） |
| `tasks.py` · `monitor.py` | QQ 音乐下载队列与歌单监控 |
| `worker_pool.py` | 运行中可调整并发数的下载工作者池（两个平台共用） |
| `lx_source.py` · `sources_api.py` | 下载源：洛雪自定义源脚本的运行时（内嵌 QuickJS）、源管理与 `/api/sources/*` 接口 |
| `notification.py` | 企业微信 / Bark / Webhook 通知 |
| `config.py` · `download_paths.py` · `local_files.py` | 配置、目录规则、本地文件检索 |
| `tagging.py` | 标签、封面、歌词写入 |
| `log_store.py` | 日志页的环形缓冲 |
| `templates/` · `static/` | 页面、样式与前端逻辑 |
| `fpk/` | 飞牛 fnOS 原生应用打包工程（见 [fpk/README.md](fpk/README.md)） |

</details>

## 🙏 致谢

本项目源自 **[huohen92/QQMusic-monitor](https://github.com/huohen92/QQMusic-monitor)** 的二次开发，感谢以下项目：

- [huohen92/QQMusic-monitor](https://github.com/huohen92/QQMusic-monitor) — 本项目的直接来源：QQ 音乐下载、监控、通知、标签、日志
- [Inrrs/QQMusic-monitor](https://github.com/Inrrs/QQMusic-monitor) — 最初的 Web 框架
- [L-1124/QQMusicApi](https://github.com/L-1124/QQMusicApi) — QQ 音乐 API 核心库
- [xhongc/music-tag-web](https://github.com/xhongc/music-tag-web) — 标签写入思路，基于 [mutagen](https://mutagen.readthedocs.io/) 实现

界面中的 QQ 音乐、网易云音乐图标取自各平台官方应用图标，仅用于标识所连接的平台。

## 📄 声明

本项目仅供个人学习与音乐备份使用。请遵守 QQ 音乐、网易云音乐的用户协议及相关法律法规，勿用于商业用途或大规模分发。
