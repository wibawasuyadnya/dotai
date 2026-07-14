#!/usr/bin/env bash
# OrkesAI release — the whole flow in one command:
#
#   bash deploy/release.sh v0.9.2
#
# 1. tags + pushes the version
# 2. updates the Homebrew formula (url + sha256) here and in the tap repo
# 3. builds the desktop apps (macOS .dmg arm64/x64, Windows .exe)
# 4. creates the GitHub release and uploads the three installers
#
# Needs: clean git tree on main, gui/ present locally, git credentials in the
# keychain with repo scope (the same ones `git push` uses).
set -euo pipefail

VER="${1:?usage: deploy/release.sh vX.Y.Z}"
REPO="wibawasuyadnya/orkesai"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAP_DIR="$(mktemp -d)/homebrew-orkesai"
say() { printf '\033[1;36m[release]\033[0m %s\n' "$*"; }

TOKEN=$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill | grep '^password=' | cut -d= -f2-)
[ -n "$TOKEN" ] || { echo "no github credentials found"; exit 1; }

cd "$ROOT"
[ -z "$(git status --porcelain)" ] || { echo "working tree not clean — commit first"; exit 1; }

# ── 1. tag ────────────────────────────────────────────────────────────────────
say "tagging $VER"
git tag "$VER" && git push origin "$VER"

# ── 2. formula: url + sha256, both repos ─────────────────────────────────────
say "computing tarball sha256"
TARBALL="$(mktemp)"
curl -fsSL "https://github.com/$REPO/archive/refs/tags/$VER.tar.gz" -o "$TARBALL"
SHA=$(shasum -a 256 "$TARBALL" | cut -d' ' -f1)
say "sha256: $SHA"

python3 - "$VER" "$SHA" <<'EOF'
import re, sys
ver, sha = sys.argv[1], sys.argv[2]
fp = "deploy/homebrew/orkesai.rb"
s = open(fp).read()
s = re.sub(r"refs/tags/v[\d.]+\.tar\.gz", f"refs/tags/{ver}.tar.gz", s)
s = re.sub(r'sha256 "[0-9a-f]{64}"', f'sha256 "{sha}"', s)
open(fp, "w").write(s)
EOF
git add deploy/homebrew/orkesai.rb
git commit -m "formula: bump to $VER"
git push origin main

say "updating tap repo"
git clone -q "https://github.com/wibawasuyadnya/homebrew-orkesai.git" "$TAP_DIR"
cp deploy/homebrew/orkesai.rb "$TAP_DIR/Formula/orkesai.rb"
git -C "$TAP_DIR" add -A
git -C "$TAP_DIR" commit -qm "orkesai ${VER#v}"
git -C "$TAP_DIR" push -q origin main

# ── 3. desktop apps ──────────────────────────────────────────────────────────
# Each installer embeds a standalone CPython (astral-sh/python-build-standalone)
# plus the server payload, so users need NOTHING preinstalled — drag, drop, run.
fetch_python() { # $1 = target triple, e.g. aarch64-apple-darwin
    say "fetching python runtime ($1)"
    rm -rf "$ROOT/gui/python-runtime" && mkdir -p "$ROOT/gui/python-runtime"
    local url
    url=$(curl -fsSL https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest \
        | python3 -c "import sys,json;print(next(a['browser_download_url'] for a in json.load(sys.stdin)['assets'] if 'cpython-3.12.' in a['name'] and '$1' in a['name'] and a['name'].endswith('install_only.tar.gz')))")
    curl -fsSL "$url" | tar -xz -C "$ROOT/gui/python-runtime" --strip-components=1
}

say "building desktop apps (dmg arm64 + x64, exe x64) with embedded python"
cd gui
BUILD_TARGET=electron npx next build
fetch_python aarch64-apple-darwin
npx electron-builder --mac dmg --arm64
fetch_python x86_64-apple-darwin
npx electron-builder --mac dmg --x64
fetch_python x86_64-pc-windows-msvc
npx electron-builder --win nsis --x64
cd "$ROOT"

# ── 4. github release + assets ───────────────────────────────────────────────
say "creating GitHub release $VER"
RID=$(python3 - "$TOKEN" "$VER" <<'EOF'
import json, re, sys, urllib.request
token, ver = sys.argv[1], sys.argv[2]
body = (f"Install: `curl -fsSL https://raw.githubusercontent.com/wibawasuyadnya/orkesai/main/install.sh | bash`"
        f" · `npm install -g wibawasuyadnya/orkesai` · `brew install wibawasuyadnya/orkesai/orkesai`\n\n"
        f"Desktop (nothing to preinstall — python + server are inside the app): "
        f"OrkesAI-arm64.dmg (Apple Silicon) · OrkesAI-x64.dmg (Intel Mac) · "
        f"OrkesAI-x64.exe (Windows, experimental)")
# What's new comes straight from this version's CHANGELOG.md section, so every
# release page documents its own features
try:
    cl = open("CHANGELOG.md", encoding="utf-8").read()
    m = re.search(rf"^## \[{re.escape(ver)}\].*?$(.*?)(?=^## \[|\Z)", cl, re.S | re.M)
    if m and m.group(1).strip():
        body = m.group(1).strip() + "\n\n---\n\n" + body
except Exception:
    pass
req = urllib.request.Request(
    "https://api.github.com/repos/wibawasuyadnya/orkesai/releases",
    data=json.dumps({"tag_name": ver, "name": f"OrkesAI {ver}", "body": body,
                     "make_latest": "true"}).encode(),
    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    method="POST")
print(json.load(urllib.request.urlopen(req))["id"])
EOF
)
for f in OrkesAI-arm64.dmg OrkesAI-x64.dmg OrkesAI-x64.exe; do
    say "uploading $f"
    curl -fsS -o /dev/null \
        -X POST "https://uploads.github.com/repos/$REPO/releases/$RID/assets?name=$f" \
        -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/octet-stream" \
        --data-binary @"gui/dist/$f"
done

say "done — https://github.com/$REPO/releases/tag/$VER"
