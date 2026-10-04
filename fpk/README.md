# 飞牛 fnOS 原生应用包（fpk）

把 Music Monitor 打包成飞牛（fnOS）应用中心可直接安装的**原生应用** fpk：**不使用 Docker**，直接用飞牛自带的 Python 3.12 运行时跑服务。

## 构建

在飞牛 NAS（或任意装有飞牛 python312 运行时与 `fnpack` 的 Linux）上执行：

`~/bash
git clone https://github.com/yiyu12138/Music-monitor.git
cd Music-monitor
bash fpk/build.sh
`~

产物：仓库根目录下的 `music-monitor_<版本>_x86_native.fpk`（约 30~40MB，依赖已随包携带，安装端无需联网）。

## 安装

1. 应用中心确认已安装 **Python 3.12**（`python312`）；缺失时 fpk 的 `install_dep_apps` 会提示先安装。
2. 应用中心 → 右上角「手动安装」，选择生成的 `.fpk`。
3. 按向导填写：
   - 访问端口（默认 `6696`）
   - 音乐保存目录（默认 `/vol1/1000/music`，就是实际保存路径，不再经过容器映射）
   - 应用数据目录（默认 `/var/apps/music-monitor/var/data`）
   - 时区（默认 `Asia/Shanghai`）
4. 安装完成后点「打开」，或直接访问 `http://<NAS 地址>:<端口>`。

安装后在应用设置的「环境变量」里改端口或目录，保存时会**自动写入配置并重启服务**。

## 运行方式

| 项目 | 说明 |
|---|---|
| 运行时 | `/var/apps/python312/target/bin/python3`（飞牛 python312 应用） |
| 应用文件 | `/var/apps/music-monitor/target/server`（源码）、`.../target/pylib`（依赖） |
| 启动命令 | `python3 -m uvicorn main:app --host 0.0.0.0 --port <端口>` |
| 运行目录 | 向导里的「应用数据目录」（`data/`、`downloads/` 以及 `templates`、`static` 软链都在这里） |
| 日志 | `/var/apps/music-monitor/var/app.log`（另有 `install.log`、`config.log`） |
| 进程管理 | `cmd/main start|stop|status`，PID 记在 `/var/apps/music-monitor/var/app.pid` |

> 从 Docker 部署迁移：把原来的 `data/` 内容拷进向导里设置的「应用数据目录」，即可保留两个平台的登录态、监控歌单与任务历史；音乐目录填成原来挂载的那个目录。
> 装 fpk 前请先停掉手动创建的同名容器（`docker rm -f music-monitor`），避免端口冲突。

## 打包结构

`~
fpk/
├── manifest                    # 应用元信息（appname / version / install_dep_apps=python312 / 桌面入口）
├── ICON.PNG / ICON_256.PNG     # 应用中心图标
├── config/
│   ├── privilege               # run-as root（需要写音乐目录、管理自身进程）
│   └── resource                # 原生应用为空 {}
├── cmd/                        # main（启动/停止/状态）、install/upgrade/config/uninstall 回调、seed_config.py
├── wizard/                     # 安装与设置向导（端口、音乐目录、数据目录、时区）
└── app/                        # 打进 app.tgz，解包到 /var/apps/music-monitor/target
    ├── server/                 # 应用源码（本项目全部文件）
    ├── pylib/                  # Python 依赖（pip --target，与 python312 同解释器）
    └── ui/config、ui/images/   # 桌面入口图标与 URL
`~

向导字段会以环境变量注入到 `cmd/*` 脚本（`wizard_port`、`wizard_music_dir`、`wizard_data_dir`、`wizard_tz`），脚本里用 `${wizard_port:-默认值}` 的形式取值。

## 说明

- 依赖在**打包机**上用与运行端相同的 Python 3.12 解释器安装，二进制扩展 ABI 一致，因此安装端不需要联网装包。
- 卸载会停止服务，但**保留应用数据目录与音乐目录**。
- 升级：重新 `bash fpk/build.sh` 得到新版 fpk，直接安装覆盖即可，数据与配置都会沿用。
