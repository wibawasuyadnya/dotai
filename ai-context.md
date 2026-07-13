# OrkesAI Agent Blueprint

> **Syntax**: `[command / execution] ──> [intent1], [intent2], [intent3]`  
> **Delimiter**: `" ──> "` (Three-dash arrow with a trailing space)

---

### Directional Syntax Guide
1. `~/path`: Indexes workspace and launches a standard AI Workspace.
2. `ai init --<skill>`: Indexes codebase workspace pre-primed with a chosen `--<skill>` (e.g., `--init` or `--coder`).
3. `[TOOL] <command> [--s]`: Runs a background utility to inject dynamic Markdown context (append ` --s` to bypass confirmation).
4. `<command>`: Launches a native terminal alias, interactive TUI, or document viewer (using `mdcat`, `leaf`, or `glow`).

---

## Active Workspace Projects & History Viewer

```properties
# --- Session-Test - This is a Project Workspace (Skill-Primed) --
ai init ~/.config/orkesai/projects/session-test --init ---> session test, projects session, projects
ai init ~/.config/orkesai/projects/session-test-2 --init ---> session test 2, projects session, projects

# --- Dynamic File Reader ---
[TOOL] cat $1 ---> view file, read file, show file, vf
# --- Active Workspace History Viewer ---
[TOOL] mdcat history.md | less -R ---> show history, hist, history
# --- Active Workspace History Searcher ---
[TOOL] read -p "Search Page: " query && mdcat history.md | grep --color=always -A 15 -B 2 -i "$query" ---> search page, hs
```

## 1. Workspace Initializers & Bridges

```properties
# --- OpenCode Direct Terminal Launcher ---
~/.config/orkesai/tools/subsec/opencode-bridge/opencode-bridge ---> opencode bridge, bridge, ocb

# --- Odysseus Direct Terminal Launcher ---
~/.config/orkesai/tools/subsec/odysseus-bridge/odysseus-bridge ---> odysseus bridge, bridge, ody, odb

# --- Hermes Direct Browser Workspace Launcher ---
~/.config/orkesai/tools/subsec/hermes-bridge/hermes-bridge ---> hermes bridge, bridge, hmb, herm
```

## 2. On-Demand System Prompts & Role Injections (Skills)

```properties
# [TOOL] cat ~/.config/orkesai/skills/identity/business/mybiz.md --leaf ---> mybiz, show business profile, view mybiz
# [TOOL] cat ~/.config/orkesai/skills/identity/marketing/strategy.md --leaf ---> marketing strategy, growth strategy, view marketing
# [TOOL] cat ~/.config/orkesai/skills/identity/workout/routine.md --leaf ---> routine, fitness profile, workout routine
```

## 3. Dynamic Context-Injected Tools (RAG)

```properties
# --- Dynamic Host Profiler & System Analytics ---
[TOOL] cat ~/.config/orkesai/skills/system/mysys.md --leaf ---> mysys, show mysys, view sys, mysys doc
[TOOL] ~/.config/orkesai/tools/generate-profile ---> generate profile, update sys profile, sync mysys

# --- Pre-Install Zero-Trust AUR Package & PKGBUILD Auditor ---
[TOOL] ~/.config/orkesai/tools/agentic/system/aur-audit ---> aur audit, audit package

# --- Host Security Surface & Vulnerability Intelligence (SECAUD) ---
[TOOL] ~/.config/orkesai/tools/agentic/system/security-audit --leaf ---> security audit, secaud, system audit

# --- System Optimization (Improve System Performance) ---
[TOOL] ~/.config/orkesai/tools/agentic/system/system-optimizer --leaf ---> system optimizer, sysop, optimize

# --- System Logs & Diagnostics (Compressed Stream Triage) ---
[TOOL] ~/.config/orkesai/tools/agentic/system/log-checker ---> log checker, ailog, log check, check errors, system crashed, events

# --- System Resources & Diagnosis (System Health) ---
[TOOL] ~/.config/orkesai/tools/agentic/system/system-health ---> system health, sysh, health, system diagnosis, why is my system slow

# --- Pending Updates ---
[TOOL] ~/.config/orkesai/tools/agentic/system/update-inspector --leaf ---> update inspector, inspector, ui

# --- Disk Usage ---
[TOOL] df -h / ---> disk usage, drive usage

# --- AI Status & Provider Diagnostics ---
[TOOL] ~/.config/orkesai/tools/agentic/system/ai-status --s --leaf ---> ai status, aistat, status, aistatus 

# --- Weather & Live Networking ---
[TOOL] curl -s "wttr.in/?format=3" --s --cat ---> weather simple, wttr, weather, rain forecast simple
[TOOL] curl -s wttr.in --s --cat ---> weather full, wttr, weather, rain forecast full


# --- System Time & Date (Real-time Clock Context) ---
[TOOL] date --s ---> time, date, current time, what time is it
```

