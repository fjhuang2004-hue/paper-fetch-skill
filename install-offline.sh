#!/usr/bin/env bash
# Offline installer for the Linux x86_64 CPython ABI-specific bundle.

set -euo pipefail

BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PAPER_FETCH_OFFLINE_PYTHON_BIN:-python3}"
PRESET="headless"
MERGE_USER_CONFIG=0
RUN_SMOKE=1
UNINSTALL=0
OFFLINE_ENV_FILE="$BUNDLE_ROOT/offline.env"
REUSE_ENV_FILE=0
INSTALLER_MANIFEST_FILE="$BUNDLE_ROOT/installer/manifest.json"

MANAGED_BEGIN="# BEGIN paper-fetch offline managed"
MANAGED_END="# END paper-fetch offline managed"
CODEX_MANAGED_BEGIN="# BEGIN paper-fetch installer managed"
CODEX_MANAGED_END="# END paper-fetch installer managed"
SKILL_NAME="paper-fetch-skill"
MCP_NAME="paper-fetch"
MCP_ENV_KEYS=(
  PYTHONUTF8
  PYTHONIOENCODING
  PAPER_FETCH_ENV_FILE
  PAPER_FETCH_MCP_PYTHON_BIN
  PAPER_FETCH_DOWNLOAD_DIR
  PAPER_FETCH_FORMULA_TOOLS_DIR
  CLOAKBROWSER_HEADLESS
)

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

load_installer_manifest() {
  if [ ! -f "$INSTALLER_MANIFEST_FILE" ]; then
    [ "$UNINSTALL" = "1" ] && return 0
    die "Missing installer manifest: $INSTALLER_MANIFEST_FILE"
  fi
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    [ "$UNINSTALL" = "1" ] && return 0
    die "$PYTHON_BIN was not found on PATH; cannot read installer manifest."
  fi

  local values
  mapfile -t values < <("$PYTHON_BIN" -c '
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
print("installer_manifest_values")
print(manifest["managed_blocks"]["offline"]["begin"])
print(manifest["managed_blocks"]["offline"]["end"])
print(manifest["managed_blocks"]["codex"]["begin"])
print(manifest["managed_blocks"]["codex"]["end"])
print(manifest["skill"]["name"])
print(manifest["mcp"]["name"])
for key in manifest["mcp"]["env_keys"]:
    print(key)
' "$INSTALLER_MANIFEST_FILE")

  [ "${values[0]:-}" = "installer_manifest_values" ] || die "Invalid installer manifest payload from $INSTALLER_MANIFEST_FILE"
  MANAGED_BEGIN="${values[1]:-}"
  MANAGED_END="${values[2]:-}"
  CODEX_MANAGED_BEGIN="${values[3]:-}"
  CODEX_MANAGED_END="${values[4]:-}"
  SKILL_NAME="${values[5]:-}"
  MCP_NAME="${values[6]:-}"
  MCP_ENV_KEYS=("${values[@]:7}")
  normalize_mcp_env_keys

  [ -n "$MANAGED_BEGIN" ] || die "installer manifest is missing managed_blocks.offline.begin"
  [ -n "$MANAGED_END" ] || die "installer manifest is missing managed_blocks.offline.end"
  [ -n "$CODEX_MANAGED_BEGIN" ] || die "installer manifest is missing managed_blocks.codex.begin"
  [ -n "$CODEX_MANAGED_END" ] || die "installer manifest is missing managed_blocks.codex.end"
  [ -n "$SKILL_NAME" ] || die "installer manifest is missing skill.name"
  [ -n "$MCP_NAME" ] || die "installer manifest is missing mcp.name"
  [ "${#MCP_ENV_KEYS[@]}" -gt 0 ] || die "installer manifest is missing mcp.env_keys"
}

usage() {
  cat <<'EOF'
Usage:
  ./install-offline.sh [--preset=headless|wslg] [--user-config] [--reuse-env-file <path>]
  ./install-offline.sh --uninstall

Options:
  --preset=headless|wslg  Select CloakBrowser headless/headful runtime env. Default: headless.
  --user-config           Also merge the offline runtime block into ~/.config/paper-fetch/.env.
  --no-user-config        Do not touch ~/.config/paper-fetch/.env. This is the default.
  --reuse-env-file <path> Use an existing offline.env without modifying it.
  --skip-smoke            Skip local command smoke checks after installation.
  --uninstall             Remove user-level shell, skill, and MCP integration without deleting this bundle.
  -h, --help              Show this help.

Environment:
  CLOAKBROWSER_HEADLESS     Set to false for a headful CloakBrowser runtime.
  CLOAKBROWSER_BINARY_PATH  Optional path to a preinstalled browser binary; when set,
                            CloakBrowser runtime download is skipped.
EOF
}

normalize_mcp_env_keys() {
  local key seen_headless=0
  local filtered=()
  for key in "${MCP_ENV_KEYS[@]}"; do
    case "$key" in
      PLAYWRIGHT_BROWSERS_PATH|FLARESOLVERR_URL|FLARESOLVERR_ENV_FILE|FLARESOLVERR_SOURCE_DIR)
        continue
        ;;
      CLOAKBROWSER_HEADLESS)
        seen_headless=1
        ;;
    esac
    filtered+=("$key")
  done
  if [ "$seen_headless" != "1" ]; then
    filtered+=(CLOAKBROWSER_HEADLESS)
  fi
  MCP_ENV_KEYS=("${filtered[@]}")
}

