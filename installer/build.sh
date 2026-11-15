#!/bin/sh
# Build dist/setup-hometown.sh: the installer head + the app as a base64 tarball after __PAYLOAD_BELOW__.
# Packs every top-level *.py, routes/, commands/, static/, ops/, requirements-mcp.txt and the two init scripts, so a
# new module or ops job needs no edit here. Works under busybox sh (CI's alpine job runs it).
set -eu
cd "$(dirname "$0")/.."
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
APP="$STAGE/hometown"
mkdir -p dist "$APP/installer"
for f in *.py; do [ -f "$f" ] && cp "$f" "$APP/"; done
cp -R routes commands static ops "$APP/"
cp requirements-mcp.txt "$APP/"
cp installer/init-hometown installer/init-hometown-mcp "$APP/installer/"
find "$APP" \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -exec rm -rf {} \; 2>/dev/null || true

# BusyBox tar has no --owner/--group; GNU tar needs them so files unpack as root.
TAR_EXTRA=""
if tar --owner=0 --group=0 --help >/dev/null 2>&1; then TAR_EXTRA="--owner=0 --group=0"; fi

OUT=dist/setup-hometown.sh
{
  cat installer/setup-head.sh
  echo "__PAYLOAD_BELOW__"
  # shellcheck disable=SC2086
  ( cd "$STAGE" && tar $TAR_EXTRA -czf - hometown ) | base64
} > "$OUT"
chmod +x "$OUT"
sh -n "$OUT"
echo "Built $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