## 4. Static Aliases & Shell Shortcuts

```properties
# --- Local-Ai Agent Blueprint (CheatSheet) ---
~/.config/orkesai/tools/blueprint --s --leaf ---> cheatsheet, bp, cs, blueprint

# --- AI-Generated Git Commits ---
~/.config/orkesai/tools/agentic/system/ai-commit ---> ai-commit, gc, git commit

# --- Index-Map (Structural Repo Profile Compiler) ---
[TOOL] ~/.config/orkesai/tools/map/index-map --cat ---> index map, im

# --- Server Lifecycle Management ---
~/.config/orkesai/tools/tools/subsec/server/kill-ai-servers ---> killserver, ks
```

## 5. TUI (Terminal User Interface) Programs

```properties
# --- Ai-Prompt-Writer-Imaage - Interactive TUI Console ---
[TOOL] ~/.config/orkesai/tools/subsec/prompt/ai-prompt-writer-image --cat ---> prompt writer image, image prompt, ip
# --- Ai-Prompt-Writer - Interactive TUI Console ---
[TOOL] ~/.config/orkesai/tools/subsec/prompt/ai-prompt-writer --cat ---> prompt writer, prompt

# --- Fusion-Research Engine (Compound MoA / Self-Fusion) ---
~/.config/orkesai/tools/agentic/fusion/f_research -r ---> fusion research, fusion, fr, deep research
# --- AI Deep Research TUI ---
~/.config/orkesai/tools/subsec/research-tui/deep-research ---> deep research, research, dr

# --- Custom TUI Applications ---
~/.config/orkesai/tools/subsec/basepage-tui/basepage.py ---> basepage, base, basepage tui, rss
~/.config/orkesai/tools/subsec/basepage-tui/basetracker.py ---> basetracker, base, basetracker tui

# --- Media & Volume Controllers (Pure Reactive) ---
~/.config/orkesai/tools/subsec/media/media.py ---> tuiamp, winamp, media

# --- Article & YouTube Summarizers ---
~/.config/orkesai/tools/subsec/ai-summary/llmsum.py ---> llmsum, ytsum, summary, sum

# --- Local-Ai Tablet Voice Bridge ---
~/.config/orkesai/tools/subsec/voice/voice-query ---> voice, voice query, voice bridge
```

## 6. Graphical Applications & Webapps

```properties
# --- System App Launcher (Ultra-Light Rofi-TUI) ---
~/.config/orkesai/tools/subsec/app-launcher/app-launcher.py ---> app launcher, app

# --- Native Webapp Wrappers & Browsers ---
omarchy-launch-webapp https://music.youtube.com/ ---> youtube music, yt, music, youtube
nohup uwsm app -- brave-origin --user-data-dir="~/.config/BraveSoftware/brave-spotify-bunker" --app=https://open.spotify.com/ >/dev/null 2>&1 & ---> spotify music, spotify, music
```

## 7. Subsection Applications

```properties
# --- Stopwatch ---
~/.config/orkesai/tools/subsec/stopwatch/stopwatch.py ---> stopwatch py, sw, stopwatch
~/.config/orkesai/tools/subsec/stopwatch/stopwatch.sh ---> stopwatch sh, sw, stopwatch

# --- Notes ---
~/.config/orkesai/tools/subsec/notes/notes.sh ---> notes, open notes, add to notes

# --- State & Workflow Management ---
~/.config/orkesai/tools/subsec/hyprstate/work ---> hyprstate work, work, hs, hyprstate
~/.config/orkesai/tools/subsec/hyprstate/clean ---> hyprstate clean, clean, hs, hyprstate
~/.config/orkesai/tools/subsec/hyprstate/gitcom ---> hyprstate gitcom, gitcom, gcom, hs, hyprstate
~/.config/orkesai/tools/subsec/hyprstate/media ---> hyprstate media, media, hs, hyprstate
```

## 8. Testing (Concepts & Prototypes)
```properties
# --- Pixel-Browse - Headless Visual Web Ingestion (((wip))) ---
[TOOL] ~/.config/orkesai/tools/subsec/headless-chromium/pixel-browse --cat ---> pixel browse, headless, chromium, pixel browser

# --- Coding-Triangle-Loop - Interactive TUI Console (((wip))) ---
[TOOL] ~/.config/orkesai/tools/agentic/coding/coding-triangle-loop --cat ---> coding loop, coding, triangle, loop

# --- Learned System Shortcuts (((wip))) ---
# ss -tuln ---> how do i view active network ports, active ports, network ports
# hostnamectl ---> how do i see my system information, system info, hostname
```

