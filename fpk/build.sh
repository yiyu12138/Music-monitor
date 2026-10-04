#!/bin/bash
# 构建飞牛 fnOS 的 fpk 安装包（在飞牛 NAS 或任意 Linux + Docker 上执行）
#
#   bash fpk/build.sh
#
# 产物：<仓库根目录>/music-monitor_<版本>_x86.fpk
#
# 说明：
# - 镜像会在打包前导出为 app/docker/music-monitor-image.tar.gz 内置进 fpk，
#   这样目标机器无需联网、也无需现场构建即可安装。
# - 需要 fnpack（飞牛自带，/usr/local/bin/fnpack）。

set -eu

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
APPNAME="music-monitor"
IMAGE_TAG="${APPNAME}:_VERSION_"

VERSION="$(sed -n 's/^APP_VERSION *= *"\([^"]*\)".*/\1/p' "${ROOT}/config.py" | head -1)"
if [ -z "${VERSION}" ]; then
    echo "无法从 config.py 读取 APP_VERSION" >&2
    exit 1
fi
IMAGE="${APPNAME}:${VERSION}"
echo "==> 版本: ${VERSION}"

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
APP="${WORK}/${APPNAME}"

# 1) 复制打包源（排除构建脚本自身与临时产物）
mkdir -p "${APP}"
cp -a "${HERE}/." "${APP}/"
rm -f "${APP}/build.sh" "${APP}/README.md" "${APP}/.gitignore"
rm -f "${APP}/app/docker/music-monitor-image.tar.gz"

# 2) 同步版本号到 manifest 与各脚本
sed -i "s/^version .*/version               = ${VERSION}/" "${APP}/manifest"
# 把脚本与 compose 里的镜像 tag 统一改成当前版本
for f in "${APP}"/cmd/* "${APP}/app/docker/docker-compose.yaml"; do
    [ -f "${f}" ] || continue
    sed -i "s#${APPNAME}:[0-9][0-9.]*#${IMAGE}#g" "${f}"
done
echo "==> 镜像 tag 已同步为 ${IMAGE}"

# 归一化权限：Windows 上打包/传输过来的 tar 可能带出 0000 权限，应用中心会读不到
find "${APP}" -type d -exec chmod 755 {} +
find "${APP}" -type f -exec chmod 644 {} +
chmod +x "${APP}"/cmd/*
echo "==> 权限已归一化"

# 3) 准备镜像
if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    echo "==> 本地没有 ${IMAGE}，从源码构建"
    docker build -t "${IMAGE}" "${ROOT}"
fi
echo "==> 导出镜像（这一步会产生约 90MB 的压缩包）"
docker save "${IMAGE}" | gzip -1 > "${APP}/app/docker/music-monitor-image.tar.gz"
ls -la "${APP}/app/docker/music-monitor-image.tar.gz"

# 4) 校验 compose 语法（用默认值渲染）
if command -v docker >/dev/null 2>&1; then
    ( cd "${APP}/app/docker" && docker compose config >/dev/null ) && echo "==> compose 校验通过"
fi

# 5) 打包
if ! command -v fnpack >/dev/null 2>&1; then
    echo "未找到 fnpack，请先安装飞牛 fnOS 的打包工具（/usr/local/bin/fnpack）" >&2
    exit 1
fi
( cd "${APP}" && fnpack build )

OUT="${ROOT}/${APPNAME}_${VERSION}_x86.fpk"
cp "${APP}/${APPNAME}.fpk" "${OUT}"
echo "==> 完成: ${OUT}"
ls -la "${OUT}"
