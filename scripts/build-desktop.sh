#!/usr/bin/env bash
# 构建 xihe 桌面安装包（Linux/macOS）。
# 流程：① PyInstaller 打包 CLI → ② 内嵌到 desktop/resources/bin → ③ electron-builder 出包。
# 产物：desktop/release/*.AppImage / *.deb（linux）；*.dmg（mac）。
#
# 用法：
#   bash scripts/build-desktop.sh          # 自动检测系统（Linux→linux 包；macOS→dmg）
#   bash scripts/build-desktop.sh linux    # 强制构建 Linux 包
#   bash scripts/build-desktop.sh mac      # 强制构建 macOS 包（需在 macOS 上执行）
#   XIHE_SKIP_CLI=1 bash scripts/build-desktop.sh   # 跳过 CLI 构建，仅打桌面（适合只改前端）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-}"
if [ -z "$TARGET" ]; then
  case "$(uname -s)" in
    Darwin*) TARGET="mac" ;;
    *) TARGET="linux" ;;
  esac
fi
cd "$ROOT"

# ① CLI 打包并内嵌
if [ "${XIHE_SKIP_CLI:-0}" = "1" ]; then
  echo "[1/3] 跳过 CLI 构建（XIHE_SKIP_CLI=1），使用现有 desktop/resources/bin"
else
  echo "[1/3] 构建 CLI 并内嵌到桌面包"
  bash scripts/build-cli.sh
  mkdir -p desktop/resources/bin
  rm -rf desktop/resources/bin/xihe
  cp -r dist/cli/xihe desktop/resources/bin/xihe
  chmod +x desktop/resources/bin/xihe/xihe
fi

# ② 携带配置模板（桌面端首启/首次保存时用于初始化完整 config.yaml）
echo "[2/4] 内嵌 config.example.yaml 模板"
cp "$ROOT/config.example.yaml" "$ROOT/desktop/resources/config.example.yaml"

# ③ 构建桌面 bundle（electron-vite）
echo "[3/4] electron-vite 构建 renderer/main/preload"
cd desktop
if [ ! -d node_modules ]; then
  npm install
fi
npm run build

# ④ electron-builder 出安装包
echo "[4/4] electron-builder 打包 ($TARGET)"
npx electron-builder --"$TARGET"

echo ""
echo "完成。安装包在: $ROOT/desktop/release/"
ls -lh "$ROOT/desktop/release/" | grep -vE "builder-debug|builder-effective|\.yml$" || true
