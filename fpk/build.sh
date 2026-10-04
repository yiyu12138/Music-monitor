#!/bin/bash
# 构建飞牛 fnOS 的【原生】fpk 安装包（不使用 Docker）
#
#   bash fpk/build.sh
#
# 产物：<仓库根目录>/music-monitor_<版本>_x86_native.fpk
#
# 原理：应用直接用飞牛自带的 python312 运行时运行 uvicorn，
#      依赖（site-packages）在打包机上用同一解释器装好、随包携带，安装端无需联网。
#
# 需要：
#   - 飞牛 python312 运行时：/var/apps/python312/target/bin/python3（可用 FNOS_PYTHON 覆盖）
#   - fnpack（飞牛自带，/usr/local/bin/fnpack）

set -eu

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
APPNAME="music-monitor"

VERSION="$(sed -n 's/^APP_VERSION *= *"\([^"]*\)".*/\1/p' "${ROOT}/config.py" | head -1)"
if [ -z "${VERSION}" ]; then
    echo "无法从 config.py 读取 APP_VERSION" >&2
    exit 1
fi
PY="${FNOS_PYTHON:-/var/apps/python312/target/bin/python3}"
PIP_INDEX="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

echo "==> 版本: ${VERSION}"
echo "==> Python 运行时: ${PY}"
[ -x "${PY}" ] || { echo "找不到 Python 运行时 ${PY}，请先安装飞牛 python312 应用" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
APP="${WORK}/${APPNAME}"

# 1) 元数据（manifest / 图标 / cmd / config / wizard / ui）
mkdir -p "${APP}"
cp -a "${HERE}/manifest" "${HERE}/ICON.PNG" "${HERE}/ICON_256.PNG" \
      "${HERE}/config" "${HERE}/cmd" "${HERE}/wizard" "${APP}/"
mkdir -p "${APP}/app/server" "${APP}/app/pylib"
cp -a "${HERE}/app/ui" "${APP}/app/ui"

# 2) 应用源码
tar -C "${ROOT}" \
    --exclude=./fpk \
    --exclude=./.git \
    --exclude=./data \
    --exclude=./downloads \
    --exclude=./screenshots \
    --exclude='./*.fpk' \
    -cf - . | tar -C "${APP}/app/server" -xf -

# 3) Python 依赖（与运行时同一解释器，ABI 一致）
echo "==> 安装 Python 依赖到 app/pylib"
"${PY}" -m pip install -q --no-warn-script-location --disable-pip-version-check \
    --target "${APP}/app/pylib" --index-url "${PIP_INDEX}" \
    -r "${ROOT}/requirements.txt"
find "${APP}/app/pylib" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
du -sh "${APP}/app/pylib"

# 4) 版本号与权限
sed -i "s/^version .*/version               = ${VERSION}/" "${APP}/manifest"
find "${APP}" -type d -exec chmod 755 {} +
find "${APP}" -type f -exec chmod 644 {} +
chmod +x "${APP}"/cmd/*

# 5) 自检
"${PY}" -m py_compile "${APP}/app/server/main.py" "${APP}/app/server/netease_app.py" "${APP}/cmd/seed_config.py"
"${PY}" -c "
import sys
sys.path.insert(0, '${APP}/app/pylib')
import fastapi, uvicorn, httpx, orjson, mutagen, cryptography, aiofiles, jinja2, qqmusic_api
print('依赖自检通过: fastapi', fastapi.__version__)
"

# 6) 打包
if ! command -v fnpack >/dev/null 2>&1; then
    echo "未找到 fnpack（飞牛自带 /usr/local/bin/fnpack）" >&2
    exit 1
fi
( cd "${APP}" && fnpack build )

OUT="${ROOT}/${APPNAME}_${VERSION}_x86_native.fpk"
cp "${APP}/${APPNAME}.fpk" "${OUT}"
echo "==> 完成: ${OUT}"
ls -la "${OUT}"