normalize_path() {
  local value="$1"
  case "$value" in
    "~")
      [ -n "${HOME:-}" ] || die "HOME is required to expand ~."
      printf '%s\n' "$HOME"
      ;;
    "~/"*)
      [ -n "${HOME:-}" ] || die "HOME is required to expand ~."
      printf '%s/%s\n' "$HOME" "${value#~/}"
      ;;
    /*)
      printf '%s\n' "$value"
      ;;
    *)
      printf '%s/%s\n' "$(pwd)" "$value"
      ;;
  esac
}

while (($#)); do
  case "$1" in
    --preset=*)
      PRESET="${1#*=}"
      ;;
    --preset)
      shift
      [ "$#" -gt 0 ] || die "--preset requires headless or wslg"
      PRESET="$1"
      ;;
    --user-config)
      MERGE_USER_CONFIG=1
      ;;
    --no-user-config)
      MERGE_USER_CONFIG=0
      ;;
    --reuse-env-file=*)
      OFFLINE_ENV_FILE="$(normalize_path "${1#*=}")"
      REUSE_ENV_FILE=1
      ;;
    --reuse-env-file)
      shift
      [ "$#" -gt 0 ] || die "--reuse-env-file requires a path"
      OFFLINE_ENV_FILE="$(normalize_path "$1")"
      REUSE_ENV_FILE=1
      ;;
    --skip-smoke)
      RUN_SMOKE=0
      ;;
    --uninstall)
      UNINSTALL=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
  shift
done

if [ "$UNINSTALL" != "1" ]; then
  case "$PRESET" in
    headless|wslg) ;;
    *) die "--preset must be headless or wslg" ;;
  esac
  if [ "$REUSE_ENV_FILE" = "1" ]; then
    [ -f "$OFFLINE_ENV_FILE" ] || die "Missing reusable offline env file: $OFFLINE_ENV_FILE"
  fi
fi

require_file() {
  [ -f "$1" ] || die "Missing required bundled file: $1"
}

require_dir() {
  [ -d "$1" ] || die "Missing required bundled directory: $1"
}

quote_env_value() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\$/\\$}"
  value="${value//\`/\\\`}"
  printf '"%s"' "$value"
}

quote_toml_value() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "$value"
}

mcp_name_regex() {
  printf '%s' "$MCP_NAME" | sed 's/[][\\.^$*+?{}|()]/\\&/g'
}

check_platform() {
  local kernel machine
  kernel="$(uname -s)"
  machine="$(uname -m)"
  [ "$kernel" = "Linux" ] || die "This offline bundle supports Linux only; detected $kernel."
  case "$machine" in
    x86_64|amd64) ;;
    *) die "This offline bundle supports x86_64 only; detected $machine." ;;
  esac
}

check_python() {
  command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "python3 was not found on PATH."
  require_file "$BUNDLE_ROOT/offline-manifest.json"

  local version tag manifest_tag
  version="$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  tag="$("$PYTHON_BIN" -c 'import sys; print(f"cp{sys.version_info.major}{sys.version_info.minor}" if sys.implementation.name == "cpython" else sys.implementation.name)')"
  manifest_tag="$("$PYTHON_BIN" -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("target", {}).get("python_tag", ""))' "$BUNDLE_ROOT/offline-manifest.json")"
  [ -n "$manifest_tag" ] || die "offline-manifest.json is missing target.python_tag."
  [ "$tag" = "$manifest_tag" ] || die "bundle requires CPython $manifest_tag; detected Python $version ($tag)."
}

verify_checksums() {
  require_file "$BUNDLE_ROOT/sha256sums.txt"
  command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required to verify the offline bundle."
  log "Verifying bundled file checksums"
  (cd "$BUNDLE_ROOT" && sha256sum --check sha256sums.txt --quiet)
}

find_project_wheel() {
  shopt -s nullglob
  local wheels=("$BUNDLE_ROOT"/dist/paper_fetch_skill-*.whl)
  if [ "${#wheels[@]}" -eq 0 ]; then
    wheels=("$BUNDLE_ROOT"/wheelhouse/paper_fetch_skill-*.whl)
  fi
  shopt -u nullglob
  [ "${#wheels[@]}" -eq 1 ] || die "Expected exactly one paper_fetch_skill wheel, found ${#wheels[@]}."
  printf '%s\n' "${wheels[0]}"
}

check_preset_requirements() {
  if [ "$PRESET" = "wslg" ] && [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    die "DISPLAY or WAYLAND_DISPLAY is required for --preset=wslg."
  fi
}

cloakbrowser_headless_value() {
  if [ "$PRESET" = "wslg" ]; then
    printf 'false\n'
  else
    printf 'true\n'
  fi
}

check_bundle_assets() {
  require_dir "$BUNDLE_ROOT/wheelhouse"
  require_file "$BUNDLE_ROOT/formula-tools/bin/texmath"
  [ -x "$BUNDLE_ROOT/formula-tools/bin/texmath" ] || die "Bundled texmath is not executable: $BUNDLE_ROOT/formula-tools/bin/texmath"

  shopt -s nullglob
  local cloakbrowser_wheels=("$BUNDLE_ROOT"/wheelhouse/cloakbrowser-*.whl)
  shopt -u nullglob
  [ "${#cloakbrowser_wheels[@]}" -gt 0 ] || die "Bundled wheelhouse is missing cloakbrowser-*.whl."

  require_file "$BUNDLE_ROOT/skills/$SKILL_NAME/SKILL.md"
}

install_project_venv() {
  local project_wheel="$1"
  local venv_dir="$BUNDLE_ROOT/.venv"

  if [ ! -x "$venv_dir/bin/python" ]; then
    log "Creating Python virtual environment at $venv_dir"
    "$PYTHON_BIN" -m venv "$venv_dir"
  fi

  export PIP_NO_INDEX=1
  export PIP_FIND_LINKS="$BUNDLE_ROOT/wheelhouse"
  export PIP_DISABLE_PIP_VERSION_CHECK=1
  export PIP_NO_BUILD_ISOLATION=1
  export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

  log "Installing paper-fetch-skill from bundled wheelhouse"
  "$venv_dir/bin/python" -m pip install \
    --no-index \
    --find-links "$BUNDLE_ROOT/wheelhouse" \
    --only-binary=:all: \
    "$project_wheel"

  [ -x "$venv_dir/bin/paper-fetch" ] || die "Installed CLI is missing or not executable: $venv_dir/bin/paper-fetch"
  [ -x "$venv_dir/bin/paper-fetch-mcp" ] || die "Installed MCP CLI is missing or not executable: $venv_dir/bin/paper-fetch-mcp"
}

mcp_python_bin() {
  printf '%s\n' "$BUNDLE_ROOT/.venv/bin/python"
}

mcp_env_value() {
  local key="$1"
  case "$key" in
    PYTHONUTF8) printf '1\n' ;;
    PYTHONIOENCODING) printf 'utf-8\n' ;;
    PAPER_FETCH_ENV_FILE) printf '%s\n' "$OFFLINE_ENV_FILE" ;;
    PAPER_FETCH_MCP_PYTHON_BIN) mcp_python_bin ;;
    PAPER_FETCH_DOWNLOAD_DIR) printf '%s\n' "$BUNDLE_ROOT/downloads" ;;
    PAPER_FETCH_FORMULA_TOOLS_DIR) printf '%s\n' "$BUNDLE_ROOT/formula-tools" ;;
    CLOAKBROWSER_HEADLESS) cloakbrowser_headless_value ;;
    *) die "Unknown MCP env key: $key" ;;
  esac
}

copy_installed_skill() {
  local destination="$1"
  local source="$BUNDLE_ROOT/skills/$SKILL_NAME"

  require_file "$source/SKILL.md"
  rm -rf "$destination"
  mkdir -p "$destination"
  cp -a "$source/." "$destination/"
}

install_skills() {
  [ -n "${HOME:-}" ] || die "HOME is required to install Codex, Claude, and Gemini skills."

  local codex_skill="$HOME/.codex/skills/$SKILL_NAME"
  local claude_skill="$HOME/.claude/skills/$SKILL_NAME"
  local gemini_skill="$HOME/.gemini/skills/$SKILL_NAME"

  log "Installing Codex skill to $codex_skill"
  copy_installed_skill "$codex_skill"
  log "Installing Claude Code skill to $claude_skill"
  copy_installed_skill "$claude_skill"
  log "Installing Gemini CLI skill to $gemini_skill"
  copy_installed_skill "$gemini_skill"
}

select_shell_startup_file() {
  [ -n "${HOME:-}" ] || die "HOME is required to update shell startup files."

  SHELL_STARTUP_STYLE="posix"
  case "$(basename "${SHELL:-}")" in
    bash)
      SHELL_STARTUP_TARGET="$HOME/.bashrc"
      ;;
    zsh)
      SHELL_STARTUP_TARGET="$HOME/.zshrc"
      ;;
    fish)
      SHELL_STARTUP_TARGET="$HOME/.config/fish/conf.d/paper-fetch-offline.fish"
      SHELL_STARTUP_STYLE="fish"
      ;;
    *)
      SHELL_STARTUP_TARGET="$HOME/.profile"
      warn "Unrecognized SHELL=${SHELL:-}; writing offline environment to $SHELL_STARTUP_TARGET"
      ;;
  esac
}

write_posix_shell_block() {
  printf '%s\n' "$MANAGED_BEGIN"
  printf 'export PATH=%s:%s:$PATH\n' "$(quote_env_value "$BUNDLE_ROOT/.venv/bin")" "$(quote_env_value "$BUNDLE_ROOT/formula-tools/bin")"
  printf 'export PAPER_FETCH_ENV_FILE=%s\n' "$(quote_env_value "$OFFLINE_ENV_FILE")"
  printf 'export PAPER_FETCH_DOWNLOAD_DIR=%s\n' "$(quote_env_value "$BUNDLE_ROOT/downloads")"
  printf 'export PAPER_FETCH_FORMULA_TOOLS_DIR=%s\n' "$(quote_env_value "$BUNDLE_ROOT/formula-tools")"
  printf 'export CLOAKBROWSER_HEADLESS=%s\n' "$(quote_env_value "$(cloakbrowser_headless_value)")"
  printf '%s\n' "$MANAGED_END"
}

write_fish_shell_block() {
  printf '%s\n' "$MANAGED_BEGIN"
  printf 'set -gx PATH %s %s $PATH\n' "$(quote_env_value "$BUNDLE_ROOT/.venv/bin")" "$(quote_env_value "$BUNDLE_ROOT/formula-tools/bin")"
  printf 'set -gx PAPER_FETCH_ENV_FILE %s\n' "$(quote_env_value "$OFFLINE_ENV_FILE")"
  printf 'set -gx PAPER_FETCH_DOWNLOAD_DIR %s\n' "$(quote_env_value "$BUNDLE_ROOT/downloads")"
  printf 'set -gx PAPER_FETCH_FORMULA_TOOLS_DIR %s\n' "$(quote_env_value "$BUNDLE_ROOT/formula-tools")"
  printf 'set -gx CLOAKBROWSER_HEADLESS %s\n' "$(quote_env_value "$(cloakbrowser_headless_value)")"
  printf '%s\n' "$MANAGED_END"
}

write_shell_startup_file() {
  local tmp mode

  select_shell_startup_file
  tmp="$(mktemp)"
  mode=""
  mkdir -p "$(dirname "$SHELL_STARTUP_TARGET")"
  if [ -f "$SHELL_STARTUP_TARGET" ]; then
    mode="$(stat -c '%a' "$SHELL_STARTUP_TARGET" 2>/dev/null || true)"
    awk -v begin="$MANAGED_BEGIN" -v end="$MANAGED_END" '
      $0 == begin { skip = 1; next }
      $0 == end { skip = 0; next }
      !skip { print }
    ' "$SHELL_STARTUP_TARGET" > "$tmp"
  else
    : > "$tmp"
  fi

  {
    printf '\n'
    if [ "$SHELL_STARTUP_STYLE" = "fish" ]; then
      write_fish_shell_block
    else
      write_posix_shell_block
    fi
  } >> "$tmp"

  mv "$tmp" "$SHELL_STARTUP_TARGET"
  if [ -n "$mode" ]; then
    chmod "$mode" "$SHELL_STARTUP_TARGET"
  fi
  log "Updated shell startup file at $SHELL_STARTUP_TARGET"
}

write_codex_config_toml() {
  [ -n "${HOME:-}" ] || die "HOME is required to update Codex MCP config."

  local codex_home="$HOME/.codex"
  local config_path="$codex_home/config.toml"
  local tmp key mcp_table_re
  tmp="$(mktemp)"
  mcp_table_re="^[[:space:]]*[[]mcp_servers[.]$(mcp_name_regex)([.].*)?[]][[:space:]]*$"
  mkdir -p "$codex_home"

  if [ -f "$config_path" ]; then
    awk -v begin="$CODEX_MANAGED_BEGIN" -v end="$CODEX_MANAGED_END" -v old_begin="$MANAGED_BEGIN" -v old_end="$MANAGED_END" -v mcp_table_re="$mcp_table_re" '
      $0 == begin || $0 == old_begin { skip_block = 1; next }
      $0 == end || $0 == old_end { skip_block = 0; next }
      skip_block { next }
      $0 ~ mcp_table_re { skip_table = 1; next }
      skip_table && $0 ~ /^[[:space:]]*\[/ { skip_table = 0 }
      !skip_table { print }
    ' "$config_path" > "$tmp"
  else
    : > "$tmp"
  fi

  {
    printf '\n%s\n' "$CODEX_MANAGED_BEGIN"
    printf '[mcp_servers.%s]\n' "$MCP_NAME"
    printf 'command = %s\n' "$(quote_toml_value "$(mcp_python_bin)")"
    printf 'args = ["-X", "utf8", "-m", "paper_fetch.mcp.server"]\n'
    printf '\n[mcp_servers.%s.env]\n' "$MCP_NAME"
    for key in "${MCP_ENV_KEYS[@]}"; do
      printf '%s = %s\n' "$key" "$(quote_toml_value "$(mcp_env_value "$key")")"
    done
    printf '%s\n' "$CODEX_MANAGED_END"
  } >> "$tmp"

  mv "$tmp" "$config_path"
  log "Updated Codex MCP config at $config_path"
}

register_codex_mcp() {
  local codex_bin key
  codex_bin="$(command -v codex || true)"

  if [ -n "$codex_bin" ]; then
    log "Registering Codex MCP server '$MCP_NAME' with Codex CLI"
    "$codex_bin" mcp remove "$MCP_NAME" >/dev/null 2>&1 || true

    local args=(mcp add)
    for key in "${MCP_ENV_KEYS[@]}"; do
      args+=(--env "$key=$(mcp_env_value "$key")")
    done
    args+=("$MCP_NAME" -- "$(mcp_python_bin)" -X utf8 -m paper_fetch.mcp.server)

    if "$codex_bin" "${args[@]}"; then
      return
    fi
    warn "Codex CLI MCP registration failed; falling back to $HOME/.codex/config.toml"
  fi

  write_codex_config_toml
}

register_claude_mcp() {
  local claude_bin key
  claude_bin="$(command -v claude || true)"

  if [ -z "$claude_bin" ]; then
    log "Claude CLI not found; installed the skill and skipped Claude MCP registration"
    return
  fi

  log "Registering Claude MCP server '$MCP_NAME' with Claude CLI"
  "$claude_bin" mcp remove -s user "$MCP_NAME" >/dev/null 2>&1 || true

  local args=(mcp add -s user)
  for key in "${MCP_ENV_KEYS[@]}"; do
    args+=(-e "$key=$(mcp_env_value "$key")")
  done
  args+=("$MCP_NAME" -- "$(mcp_python_bin)" -X utf8 -m paper_fetch.mcp.server)

  if ! "$claude_bin" "${args[@]}"; then
    warn "Claude MCP registration failed and was skipped."
  fi
}

register_gemini_mcp() {
  local gemini_bin key
  gemini_bin="$(command -v gemini || true)"

  if [ -z "$gemini_bin" ]; then
    log "Gemini CLI not found; installed the skill and skipped Gemini MCP registration"
    return
  fi

  log "Registering Gemini MCP server '$MCP_NAME' with Gemini CLI"
  "$gemini_bin" mcp remove "$MCP_NAME" >/dev/null 2>&1 || true

  local args=(mcp add)
  for key in "${MCP_ENV_KEYS[@]}"; do
    args+=(--env "$key=$(mcp_env_value "$key")")
  done
  args+=("$MCP_NAME" -- "$(mcp_python_bin)" -X utf8 -m paper_fetch.mcp.server)

  if ! "$gemini_bin" "${args[@]}"; then
    warn "Gemini MCP registration failed and was skipped."
  fi
}

remove_managed_block_from_file() {
  local target="$1"
  local remove_if_empty="${2:-0}"
  local tmp mode

  [ -f "$target" ] || return 0
  tmp="$(mktemp)"
  mode="$(stat -c '%a' "$target" 2>/dev/null || true)"
  awk -v begin="$MANAGED_BEGIN" -v end="$MANAGED_END" '
    $0 == begin { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
  ' "$target" > "$tmp"

  if [ "$remove_if_empty" = "1" ] && ! grep -q '[^[:space:]]' "$tmp"; then
    rm -f "$tmp" "$target"
    log "Removed empty managed file $target"
    return 0
  fi

  mv "$tmp" "$target"
  if [ -n "$mode" ]; then
    chmod "$mode" "$target"
  fi
  log "Removed managed shell block from $target"
}

remove_shell_startup_blocks() {
  [ -n "${HOME:-}" ] || die "HOME is required for --uninstall."

  remove_managed_block_from_file "$HOME/.bashrc"
  remove_managed_block_from_file "$HOME/.zshrc"
  remove_managed_block_from_file "$HOME/.profile"
  remove_managed_block_from_file "$HOME/.config/fish/conf.d/paper-fetch-offline.fish" 1
}

remove_installed_skills() {
  [ -n "${HOME:-}" ] || die "HOME is required for --uninstall."

  local codex_skill="$HOME/.codex/skills/$SKILL_NAME"
  local claude_skill="$HOME/.claude/skills/$SKILL_NAME"
  local gemini_skill="$HOME/.gemini/skills/$SKILL_NAME"

  rm -rf "$codex_skill" "$claude_skill" "$gemini_skill"
  log "Removed Codex skill at $codex_skill"
  log "Removed Claude Code skill at $claude_skill"
  log "Removed Gemini CLI skill at $gemini_skill"
}

remove_codex_config_toml() {
  [ -n "${HOME:-}" ] || die "HOME is required for --uninstall."

  local config_path="$HOME/.codex/config.toml"
  local tmp mode mcp_table_re
  [ -f "$config_path" ] || return 0

  tmp="$(mktemp)"
  mode="$(stat -c '%a' "$config_path" 2>/dev/null || true)"
  mcp_table_re="^[[:space:]]*[[]mcp_servers[.]$(mcp_name_regex)([.].*)?[]][[:space:]]*$"
  awk -v begin="$CODEX_MANAGED_BEGIN" -v end="$CODEX_MANAGED_END" -v old_begin="$MANAGED_BEGIN" -v old_end="$MANAGED_END" -v mcp_table_re="$mcp_table_re" '
    $0 == begin || $0 == old_begin { skip_block = 1; next }
    $0 == end || $0 == old_end { skip_block = 0; next }
    skip_block { next }
    $0 ~ mcp_table_re { skip_table = 1; next }
    skip_table && $0 ~ /^[[:space:]]*\[/ { skip_table = 0 }
    !skip_table { print }
  ' "$config_path" > "$tmp"

  mv "$tmp" "$config_path"
  if [ -n "$mode" ]; then
    chmod "$mode" "$config_path"
  fi
  log "Removed Codex MCP config from $config_path"
}

unregister_codex_mcp() {
  local codex_bin
  codex_bin="$(command -v codex || true)"
  if [ -n "$codex_bin" ]; then
    log "Removing Codex MCP server '$MCP_NAME' with Codex CLI"
    "$codex_bin" mcp remove "$MCP_NAME" >/dev/null 2>&1 || true
  fi
  remove_codex_config_toml
}

unregister_claude_mcp() {
  local claude_bin
  claude_bin="$(command -v claude || true)"
  if [ -n "$claude_bin" ]; then
    log "Removing Claude MCP server '$MCP_NAME' with Claude CLI"
    "$claude_bin" mcp remove -s user "$MCP_NAME" >/dev/null 2>&1 || true
  else
    log "Claude CLI not found; skipped Claude MCP removal"
  fi
}

unregister_gemini_mcp() {
  local gemini_bin
  gemini_bin="$(command -v gemini || true)"
  if [ -n "$gemini_bin" ]; then
    log "Removing Gemini MCP server '$MCP_NAME' with Gemini CLI"
    "$gemini_bin" mcp remove "$MCP_NAME" >/dev/null 2>&1 || true
  else
    log "Gemini CLI not found; skipped Gemini MCP removal"
  fi
}

uninstall_user_integrations() {
  remove_installed_skills
  remove_shell_startup_blocks
  unregister_codex_mcp
  unregister_claude_mcp
  unregister_gemini_mcp

  echo
  echo "Offline user-level integration removed."
  echo "Bundle files were left in place: $BUNDLE_ROOT"
}

write_managed_env_file() {
  local target="$1"
  local tmp
  tmp="$(mktemp)"

  mkdir -p "$(dirname "$target")"
  if [ -f "$target" ]; then
    awk -v begin="$MANAGED_BEGIN" -v end="$MANAGED_END" '
      $0 == begin { skip = 1; next }
      $0 == end { skip = 0; next }
      !skip { print }
    ' "$target" > "$tmp"
  elif [ -f "$BUNDLE_ROOT/.env.example" ]; then
    cp "$BUNDLE_ROOT/.env.example" "$tmp"
  else
    : > "$tmp"
  fi

  {
    printf '\n%s\n' "$MANAGED_BEGIN"
    printf 'PAPER_FETCH_DOWNLOAD_DIR=%s\n' "$(quote_env_value "$BUNDLE_ROOT/downloads")"
    printf 'PAPER_FETCH_FORMULA_TOOLS_DIR=%s\n' "$(quote_env_value "$BUNDLE_ROOT/formula-tools")"
    printf 'CLOAKBROWSER_HEADLESS=%s\n' "$(quote_env_value "$(cloakbrowser_headless_value)")"
    printf '# CLOAKBROWSER_BINARY_PATH="/absolute/path/to/preinstalled/browser"\n'
    printf '%s\n' "$MANAGED_END"
  } >> "$tmp"

  mv "$tmp" "$target"
}

write_activate_script() {
  local target="$BUNDLE_ROOT/activate-offline.sh"
  local offline_env_literal

  if [ "$REUSE_ENV_FILE" = "1" ]; then
    offline_env_literal="$(quote_env_value "$OFFLINE_ENV_FILE")"
    cat > "$target" <<EOF
#!/usr/bin/env bash

INSTALL_ROOT="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
export PAPER_FETCH_ENV_FILE=$offline_env_literal

if [ -f "\$PAPER_FETCH_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "\$PAPER_FETCH_ENV_FILE"
  set +a
fi

export PATH="\$INSTALL_ROOT/.venv/bin:\$INSTALL_ROOT/formula-tools/bin:\$PATH"
export PAPER_FETCH_ENV_FILE=$offline_env_literal
export PAPER_FETCH_DOWNLOAD_DIR="\$INSTALL_ROOT/downloads"
export PAPER_FETCH_FORMULA_TOOLS_DIR="\$INSTALL_ROOT/formula-tools"
export CLOAKBROWSER_HEADLESS="\${CLOAKBROWSER_HEADLESS:-$(cloakbrowser_headless_value)}"
export PYTHONUTF8="\${PYTHONUTF8:-1}"
export PYTHONIOENCODING="\${PYTHONIOENCODING:-utf-8}"
EOF
  else
    cat > "$target" <<'EOF'
#!/usr/bin/env bash

INSTALL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PAPER_FETCH_ENV_FILE="${PAPER_FETCH_ENV_FILE:-$INSTALL_ROOT/offline.env}"

if [ -f "$PAPER_FETCH_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$PAPER_FETCH_ENV_FILE"
  set +a
fi

export PATH="$INSTALL_ROOT/.venv/bin:$INSTALL_ROOT/formula-tools/bin:$PATH"
export PAPER_FETCH_DOWNLOAD_DIR="${PAPER_FETCH_DOWNLOAD_DIR:-$INSTALL_ROOT/downloads}"
export PAPER_FETCH_FORMULA_TOOLS_DIR="${PAPER_FETCH_FORMULA_TOOLS_DIR:-$INSTALL_ROOT/formula-tools}"
export CLOAKBROWSER_HEADLESS="${CLOAKBROWSER_HEADLESS:-__CLOAKBROWSER_HEADLESS__}"
export PYTHONUTF8="${PYTHONUTF8:-1}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
EOF
    sed -i "s|__CLOAKBROWSER_HEADLESS__|$(cloakbrowser_headless_value)|g" "$target"
  fi
  chmod +x "$target"
}

check_cloakbrowser_package() {
  local venv_python="$BUNDLE_ROOT/.venv/bin/python"
  "$venv_python" -c 'import cloakbrowser; assert hasattr(cloakbrowser, "launch")'
  if [ -n "${CLOAKBROWSER_BINARY_PATH:-}" ] && [ ! -x "$CLOAKBROWSER_BINARY_PATH" ]; then
    die "CLOAKBROWSER_BINARY_PATH is set but is not executable: $CLOAKBROWSER_BINARY_PATH"
  fi
}

warm_cloakbrowser_runtime() {
  local venv_python="$BUNDLE_ROOT/.venv/bin/python"
  if [ -n "${CLOAKBROWSER_BINARY_PATH:-}" ]; then
    log "Using preconfigured CLOAKBROWSER_BINARY_PATH; skipping CloakBrowser runtime download"
    [ -x "$CLOAKBROWSER_BINARY_PATH" ] || die "CLOAKBROWSER_BINARY_PATH is set but is not executable: $CLOAKBROWSER_BINARY_PATH"
    return 0
  fi
  log "Checking CloakBrowser runtime availability"
  "$venv_python" -c 'import cloakbrowser; cloakbrowser.ensure_runtime()' \
    || warn "CloakBrowser runtime warmup failed; first use may download it, or set CLOAKBROWSER_BINARY_PATH to a preinstalled binary."
}

run_smoke_checks() {
  [ "$RUN_SMOKE" = "1" ] || return 0

  local key env_args=()

  log "Running local smoke checks"
  "$BUNDLE_ROOT/.venv/bin/paper-fetch" --help >/dev/null
  "$BUNDLE_ROOT/formula-tools/bin/texmath" --help >/dev/null
  check_cloakbrowser_package
  for key in "${MCP_ENV_KEYS[@]}"; do
    env_args+=("$key=$(mcp_env_value "$key")")
  done
  env "${env_args[@]}" "$BUNDLE_ROOT/.venv/bin/python" -c 'from paper_fetch.mcp.fetch_tool import provider_status_payload; payload = provider_status_payload(); assert "providers" in payload'
}

main() {
  local project_wheel

  load_installer_manifest

  if [ "$UNINSTALL" = "1" ]; then
    uninstall_user_integrations
    return 0
  fi

  check_platform
  check_python
  verify_checksums
  check_preset_requirements
  check_bundle_assets
  project_wheel="$(find_project_wheel)"

  install_project_venv "$project_wheel"
  warm_cloakbrowser_runtime

  if [ "$REUSE_ENV_FILE" = "1" ]; then
    log "Reusing offline.env without modifying it: $OFFLINE_ENV_FILE"
  else
    log "Writing repo-local offline.env"
    write_managed_env_file "$OFFLINE_ENV_FILE"
  fi
  write_activate_script

  if [ "$MERGE_USER_CONFIG" = "1" ]; then
    [ -n "${HOME:-}" ] || die "HOME is required for --user-config."
    log "Merging offline runtime block into $HOME/.config/paper-fetch/.env"
    write_managed_env_file "$HOME/.config/paper-fetch/.env"
  fi

  install_skills
  write_shell_startup_file
  register_codex_mcp
  register_claude_mcp
  register_gemini_mcp

  run_smoke_checks

  echo
  echo "Offline installation complete."
  echo "Shell startup file updated: $SHELL_STARTUP_TARGET"
  echo "Open a new shell, or activate the current one with: source $BUNDLE_ROOT/activate-offline.sh"
  echo "CloakBrowser headless: $(cloakbrowser_headless_value)"
  echo "Optional runtime override: set CLOAKBROWSER_BINARY_PATH in $OFFLINE_ENV_FILE before first browser fetch."
  echo "Restart Codex, Claude Code, and Gemini CLI so they rescan skills and MCP registration."
  echo "Elsevier setup: request a key at https://dev.elsevier.com/, then add ELSEVIER_API_KEY=\"...\" to $OFFLINE_ENV_FILE before fetching Elsevier papers."
}

main "$@"
