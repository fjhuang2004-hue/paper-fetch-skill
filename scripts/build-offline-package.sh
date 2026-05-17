#!/usr/bin/env bash
# Build the Linux x86_64 CPython 3.11-3.14 offline runtime tarball.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${PAPER_FETCH_OFFLINE_BUILD_DIR:-$REPO_DIR/.offline-build}"
OUTPUT_DIR="$REPO_DIR/dist"
PACKAGE_NAME=""
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALLER_MANIFEST_FILE="$REPO_DIR/installer/manifest.json"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  scripts/build-offline-package.sh [--output-dir <path>] [--package-name <name>]

Builds a Linux x86_64 CPython 3.11-3.14 tar.gz bundle containing:
  - preinstalled Python runtime under runtime/site-packages
  - command wrappers under bin/
  - texmath under formula-tools/
  - cloakbrowser Python package; the CloakBrowser browser binary is not bundled
EOF
}

while (($#)); do
  case "$1" in
    --output-dir)
      shift
      [ "$#" -gt 0 ] || die "--output-dir requires a path"
      OUTPUT_DIR="$1"
      ;;
    --package-name)
      shift
      [ "$#" -gt 0 ] || die "--package-name requires a value"
      PACKAGE_NAME="$1"
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

detect_python_tag() {
  "$PYTHON_BIN" - <<'PY'
import sys

if sys.implementation.name != "cpython":
    raise SystemExit(1)

print(f"cp{sys.version_info.major}{sys.version_info.minor}")
PY
}

is_supported_python_tag() {
  case "$1" in
    cp311|cp312|cp313|cp314) return 0 ;;
    *) return 1 ;;
  esac
}

check_target() {
  [ "$(uname -s)" = "Linux" ] || die "Offline package build currently targets Linux only."
  case "$(uname -m)" in
    x86_64|amd64) ;;
    *) die "Offline package build currently targets x86_64 only." ;;
  esac
  local python_tag
  python_tag="$(detect_python_tag)" \
    || die "Offline package build requires CPython 3.11, 3.12, 3.13, or 3.14."
  is_supported_python_tag "$python_tag" \
    || die "Offline package build requires CPython 3.11, 3.12, 3.13, or 3.14; detected $python_tag."
  printf '%s\n' "$python_tag"
}

project_version() {
  "$PYTHON_BIN" -c 'import pathlib, sys, tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["project"]["version"])' "$REPO_DIR/pyproject.toml"
}

installer_manifest_value() {
  "$PYTHON_BIN" -c '
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
value = data
for part in sys.argv[2].split("."):
    value = value[part]
print(value)
' "$INSTALLER_MANIFEST_FILE" "$1"
}

copy_runtime_assets() {
  local staging="$1"
  log "Copying runtime installer assets"
  mkdir -p "$staging/installer" "$staging/skills"
  cp "$REPO_DIR/install-offline.sh" "$staging/install-offline.sh"
  chmod +x "$staging/install-offline.sh"
  cp "$REPO_DIR/.env.example" "$staging/.env.example"
  cp "$REPO_DIR/LICENSE" "$staging/LICENSE"
  cp "$INSTALLER_MANIFEST_FILE" "$staging/installer/manifest.json"
  cp -a "$REPO_DIR/skills/paper-fetch-skill" "$staging/skills/"
}

build_project_runtime() {
  local staging="$1"
  local project_dist="$BUILD_DIR/project-dist"
  local wheelhouse="$BUILD_DIR/linux-runtime-wheelhouse"
  local site_packages="$staging/runtime/site-packages"
  rm -rf "$project_dist" "$wheelhouse" "$site_packages"
  mkdir -p "$project_dist" "$wheelhouse" "$site_packages"

  log "Building project wheel"
  "$PYTHON_BIN" -m pip wheel --no-deps --wheel-dir "$project_dist" "$REPO_DIR"

  shopt -s nullglob
  local wheels=("$project_dist"/paper_fetch_skill-*.whl)
  shopt -u nullglob
  [ "${#wheels[@]}" -eq 1 ] || die "Expected one built project wheel, found ${#wheels[@]}."

  log "Downloading binary dependency wheelhouse"
  "$PYTHON_BIN" -m pip download \
    --dest "$wheelhouse" \
    --only-binary=:all: \
    "${wheels[0]}"

  shopt -s nullglob
  local cloakbrowser_wheels=("$wheelhouse"/cloakbrowser-*.whl)
  shopt -u nullglob
  [ "${#cloakbrowser_wheels[@]}" -gt 0 ] || die "Dependency wheelhouse is missing cloakbrowser-*.whl."

  log "Installing project runtime into package"
  PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
  "$PYTHON_BIN" -m pip install \
    --target "$site_packages" \
    --no-index \
    --find-links "$wheelhouse" \
    --only-binary=:all: \
    "${wheels[0]}"

  log "Precompiling Python runtime bytecode"
  "$PYTHON_BIN" -m compileall -q "$site_packages"

  PYTHONPATH="$site_packages${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -X utf8 -c 'import cloakbrowser; import paper_fetch; import paper_fetch.mcp.server; assert hasattr(cloakbrowser, "launch")'
}

bundle_formula_tools() {
  local staging="$1"
  log "Bundling formula tools"
  PYTHONPATH="$staging/runtime/site-packages${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m paper_fetch.formula.install --target-dir "$staging/formula-tools" --no-node
  "$staging/formula-tools/bin/texmath" --help >/dev/null
  PYTHONPATH="$staging/runtime/site-packages${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" - "$staging/formula-tools" <<'PY'
from pathlib import Path
import sys

from paper_fetch.formula.install import stage_bundled_node_workspace

stage_bundled_node_workspace(Path(sys.argv[1]))
PY
}

