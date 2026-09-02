#!/usr/bin/env bash
# Launch NOAI Classic Upscale (Git Bash / WSL).
set -euo pipefail
cd "$(dirname "$0")"
APP="noai_classic_upscale.py"

is_store_stub() {
  case "$1" in
    *WindowsApps*|*windowsapps*) return 0 ;;
  esac
  return 1
}

try_run() {
  local py="${1:-}"
  shift || true
  if [[ -z "$py" ]]; then
    return 1
  fi
  if is_store_stub "$py"; then
    return 1
  fi
  if [[ ! -f "$py" ]] && ! command -v "$py" >/dev/null 2>&1; then
    return 1
  fi
  if "$py" -c "import tkinter" >/dev/null 2>&1; then
    exec "$py" "$APP" "$@"
  fi
  return 1
}

UV_PY="${USERPROFILE:-$HOME}/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe"
try_run "$UV_PY" "$@" || true

if command -v uv >/dev/null 2>&1; then
  if uv run --python 3.12 python -c "import tkinter" >/dev/null 2>&1; then
    exec uv run --python 3.12 python "$APP" "$@"
  fi
fi

if command -v py >/dev/null 2>&1; then
  if py -3 -c "import tkinter" >/dev/null 2>&1; then
    exec py -3 "$APP" "$@"
  fi
fi

for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    try_run "$(command -v "$cand")" "$@" || true
  fi
done

echo "Could not find a Python with tkinter."
echo "Install Python 3, or run:"
echo "  uv python install 3.12"
echo "  uv run --python 3.12 python $APP"
exit 1
