#!/usr/bin/env bash
# OrkesAI Agent Hook v0.9.0

[[ $- != *i* ]] && return

_AI_DIR="$HOME/.config/orkesai"
_AI_SCRIPT_PATH="$_AI_DIR/ai-agent.py"
[[ -f "$_AI_SCRIPT_PATH" ]] || return

command -v python3 >/dev/null 2>&1 && _AI_PYTHON_BIN="python3" || _AI_PYTHON_BIN="python"

# NOTE: .env is loaded by ai-agent.py itself on every run (fresh each time),
# not exported here — so edits to .env apply to the very next `ai` command.

# Quick backend switches
alias aic='AI_BACKEND=claude ai'
alias aix='AI_BACKEND=codex ai'
alias aio='AI_BACKEND=openrouter ai'
alias aig='AI_BACKEND=gemini ai'
alias ail='AI_BACKEND=local ai'

# Multi-agent API server (backend for the gui/ web + Electron app)
alias ais='"$_AI_PYTHON_BIN" "$_AI_DIR/server/server.py"'

_ai_cleanup_stale() {
    [[ -n "$ZSH_VERSION" ]] && setopt local_options null_glob
    local f pid
    for f in "$_AI_DIR"/.active_cd.*; do
        [[ -f "$f" ]] && { pid="${f##*.active_cd.}"; kill -0 "$pid" 2>/dev/null || rm -f "$f"; }
    done
}
_ai_cleanup_stale

_ai_teleport() {
    local rc=$? f="$_AI_DIR/.active_cd.$$"
    [[ -f "$f" ]] && { cd "$(<"$f")" && rm -f "$f"; }
    return $rc
}

if [[ -n "$ZSH_VERSION" ]]; then
    autoload -Uz add-zsh-hook && add-zsh-hook precmd _ai_teleport
else
    [[ "$PROMPT_COMMAND" != *_ai_teleport* ]] && PROMPT_COMMAND="_ai_teleport${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
fi

ai_handle_missing() {
    [[ -n "$ZSH_VERSION" ]] && setopt local_options ksh_arrays
    [[ -z "$*" ]] && return 127
    local cmd=$("$_AI_PYTHON_BIN" "$_AI_SCRIPT_PATH" --interactive "$*")
    [[ -z "$cmd" ]] && return 127
    local exp="${cmd/#\~/$HOME}"
    [[ -d "$exp" ]] && ai init "$exp" || eval "$cmd"
}

command_not_found_handle() { [[ "$1" == --* ]] && return 127; ai_handle_missing "$*"; }
command_not_found_handler() { command_not_found_handle "$@"; }

ai() {
    if [[ "$1" == "init" ]]; then
        local path=$(pwd) skills=() name map
        
        # If the second argument is not empty and does not start with "-", treat it as a path
        if [[ -n "${2:-}" && "${2:-}" != -* ]]; then
            path="$2"
            skills=("${@:3}")
        else
            skills=("${@:2}")
        fi
        
        # If the directory does not exist, automatically create it first
        if [[ ! -d "$path" ]]; then
            mkdir -p "$path" || return 1
        fi
        
        path=$(CDPATH= cd "$path" && pwd) || return 1
        echo "$path" > "$_AI_DIR/.active_cd.$$"
        name=$(basename "$path")
        map="$path/index-map-$name.txt"
        
        # Fast newer-file/directory check
        [[ ! -f "$map" ]] || [[ -n "$(find "$path" ! -path "$path" -not -path '*/.git/*' -not -path '*/.agent/*' -not -name 'history.md' ! -name "$(basename "$map")" -newer "$map" -print -quit 2>/dev/null)" ]] && {
            "$_AI_PYTHON_BIN" "$_AI_DIR/tools/map/index-map" "$path" || return 1
        }
        
        if [[ -f "$map" ]]; then
            AI_ACTIVE_SKILL="${skills[*]}" AI_WORKSPACE_PATH="$path" "$_AI_PYTHON_BIN" "$_AI_SCRIPT_PATH" --talk-chat "$(<"$map")"
        else
            printf "\033[1;31mError: Context file not found at: %s\033[0m\n" "$map" >&2 && return 1
        fi
    else
        "$_AI_PYTHON_BIN" "$_AI_SCRIPT_PATH" --talk "$@"
    fi
}