write_cmd_wrappers() {
  local staging="$1"
  local bin="$staging/bin"
  log "Writing command wrappers"
  mkdir -p "$bin"

  cat > "$bin/python" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -n "${PAPER_FETCH_OFFLINE_PYTHON_BIN:-}" ]; then
  PYTHON_BIN="$PAPER_FETCH_OFFLINE_PYTHON_BIN"
elif [ -f "$INSTALL_ROOT/runtime/python-bin" ]; then
  IFS= read -r PYTHON_BIN < "$INSTALL_ROOT/runtime/python-bin"
else
  PYTHON_BIN="python3"
fi
export PYTHONPATH="$INSTALL_ROOT/runtime/site-packages${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUTF8="${PYTHONUTF8:-1}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
exec "$PYTHON_BIN" "$@"
EOF

  cat > "$bin/paper-fetch" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -z "${PAPER_FETCH_ENV_FILE:-}" ]; then
  export PAPER_FETCH_ENV_FILE="$INSTALL_ROOT/offline.env"
fi
exec "$INSTALL_ROOT/bin/python" -X utf8 -m paper_fetch.cli "$@"
EOF

  cat > "$bin/paper-fetch-mcp" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -z "${PAPER_FETCH_ENV_FILE:-}" ]; then
  export PAPER_FETCH_ENV_FILE="$INSTALL_ROOT/offline.env"
fi
exec "$INSTALL_ROOT/bin/python" -X utf8 -m paper_fetch.mcp.server "$@"
EOF

  cat > "$bin/paper-fetch-install-formula-tools" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$INSTALL_ROOT/bin/python" -X utf8 -m paper_fetch.formula.install "$@"
EOF

  chmod +x "$bin/python" "$bin/paper-fetch" "$bin/paper-fetch-mcp" "$bin/paper-fetch-install-formula-tools"
}

write_offline_readme() {
  local staging="$1"
  cat > "$staging/README.offline.md" <<'EOF'
# Paper Fetch Offline Package

This package includes an installed Python runtime under `runtime/site-packages`, command wrappers under `bin/`, and formula tools.
It does not redistribute the CloakBrowser browser binary.

The first browser-backed fetch may need network access so CloakBrowser can download its runtime. In restricted environments, preinstall a compatible browser runtime and set `CLOAKBROWSER_BINARY_PATH` before using browser-backed providers.

Set `CLOAKBROWSER_HEADLESS=false` only when running with a display-capable session.
EOF
}

write_manifest_and_checksums() {
  local staging="$1"
  local version="$2"
  local python_tag="$3"
  local git_revision
  git_revision="$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || true)"

  log "Writing manifest and checksums"
  "$PYTHON_BIN" - "$staging" "$version" "$git_revision" "$python_tag" "$INSTALLER_MANIFEST_FILE" <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from datetime import UTC, datetime

staging = Path(sys.argv[1])
version = sys.argv[2]
git_revision = sys.argv[3] or None
python_tag = sys.argv[4]
installer_manifest = json.loads(Path(sys.argv[5]).read_text(encoding="utf-8"))
site_packages = staging / "runtime" / "site-packages"
installed_packages = sorted(path.name for path in site_packages.glob("*.dist-info"))

payload = {
    "schema_version": 2,
    "name": installer_manifest["packages"]["linux_manifest_name"],
    "project": installer_manifest["project"],
    "version": version,
    "built_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "git_revision": git_revision,
    "target": {
        "platform": "linux",
        "arch": "x86_64",
        "python_tag": python_tag,
    },
    "entrypoint": "install-offline.sh",
    "components": {
        "python_runtime": "runtime/site-packages",
        "command_wrappers": "bin",
        "installed_package_count": len(installed_packages),
        "installer_manifest": "installer/manifest.json",
        "formula_tools": "formula-tools",
        "cloakbrowser": {
            "python_package": "runtime/site-packages",
            "browser_binary": "not_bundled",
        },
    },
}

(staging / "offline-manifest.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + os.linesep,
    encoding="utf-8",
)
PY

  (
    cd "$staging"
    find . -type f ! -name sha256sums.txt -print0 \
      | sort -z \
      | xargs -0 sha256sum > sha256sums.txt
  )
}

create_archive() {
  local staging_parent="$1"
  local package_name="$2"
  local output_dir="$3"
  mkdir -p "$output_dir"
  log "Creating tar.gz archive"
  tar -C "$staging_parent" -czf "$output_dir/$package_name.tar.gz" "$package_name"
  printf '%s\n' "$output_dir/$package_name.tar.gz"
}

main() {
  local package_name package_prefix python_tag staging version

  [ -f "$INSTALLER_MANIFEST_FILE" ] || die "Missing installer manifest: $INSTALLER_MANIFEST_FILE"
  python_tag="$(check_target)"
  package_prefix="$(installer_manifest_value packages.linux_offline_name_prefix)"
  package_name="${PACKAGE_NAME:-$package_prefix-$python_tag}"
  staging="$BUILD_DIR/$package_name"
  version="$(project_version)"
  rm -rf "$staging"
  mkdir -p "$BUILD_DIR"

  mkdir -p "$staging"
  copy_runtime_assets "$staging"
  build_project_runtime "$staging"
  bundle_formula_tools "$staging"
  write_cmd_wrappers "$staging"
  write_offline_readme "$staging"
  write_manifest_and_checksums "$staging" "$version" "$python_tag"
  create_archive "$BUILD_DIR" "$package_name" "$OUTPUT_DIR"
}

main "$@"
