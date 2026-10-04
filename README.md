# Music Monitor · 音乐歌单监控下载器

> 本项目基于 **[huohen92/QQMusic-monitor](https://github.com/huohen92/QQMusic-monitor)** 二次开发：在原版 QQ 音乐下载与歌单监控的基础上，**新增网易云音乐支持、重做了整套界面，并提供飞牛 fnOS 原生应用（fpk）打包**。感谢原作者与上游项目的贡献，来源说明见文末。

一个自托管的音乐下载与歌单监控工具：在网页上登录 **QQ 音乐 / 网易云音乐**，浏览歌单、搜索、在线试听、下载歌曲；**监控歌单，有新歌自动下载**；下载后自动写入标签、封面与歌词。适合部署在 NAS 或家用服务器。

> 当前版本：**v1.2.0** ｜ 更新记录见 [CHANGELOG.md](CHANGELOG.md)

## ✨ 功能特性

### 双平台

| | QQ 音乐 | 网易云音乐 |
|---|---|---|
| 登录方式 | QQ 扫码 / 微信扫码 / 手机号验证码 | 扫码 / 手机号（验证码或密码）/ Cookie |
| 歌单 | 自建 + 收藏 + 我喜欢 | 创建 + 收藏 + 我喜欢的音乐 |
| 搜索、试听、单曲 / 整单下载 | ✅ | ✅ |
| 歌单监控（新歌自动下载） | ✅ | ✅ |
| 音质 | 自动取最高：母带 → 全景声 → FLAC → … | 自动取最高：超清母带 → Hi-Res → 无损 → …（按账号权益降档） |
| 标签 / 封面 / 歌词 / .lrc | ✅ | ✅（同一套开关） |
| 每歌单独立目录、按日期建文件夹 | ✅ | ✅ |

### 通用能力
- **统一配置**：一个配置页管两个平台，把并发数、检查间隔、保存位置、标签歌词、通知合并成一份设置，保存后立即生效。
- **下载管理**：可取消排队/下载中的任务并清理半成品，失败可重试；「本地已存在」按目标目录实际文件判断，命中即跳过，可一键覆盖重下。
- **播放器**：两个平台共用一个底部播放条，服务端代理音频（支持拖拽进度），支持音量记忆与快捷键（空格播放、←/→ 快退快进）。
- **通知**：企业微信机器人 / Bark / Webhook，推送下载成功、失败、歌单更新、歌单汇总与登录失效；网易云消息带 `[网易云]` 前缀。
- **界面**：浅色 / 深色 / 跟随系统；桌面侧边导航、手机底部标签栏；加载骨架屏、空状态、轻提示；运行日志页。

## 📷 界面预览

![QQ 音乐](screenshots/main-page.png)

| 网易云音乐（深色） | 手机端 |
|---|---|
| ![网易云音乐](screenshots/netease-dark.png) | ![手机端](screenshots/mobile-netease.png) |

## 🚀 部署方式一：飞牛 fnOS 原生应用（fpk，推荐给飞牛用户）

可以直接到 **[Releases](https://github.com/yiyu12138/Music-monitor/releases/latest)** 下载预编译好的 `music-monitor_<版本>_x86_native.fpk`。


## 🐳 部署方式二：Docker

### docker compose（推荐，从源码构建）

`~bash
git clone https://github.com/yiyu12138/Music-monitor.git
cd Music-monitor
docker compose up -d --build
`~

仓库自带的 `docker-compose.yml` 已把 `./data` 与 `./downloads` 映射到当前目录，容器名 `Music-monitor`，端口 `6696`，时区 `Asia/Shanghai`。

### docker run

`~bash
docker build -t music-monitor:1.2.0 .
docker run -d \
  --name Music-monitor \
  --restart unless-stopped \
  -p 6696:6696 \
  -e TZ=Asia/Shanghai \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/downloads:/app/downloads \
  music-monitor:1.2.0
`~

### 目录说明

| 挂载目录 | 容器路径 | 作用 |
|---|---|---|
| `./data` | `/app/data` | 两个平台的登录凭证、设备指纹、配置、任务历史、监控歌单 |
| `./downloads` | `/app/downloads` | 下载的音乐文件（可以直接挂 NAS 上已有的音乐目录） |

### 更新

`~bash
git pull
docker compose up -d --build
`~

> ⚠️ `data/` 里是登录凭证（`qq_cookie.json`、`ncm_cookie.json`）和设备指纹，**请勿分享或提交到代码库**（`.gitignore` 已排除）。

## 🧭 两种部署方式怎么选

| | 飞牛原生 fpk | Docker |
|---|---|---|
| 适合 | 飞牛 NAS，希望像原生应用一样在应用中心管理 | 任意 Linux / NAS / 服务器 |
| 运行 | 宿主机的 python312 进程 | 容器 |
| 依赖 | 随包携带（约 30MB 的 fpk） | 镜像内置（约 90MB 压缩） |
| 音乐目录 | 向导里填宿主机路径 | 通过卷映射 |
| 升级 | 重新 `bash fpk/build.sh` 后覆盖安装 | `docker compose up -d --build` |
| 卸载 | 保留数据与音乐目录 | 删除容器与镜像，数据在挂载目录里 |

## 📖 使用说明

1. 从左侧（手机端是底部）切换 **QQ 音乐** / **网易云音乐**。
2. 登录：两边都是同一套卡片式界面 —— QQ 支持「QQ 扫码 / 微信扫码 / 手机号」，网易云支持「扫码 / 手机号 / Cookie」。
3. 点开歌单查看歌曲：可试听、单曲下载，或「全部下载」（本地已有的会自动跳过）。
4. 点歌单旁的「监控」，之后该歌单新增歌曲会自动下载；每个歌单可单独设置目录与「按日期建文件夹」。
5. 两个平台的下载设置、通知、标签与歌词都在「配置」页；「日志」页可查看运行输出。

## 🗂 数据与目录

| 文件 | 说明 |
|---|---|
| `config.json` | 两个平台共用的配置（并发、目录、歌词、通知等） |
| `qq_cookie.json` / `device.json` | QQ 音乐登录态与设备指纹 |
| `ncm_cookie.json` | 网易云登录态（含 `MUSIC_U`） |
| `download_tasks.json` / `ncm_tasks.json` | 两个平台的下载任务历史 |
| `monitored_playlists.json` / `ncm_monitored.json` | 监控中的歌单与已知歌曲列表 |

下载目录规则：单曲/搜索下载放到「单曲下载目录」；歌单下载默认放到 `<下载根目录>/<歌单名>/`，在配置页关闭「歌单按歌单名建子文件夹」后改为全部平铺到下载根目录。

## 🔁 迁移

- **从 huohen92/QQMusic-monitor 迁移**：`data/` 结构兼容，把原 `data/` 目录挂上去即可保留登录态、监控歌单与任务历史；若旧 compose 挂载了单个 `.py` 文件做覆盖，迁移前请删掉这些挂载。
- **从 Docker 迁到飞牛原生 fpk**：把原 `data/` 内容拷贝到向导里设置的应用数据目录，音乐目录填成原来挂载的那个目录即可。

## ❓ 常见问题

**网易云提示「当前网络被风控」/ 返回 -462？**
这是网易云针对服务器 IP 的风控。程序会自动在「网页 / PC 客户端 / 安卓客户端」三套接口之间切换重试；仍被拦截时，请改用 **Cookie 登录**：浏览器登录 `music.163.com` → F12 → 应用 / Application → Cookie → 复制 `MUSIC_U` 粘贴进来。已登录过的实例不受影响。

**QQ 音乐下载失败或自动重试？**
QQ 单个账号每天约 190 首限额，超出后任务会进入冷却并按「限额冷却后重试间隔」自动重试；VIP 歌曲需要会员账号。

**Bark 通知收不到？**
`device_key` 复制时容易多带一个 `/` 或少一位字符。配置页粘贴完整 key 后点「发送测试」验证（代码已会自动去掉首尾斜杠）。

**端口被占用 / 容器名冲突？**
改映射端口即可（如 `"8669:6696"`）；同名容器先用 `docker rm -f Music-monitor` 删掉再启动。装 fpk 前也请先停掉手动创建的同名容器。

**可以用 `--workers` 多进程启动吗？**
不可以。任务状态与监控批次保存在进程内存中，多 worker 会导致状态不一致。

**时区/日期文件夹不对？**
按日期建文件夹依赖容器或服务的时区，Docker 部署已用 `TZ=Asia/Shanghai`；fpk 安装时向导里可填时区。

## 🔨 本地开发

`~bash
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 6696
`~

## 📁 模块说明

| 文件 | 作用 |
|---|---|
| `main.py` | FastAPI 入口与 QQ 音乐接口，挂载网易云路由 |
| `qq_music.py` | 封装 `qqmusic-api-python`：登录、歌单、搜索、歌词、播放链接 |
| `netease_api.py` | 网易云接口客户端：weapi / eapi 加密、三通道风控切换、扫码 / 手机号 / Cookie 登录 |
| `netease_app.py` | 网易云业务模块：歌单、搜索、播放代理、下载队列、歌单监控（`/api/ncm/*`） |
| `tasks.py` / `monitor.py` | QQ 音乐下载队列与歌单监控（检查间隔两平台共用） |
| `worker_pool.py` | 可在运行中调整并发数的下载工作者池（两个平台共用） |
| `notification.py` | 企业微信 / Bark / Webhook 通知 |
| `config.py` / `download_paths.py` / `local_files.py` | 配置、下载目录规则、本地文件检索 |
| `tagging.py` | 标签、封面、歌词写入（两个平台共用） |
| `log_store.py` | 日志环形缓冲（日志页） |
| `templates/index.html` · `static/style.css` | 页面骨架与设计系统（浅/深主题、平台主题色、响应式） |
| `static/script.js` · `static/netease.js` | QQ 音乐页逻辑（含登录卡片、共用播放条、对话框、路由） · 网易云页逻辑 |
| `fpk/` | 飞牛 fnOS 原生应用打包工程（manifest、cmd、wizard、build.sh） |

## 📄 License

本项目仅供个人学习与音乐备份使用，请遵守 QQ 音乐、网易云音乐的用户协议及相关法律法规，请勿用于商业用途或大规模分发。

## 🙏 致谢与项目来源

**本项目源自 [huohen92/QQMusic-monitor](https://github.com/huohen92/QQMusic-monitor) 的二次开发**，在其 v0.9.3 基础上：

- 新增**网易云音乐**全套功能（三种登录方式与风控通道切换、歌单、搜索、试听代理、下载、监控、设置）；
- **重做整体界面**（侧边导航与移动端标签栏、平台主题色、共用登录卡片与播放条、骨架屏、轻提示、设置抽屉/配置卡片）；
- 新增**飞牛 fnOS 原生应用（fpk）打包**与文档；
- 修复若干问题（详见 [CHANGELOG.md](CHANGELOG.md)）。

上游项目：

- **[huohen92/QQMusic-monitor](https://github.com/huohen92/QQMusic-monitor)**：本项目的直接来源（QQ 音乐下载、监控、通知、标签、日志等基础能力）
- **[Inrrs/QQMusic-monitor](https://github.com/Inrrs/QQMusic-monitor)**：最初的 Web 框架（登录、歌单浏览、下载队列、歌单监控、通知）
- **[L-1124/QQMusicApi](https://github.com/L-1124/QQMusicApi)**（`qqmusic-api-python`）：QQ 音乐 API 核心库
- **[xhongc/music-tag-web](https://github.com/xhongc/music-tag-web)**：标签写入思路，基于 [mutagen](https://mutagen.readthedocs.io/) 实现
- 界面中的 QQ 音乐、网易云音乐图标取自各平台官方应用图标，仅用于标识所连接的平台
