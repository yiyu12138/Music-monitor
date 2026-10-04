# 飞牛 fnOS 应用包（fpk）

把 Music Monitor 打包成飞牛（fnOS）应用中心可直接安装的 `.fpk`。

## 构建

在装有 Docker 与 `fnpack` 的飞牛 NAS（或任意 Linux）上执行：

```bash
git clone https://github.com/yiyu12138/Music-monitor.git
cd Music-monitor
bash fpk/build.sh
```

产物：仓库根目录下的 `music-monitor_<版本>_x86.fpk`（约 90MB，已内置 Docker 镜像，安装时无需联网构建）。

## 安装

1. 打开飞牛「应用中心」→ 右上角「手动安装」，选择生成的 `.fpk`。
2. 按向导填写：访问端口（默认 6696）、音乐保存目录（默认 `/vol1/1000/music`）、应用数据目录（默认 `/var/apps/music-monitor/shares/data`）、时区。
3. 安装完成后点「打开」，即可进入 Web 界面；也可以直接用 `http://<NAS 地址>:<端口>` 访问。

安装后可在应用中心的「设置」里修改端口与目录（保存后容器会自动按新配置重建）。

## 目录与数据

| 位置 | 说明 |
|---|---|
| `/var/apps/music-monitor/target` | 应用文件（每次升级会被新版覆盖） |
| `/var/apps/music-monitor/var` | 运行日志（`main.log`、`install.log`、`config.log`） |
| `/var/apps/music-monitor/shares/data` | 应用数据：登录态、配置、任务历史、监控歌单 |
| 向导里的「音乐保存目录」 | 下载的音乐文件 |

> 从手动 Docker 部署迁移：把原来的 `data/` 目录内容拷进 `/var/apps/music-monitor/shares/data`，即可保留两个平台的登录态与监控配置。
> 装 fpk 前请先停掉手动创建的同名容器（`docker rm -f music-monitor`），否则端口与容器名会冲突。

## 打包结构

```
fpk/
├── manifest                    # 应用元信息（名称、版本、入口）
├── ICON.PNG / ICON_256.PNG     # 应用中心图标
├── config/
│   ├── privilege               # 以 root 运行（脚本需要 docker 权限）
│   └── resource                # 声明 docker-project 与数据共享目录
├── cmd/                        # 生命周期脚本：main / install / upgrade / config / uninstall
├── wizard/                     # 安装与设置向导（端口、目录、时区）
└── app/
    ├── docker/docker-compose.yaml       # 容器定义（镜像 + 端口 + 挂载，占位符取自向导）
    └── ui/config、ui/images/            # 桌面入口图标与 URL
```

向导字段会以环境变量注入，compose 里用 `${wizard_xxx:-默认值}` 引用，因此即使环境变量缺失也能用默认值启动。

## 说明

- 应用以 root 身份运行容器（`config/privilege` 的 `run-as: root`），因为安装脚本需要调用 Docker 导入镜像。
- 内置镜像的 tag 与 `config.py` 的 `APP_VERSION` 一致，升级时重新打包并导入即可覆盖。
- 卸载应用会删除容器与镜像，但**不会删除数据目录与音乐目录**。
