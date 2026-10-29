#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP="$SCRIPT_DIR/../.local/Figma Plugin Experiment.app"

if [[ ! -d "$APP" ]]; then
  echo "实验副本不存在，请先运行 python3 scripts/patch-figma.py。" >&2
  exit 1
fi
if pgrep -f '^/Applications/Figma[.]app/Contents/MacOS/Figma([[:space:]]|$)' >/dev/null; then
  echo "请先通过 Figma → Quit 退出原版，再运行此脚本。副本默认使用现有 Figma 本地配置。" >&2
  exit 1
fi
codesign --verify --deep --strict "$APP"
exec "$APP/Contents/MacOS/Figma" "$@"
