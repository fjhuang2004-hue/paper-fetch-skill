#!/usr/bin/env bash
# Build the Linux x86_64 CPython 3.11-3.14 offline tarball.

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
  - source snapshot
  - project wheel and Python dependency wheelhouse
  - texmath under formula-tools/
  - cloakbrowser Python wheel; the CloakBrowser browser binary is not bundled
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

copy_source_snapshot() {
  local staging="$1"
  log "Copying source snapshot"
  mkdir -p "$staging"
  tar \
    --exclude='./.git' \
    --exclude='./.venv' \
    --exclude='./.offline-build' \
    --exclude='./.formula-tools' \
    --exclude='./.pytest_cache' \
    --exclude='./.ruff_cache' \
    --exclude='./build' \
    --exclude='./dist' \
    --exclude='./tests' \
    --exclude='./live-downloads' \
    --exclude='./**/__pycache__' \
    --exclude='./*.egg-info' \
    --exclude='./vendor/flaresolverr' \
    -C "$REPO_DIR" -cf - . | tar -C "$staging" -xf -
}

build_project_wheelhouse() {
  local staging="$1"
  local project_dist="$BUILD_DIR/project-dist"
  local wheelhouse="$staging/wheelhouse"
  rm -rf "$project_dist"
  mkdir -p "$project_dist" "$wheelhouse" "$staging/dist"

  log "Building project wheel"
  "$PYTHON_BIN" -m pip wheel --no-deps --wheel-dir "$project_dist" "$REPO_DIR"

  shopt -s nullglob
  local wheels=("$project_dist"/paper_fetch_skill-*.whl)
  shopt -u nullglob
  [ "${#wheels[@]}" -eq 1 ] || die "Expected one built project wheel, found ${#wheels[@]}."
  cp "${wheels[0]}" "$staging/dist/"

  log "Downloading project dependency wheelhouse"
  "$PYTHON_BIN" -m pip download \
    --dest "$wheelhouse" \
    --only-binary=:all: \
    "${wheels[0]}"

  shopt -s nullglob
  local cloakbrowser_wheels=("$wheelhouse"/cloakbrowser-*.whl)
  shopt -u nullglob
  [ "${#cloakbrowser_wheels[@]}" -gt 0 ] || die "Dependency wheelhouse is missing cloakbrowser-*.whl."
}

create_build_venv() {
  local staging="$1"
  local build_venv="$BUILD_DIR/build-venv"
  rm -rf "$build_venv"
  "$PYTHON_BIN" -m venv "$build_venv"
  "$build_venv/bin/python" -m pip install --quiet --upgrade pip >&2
  "$build_venv/bin/python" -m pip install \
    --no-index \
    --find-links "$staging/wheelhouse" \
    "$staging"/dist/paper_fetch_skill-*.whl >&2
  printf '%s\n' "$build_venv/bin/python"
}

bundle_formula_tools() {
  local staging="$1"
  local build_python="$2"
  log "Bundling formula tools"
  "$build_python" -m paper_fetch.formula.install --target-dir "$staging/formula-tools" --no-node
  "$staging/formula-tools/bin/texmath" --help >/dev/null
  "$build_python" - "$staging/formula-tools" <<'PY'
from pathlib import Path
import sys

from paper_fetch.formula.install import stage_bundled_node_workspace

stage_bundled_node_workspace(Path(sys.argv[1]))
PY
}

write_offline_readme() {
  local staging="$1"
  cat > "$staging/README.offline.md" <<'EOF'
# Paper Fetch Offline Package

This package includes Python wheels, including `cloakbrowser`, and formula tools.
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

project_wheels = sorted(path.name for path in (staging / "dist").glob("paper_fetch_skill-*.whl"))
wheelhouse = sorted(path.name for path in (staging / "wheelhouse").glob("*.whl"))
cloakbrowser_wheels = sorted(path.name for path in (staging / "wheelhouse").glob("cloakbrowser-*.whl"))

payload = {
    "schema_version": 1,
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
        "source_snapshot": ".",
        "installer_manifest": "installer/manifest.json",
        "project_wheels": [f"dist/{name}" for name in project_wheels],
        "wheelhouse_count": len(wheelhouse),
        "formula_tools": "formula-tools",
        "cloakbrowser": {
            "wheels": [f"wheelhouse/{name}" for name in cloakbrowser_wheels],
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
  local package_name package_prefix python_tag staging version build_python

  [ -f "$INSTALLER_MANIFEST_FILE" ] || die "Missing installer manifest: $INSTALLER_MANIFEST_FILE"
  python_tag="$(check_target)"
  package_prefix="$(installer_manifest_value packages.linux_offline_name_prefix)"
  package_name="${PACKAGE_NAME:-$package_prefix-$python_tag}"
  staging="$BUILD_DIR/$package_name"
  version="$(project_version)"
  rm -rf "$staging"
  mkdir -p "$BUILD_DIR"

  copy_source_snapshot "$staging"
  build_project_wheelhouse "$staging"
  build_python="$(create_build_venv "$staging")"
  bundle_formula_tools "$staging" "$build_python"
  write_offline_readme "$staging"
  write_manifest_and_checksums "$staging" "$version" "$python_tag"
  create_archive "$BUILD_DIR" "$package_name" "$OUTPUT_DIR"
}

main "$@"
