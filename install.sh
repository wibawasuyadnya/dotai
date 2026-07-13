#!/usr/bin/env bash
# OrkesAI installer — terminal multi-agent AI
#
#   curl -fsSL https://raw.githubusercontent.com/wibawasuyadnya/orkesai/main/install.sh | bash
#
# What it does (idempotent — safe to re-run to update):
#   1. Puts the app in ~/.config/orkesai (git clone, or copies from a local
#      source when invoked by the npm/brew wrappers with --local <dir>)
#   2. Creates .env from .env.example if missing — YOUR keys, YOUR settings;
#      never overwrites .env, settings.json, agents.json, projects/, sessions
#   3. Ensures python3 + the one pip dependency (requests)
#   4. Adds `source ~/.config/orkesai/ai-hook.sh` to your shell rc,
#      which provides the `ai` command (and aic/aix/aio/ail/ais shortcuts)
set -euo pipefail

REPO="wibawasuyadnya/orkesai"
DIR="$HOME/.config/orkesai"
SRC=""
[ "${1:-}" = "--local" ] && SRC="${2:?usage: install.sh --local <srcdir>}"

say()  { printf '\033[1;36m[orkesai]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[orkesai]\033[0m %s\n' "$*"; }

# ── 1. code → ~/.config/orkesai ────────────────────────────────────────────
if [ -L "$DIR" ]; then
    say "dev install detected ($DIR is a symlink) — leaving code as-is"
elif [ -d "$DIR/.git" ]; then
    say "updating existing install (git pull)"
    git -C "$DIR" pull --ff-only || warn "pull failed — local changes? update skipped"
elif [ -n "$SRC" ]; then
    say "copying app from $SRC"
    mkdir -p "$DIR"
    # rsync-less copy that never touches user data
    (cd "$SRC" && tar cf - --exclude .git --exclude node_modules --exclude gui \
        --exclude projects --exclude .sessions --exclude .env --exclude settings.json .) \
        | (cd "$DIR" && tar xf -)
elif [ -f "$DIR/ai-agent.py" ]; then
    say "existing install found at $DIR (not a git checkout) — leaving code as-is"
else
    if command -v git >/dev/null 2>&1; then
        say "cloning $REPO → $DIR"
        git clone --depth 1 "https://github.com/$REPO.git" "$DIR"
    else
        say "git not found — downloading tarball"
        mkdir -p "$DIR"
        curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" \
            | tar xz -C "$DIR" --strip-components 1
    fi
fi

# ── 2. per-user config (never overwritten on update) ────────────────────────
if [ ! -f "$DIR/.env" ] && [ -f "$DIR/.env.example" ]; then
    cp "$DIR/.env.example" "$DIR/.env"
    say "created $DIR/.env from the template — add your own keys there"
fi

# ── 3. python + deps ─────────────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 is required — install it (macOS: xcode-select --install) and re-run"
    exit 1
fi
if ! python3 -c "import requests" >/dev/null 2>&1; then
    say "installing python dependency: requests"
    pip3 install --quiet --user requests 2>/dev/null \
        || pip3 install --quiet --user --break-system-packages requests 2>/dev/null \
        || warn "could not pip install requests — run: pip3 install requests"
fi

# ── 4. shell hook → the `ai` command ─────────────────────────────────────────
HOOK_LINE="source \"\$HOME/.config/orkesai/ai-hook.sh\""
added=""
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    [ -f "$rc" ] || continue
    if ! grep -Fq "orkesai/ai-hook.sh" "$rc"; then
        printf '\n# OrkesAI terminal agent\n%s\n' "$HOOK_LINE" >> "$rc"
        added="$added $(basename "$rc")"
    fi
done
[ -n "$added" ] && say "hooked into:$added" || say "shell hook already present"

say "done — open a NEW terminal (or: source ~/.zshrc) and type: ai"
say "backends: edit $DIR/.env — OpenRouter key, or install the claude/codex CLIs,"
say "or none at all: the local Qwen3-4B model auto-downloads on first use (~2.5 GB)."
