# Changelog

Every release and what it brought. Versions link to the GitHub release with
the downloadable installers.

## Unreleased

- Legacy terminal TPM facts migrate into the shared brain automatically
  (once, on first open — 'key: value' facts, scoped per old workspace);
  the Databases pane now shows `.memory.db` with per-scope counts, and
  deleting it wipes the brain's contents without breaking the store.

- **One shared memory — one brain for GUI and terminal** (`.memory.db`,
  SQLite + FTS5, stdlib only). What one frontend learns, the other knows.
  Every memory is scoped (everywhere / per-@role / per-project), typed
  (fact / preference / learning) and tiered: **pinned** memories always ride
  along with every message, normal ones are recalled by relevance, ephemerals
  age out. Fully user-managed: Settings → Memory pane (add / search / edit /
  pin / forget), terminal `/mem`, and `/api/memories` — the AI never edits
  your memories, and with learning ON it proposes new ones from your chats.

- **Automations get a working folder** ("Works in folder"): commands run
  there and the agent may create/update files INSIDE it without asking —
  writes outside it and destructive shell stay denied. Enables recipes like
  "GitLab push webhook → task-titled document in my Obsidian vault".

## [v0.13.0](https://github.com/wibawasuyadnya/orkesai/releases/tag/v0.13.0) — 2026-07-14

**OrkesAI learns (opt-in) + drop-in skills.**

- **Learning loop** behind Settings → "Let OrkesAI learn from you" (off by
  default). No model weights ever change — it's a write-back loop of plain,
  user-visible files:
  - `PROFILE.md` — user modeling: style, preferences and goals distilled after
    each turn and injected into every prompt (GUI and terminal)
  - `skills/custom/auto-*.md` — completed tool-heavy work is distilled into a
    reusable skill document and attached to the @role that did it
    (**per-@role learning**: your debugger gets better at debugging, your
    copywriter learns your voice)
  - `.learnings/learnings.md` — tool denials, tool errors and failed
    automation runs are recorded and injected as "don't repeat these"
  - New **Self-review (heartbeat)** automation template reviews the learnings
    on a schedule
- **Drop-in Claude Code / Codex skills** — copy a Claude Code skill folder
  (`<name>/SKILL.md`) or a plain Codex prompt file into `skills/custom/` and
  any agent can use it. OrkesAI never reads `~/.claude` or `~/.codex` itself.
- Learned files can never enter git (`.gitignore`) and never ship in installers.
- Fix: a dev checkout no longer imports the installed engine's modules.

## [v0.12.0](https://github.com/wibawasuyadnya/orkesai/releases/tag/v0.12.0) — 2026-07-14

**First-run setup wizard — no default team.**

- Fresh installs ship with **zero @roles**; a splash wizard runs on first
  launch: pick who you are (business owner / marketer / finance / data /
  engineer / digital artist) → get a matching 3-role team template, or start
  custom with an empty team → optionally auto-install the Claude Code / Codex
  CLIs (`npm install -g`, live progress) or choose advanced manual install.
- Fresh empty session lands with the **message box centered** on the welcome
  screen; existing installs (a team already exists) skip the wizard entirely.
- Models are now told their served identity — GLM no longer introduces itself
  as Claude; every @role answers truthfully which model and backend serves it.

## [v0.11.1](https://github.com/wibawasuyadnya/orkesai/releases/tag/v0.11.1) — 2026-07-13

**Desktop app can now see your Claude/Codex CLIs.**

- Finder-launched apps only get the bare system PATH, so the bundled server
  couldn't find `claude`/`codex` and the picker showed "setup" forever. The
  app now resolves your login shell's PATH and passes it to the server, with
  a server-side fallback covering Homebrew, npm, nvm and `~/.local/bin`.

## [v0.11.0](https://github.com/wibawasuyadnya/orkesai/releases/tag/v0.11.0) — 2026-07-13

**Fully self-contained desktop app — drag, drop, run.**

- The `.dmg`/`.exe` embed a standalone Python runtime plus the whole engine;
  nothing to preinstall. First launch seeds `~/.config/orkesai` and starts the
  local server automatically. App updates refresh engine code only — user data
  (.env, agents, sessions, notes, automations, groups) is never touched.
- The engine is now stdlib-only end to end (dropped the last pip dependency),
  so the terminal CLI install needs zero Python packages too.
- The terminal CLI stays a separate, optional install.

## [v0.10.0](https://github.com/wibawasuyadnya/orkesai/releases/tag/v0.10.0) — 2026-07-13

**Rebrand to OrkesAI + the big feature drop.**

- Renamed from DotAI to **OrkesAI** (repo, config dir `~/.config/orkesai`,
  formula, installers; old URLs redirect).
- **Automations**: trigger (manual / every N minutes / daily / webhook) →
  prompt → actions (forward to webhook, save note). Each automation keeps its
  runs in its own ⚙ chat; external `POST /api/hooks/<id>` fires it; JSON
  template export/import; full-page editor.
- **Group chat**: several @roles in one conversation — everyone answers their
  part, or @mention to address one; each role replies on its **own** engine
  with the model labeled on every reply; @mention autocomplete; stacked-avatar
  sidebar.
- **Custom API integrations**: connect any OpenAI-compatible endpoint (name,
  base URL, auth, key) as its own backend; models auto-discovered.
- **Full OpenRouter catalog** (~345 models) with searchable model pickers.
- **Notes**: hard tool-level guard for "Let the AI keep notes" off; per-note
  export to PDF / DOCX / XLSX / CSV from the note popup.
- Engine-change dividers in every affected chat when an @role's backend/model
  changes; terminal parity commands `/group`, `/auto`, `/models`.

## [v0.9.1](https://github.com/wibawasuyadnya/orkesai/releases/tag/v0.9.1) — 2026-07-07

- One-command release flow (`deploy/release.sh`), experimental Windows NSIS
  build, spawn guard when python3 is missing, landing/README download links,
  `.env` inline-comment parsing fix.

## [v0.9.0](https://github.com/wibawasuyadnya/orkesai/releases/tag/v0.9.0) — 2026-07-07

- First packaged distribution (as DotAI): curl/npm/brew installers into a
  per-user config dir, GitHub Pages landing, Electron `.dmg` packaging,
  Homebrew formula + tap.
