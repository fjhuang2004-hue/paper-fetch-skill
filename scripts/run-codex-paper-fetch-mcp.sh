#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PAPER_FETCH_MCP_PYTHON_BIN:-python3}"
OFFLINE_ENV_FILE="$REPO_DIR/offline.env"

is_wsl() {
    [ -n "${WSL_DISTRO_NAME:-}" ] || grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null
}

default_xdg_runtime_dir() {
    printf '/run/user/%s\n' "$(id -u)"
}

ensure_wslg_env() {
    if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
        local candidate
        candidate="$(default_xdg_runtime_dir)"
        if [ -d "$candidate" ]; then
            export XDG_RUNTIME_DIR="$candidate"
        fi
    fi

    if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -n "${XDG_RUNTIME_DIR:-}" ]; then
        for candidate in wayland-0 wayland-1; do
            if [ -S "$XDG_RUNTIME_DIR/$candidate" ]; then
                export WAYLAND_DISPLAY="$candidate"
                break
            fi
        done
    fi

    if [ -z "${DISPLAY:-}" ]; then
        export DISPLAY=":0"
    fi
}

load_offline_env_if_present() {
    if [ -f "$OFFLINE_ENV_FILE" ] && [ -z "${PAPER_FETCH_ENV_FILE:-}" ]; then
        export PAPER_FETCH_ENV_FILE="$OFFLINE_ENV_FILE"
        set -a
        # shellcheck disable=SC1090
        source "$OFFLINE_ENV_FILE"
        set +a
    fi
}

main() {
    load_offline_env_if_present
    if is_wsl; then
        ensure_wslg_env
    fi

    exec "$PYTHON_BIN" -m paper_fetch.mcp.server "$@"
}

main "$@"
