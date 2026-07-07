<p align="center">
  <img src="assets/dotai-logo.png" alt="dot.ai" width="420">
</p>

<p align="center">
  A zero-daemon, multi-backend, multi-agent AI for your terminal.<br>
  Chat with <b>Claude</b> (your claude.ai login), <b>Codex</b> (your ChatGPT login),
  <b>OpenRouter</b> (DeepSeek & hundreds more), <b>Gemini</b>, or any <b>local GGUF model</b> (Hermes, Qwen, Llama, …) —
  with automatic fallback between them.
</p>

<p align="center">
  Maintained by <b>suyadnya</b>
</p>

---

## Features

- **5 interchangeable backends** — switch with a single env var, no restart, no config file
- **Automatic fallback cascade** — if your primary backend is down or rate-limited, the next one answers
- **Claude & Codex via your subscriptions** — uses the official `claude` / `codex` CLI logins, so no API keys are needed
- **A team of role agents** — `@debug`, `@review`, … each with its own model, backend, prompt, and persistent sessions; full CRUD from the terminal (`/team add|edit|rm`)
- **MCP tools & skills per agent** — attach MCP servers (`/mcp add`) and prompt skills (like the bundled `caveman` token-saver) to any team agent
- **Opencode-style terminal UI** — composer pinned to the bottom, shaded user-message blocks, per-turn model/token/cost statusline — in your plain terminal, no TUI
- **Unlimited local agents** — every folder can become a persistent, codebase-aware agent with its own memory, history, and personality (skill)
- **Zero idle cost** — no daemon; nothing runs until you type `ai`, and exiting the chat also stops the auto-started local llama-server (set `AI_KEEP_LOCAL=1` to keep it warm across sessions)
- **Persistent memory** — per-workspace SQLite session history + long-term fact memory (TPM)
- **Terminal-native UX** — streaming output, spellcheck, token meters, session snapshots/rollback

---

## Prerequisites

| Requirement | Notes |
| :--- | :--- |
| **Python 3.9+** | Standard library only (plus `requests`) |
| **macOS or Linux** | zsh and bash are both supported |
| **[llama.cpp](https://github.com/ggml-org/llama.cpp)** | *Optional* — only for running local models (`brew install llama.cpp` / your package manager) |
| **[Claude Code CLI](https://claude.com/claude-code)** | *Optional* — only for the Claude backend (`brew install claude` or `npm i -g @anthropic-ai/claude-code`, then `claude login`) |
| **[OpenAI Codex CLI](https://github.com/openai/codex)** | *Optional* — only for the Codex backend (`brew install codex` or `npm i -g @openai/codex`, then `codex login`) |
| **At least one backend** | Any of: Claude login, ChatGPT/Codex login, OpenRouter key, Gemini key, or a local model |

---

## Installation

```bash
# 1. Clone to the path the agent expects
git clone https://github.com/suyadnya/local-ai.git ~/.config/local-ai

#    …or clone anywhere and symlink:
# git clone https://github.com/suyadnya/local-ai.git ~/Documents/local-ai
# ln -s ~/Documents/local-ai ~/.config/local-ai

# 2. Hook it into your shell
#    zsh (macOS default):
echo '[ -f "$HOME/.config/local-ai/ai-hook.sh" ] && source "$HOME/.config/local-ai/ai-hook.sh"' >> ~/.zshrc
source ~/.zshrc

#    bash:
echo '[ -f "$HOME/.config/local-ai/ai-hook.sh" ] && source "$HOME/.config/local-ai/ai-hook.sh"' >> ~/.bashrc
source ~/.bashrc

# 3. Create your config
cp ~/.config/local-ai/.env.example ~/.config/local-ai/.env
#    …then edit .env and fill in the backends you want (see next section)

# 4. Test it
ai hello
```

---

## Configuration — pick your backend(s)

All configuration lives in one file: **`.env`** in the install folder
(`~/.config/local-ai/.env`). No shell knowledge needed — copy the template and
fill in only what you use:

```bash
cp ~/.config/local-ai/.env.example ~/.config/local-ai/.env
nano ~/.config/local-ai/.env      # or open it in any editor
```

```dotenv
# Primary backend: claude | codex | openrouter | gemini | local
AI_BACKEND=claude

# Claude — NO API key needed, uses your claude.ai login via the claude CLI
CLAUDE_MODEL=sonnet                              # sonnet | haiku | opus

# Codex — NO API key needed, uses your ChatGPT login via the codex CLI
# CODEX_MODEL=gpt-5.2-codex                      # omit to use your codex default
# CODEX_EFFORT=medium                            # minimal | low | medium | high

# OpenRouter — one key, hundreds of models (incl. DeepSeek) — openrouter.ai/keys
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENROUTER_MODEL=deepseek/deepseek-v4-flash

# Gemini — optional; leave commented out if you don't use it
# GEMINI_API_KEY=AIza-your-key-here
```

**Every line is optional** — leave anything commented out and the agent skips
that backend in the fallback chain. `.env` is gitignored, so your keys can
never be committed or published. The agent reads `.env` fresh on every run,
so edits apply to your very next `ai` command — no terminal restart needed.
(Power users: plain environment variables also work and take priority over `.env`.)

These aliases are available out of the box for hopping between backends:

| Alias | Backend |
| :--- | :--- |
| `aic` | Claude (claude.ai account login) |
| `aix` | Codex (ChatGPT account login) |
| `aio` | OpenRouter (DeepSeek & hundreds more) |
| `aig` | Gemini |
| `ail` | Local model (llama.cpp) |

### Using your Claude / ChatGPT subscription (no API keys)

The `claude` and `codex` backends don't bill per token — they ride on the
subscriptions you already pay for:

```bash
# Claude (Pro/Max plan)
brew install claude        # or: npm i -g @anthropic-ai/claude-code
claude login               # opens a browser, sign in once
# then in .env:  AI_BACKEND=claude  and pick CLAUDE_MODEL=sonnet|haiku|opus

# Codex (ChatGPT Plus/Pro plan)
brew install codex         # or: npm i -g @openai/codex
codex login                # opens a browser, sign in once
# then in .env:  AI_BACKEND=codex   and optionally CODEX_MODEL / CODEX_EFFORT
```

Both run in chat-only mode inside the agent (file/shell tools disabled), and
if you hit your plan's usage limit, the cascade falls through to your other
configured backends automatically.

### How routing works

`AI_BACKEND` promotes one engine to the front of the line; everything else you
have configured stays behind it as a fallback. So with `AI_BACKEND=claude` and
an OpenRouter key set, a Claude outage (or hitting your subscription's token
limit) automatically falls through to OpenRouter — or force it yourself for one
question:

```bash
aio "explain this error"        # OpenRouter, right now, regardless of default
ail                             # full chat session on your local model
```

---

## Local models (Hermes, or any GGUF)

The agent's `local` backend talks to a llama.cpp server on `http://localhost:8080`.

```bash
# Start Hermes-4-14B (first run downloads ~9 GB, then it's cached)
./start-hermes.sh
```

`start-hermes.sh` is just:

```bash
llama-server -hf bartowski/NousResearch_Hermes-4-14B-GGUF:Q4_K_M --port 8080 -c 8192 --jinja
```

Swap the `-hf` repo for **any GGUF on Hugging Face** to change models
(Qwen, Llama, Mistral, …). Rough sizing guide: a Q4_K_M GGUF needs ~0.6 GB RAM
per billion parameters — a 14B model fits in 16–24 GB machines, a 70B does not.
> **Note:** giant MoE models like DeepSeek V4 (284B) can't run on normal
> hardware — use them through OpenRouter instead.

Keep the server running in a separate terminal (or a `tmux` pane / LaunchAgent),
then use `AI_BACKEND=local` — or no keys at all, since `local` is the final fallback.

---

## Creating your own agents

Every directory can be its own persistent agent — with its own indexed file
map, conversation database, long-term memory, and personality. Make as many as
you want:

```bash
# A local-model coding agent in a project folder
AI_BACKEND=local ai init ~/code/my-app -coder

# A research agent (Claude) in a notes folder
AI_BACKEND=claude ai init ~/notes/research -brief

# A general Hermes agent in a scratch workspace
AI_BACKEND=local ai init ~/agents/hermes-lab
```

What `ai init <path> [-skill]` does:

1. Indexes the directory into `index-map-<name>.txt` (re-indexed only when files change)
2. Opens a stateful chat primed with that map and the chosen skill
3. Stores its memory per-workspace: turns in `projects/database/<name>.db`,
   human-readable log in `<path>/history.md`, compiled facts in `<path>/.agent/tpm.md`

**Skills are the agent's personality/instructions.** They're plain Markdown
files anywhere under `skills/` — the filename is the skill name:

```bash
# Create skills/dept/hermes-writer.md with your system prompt, then:
AI_BACKEND=local ai init ~/agents/writer -hermes-writer
```

Built-in skills include `-coder`, `-architect`, `-refactor`, `-reviewer`,
`-brief`, `-thinking`, and more (see `skills/`). Load extra skills mid-session
with `/skill <name>`.

---

## Team orchestration in the terminal

You are the orchestrator: from inside `ai` (or as a one-shot) address any role
agent from `agents.json` directly — each has its own model, system prompt, and
persistent session, shared with the web GUI:

```bash
❯ @debug this stack trace says NoneType has no attribute 'id' — why?
❯ @review here's my diff, find the bugs
❯ @research compare SSE vs WebSockets for streaming chat
❯ @debug /new                # start a fresh Debugger session
❯ /team                      # roster, models, session counts

# Handoffs — no re-pasting between agents:
❯ @review /last debug        # Reviewer gets the Debugger's last answer
❯ @review /last debug check the fix for race conditions   # …with instructions
❯ @chat /last                # most recent answer from anyone on the team

ai @research "is bun production ready?"    # one-shot from the shell
```

Each reply ends with a dim usage line (`▪ model · ↓tokens ↑tokens · $cost`).

### Customizing the team

Full CRUD from the terminal — no file editing needed:

```bash
❯ /team add coder            # wizard: name, icon, backend, model, prompt
❯ /team show coder           # full config of one agent
❯ /team edit coder model deepseek/deepseek-v4-pro
❯ /team edit coder prompt You write clean, working code.
❯ /team edit coder backend claude    # or: name, icon
❯ /team rm coder             # confirms, offers to drop its sessions too
```

These all work as one-shots from the shell too (`ai /team add`). Under the
hood the team is just `agents.json` — you can still edit it by hand and the
change applies to the very next message (`/team` re-reads it live). Each entry:

```jsonc
{
  "id": "debug",                        // the @handle in the terminal
  "name": "Debugger",                   // display name (GUI sidebar, prefix)
  "icon": "🐞",
  "backend": "openrouter",              // openrouter (default) | claude | codex
  "model": "deepseek/deepseek-v4-pro",  // openrouter slug; claude: sonnet|opus|haiku; codex: gpt-5.2-codex or ""
  "system": "You are a senior debugging specialist…"
}
```

- **Model changes** — running sessions keep the model they were created
  with; start a fresh one with `@debug /new` to pick up the change.
- **Skills on agents** — `/team edit chat skills caveman` appends the skill
  file's text to that agent's system prompt (comma/space-separate for
  several, `none` to clear). The bundled `caveman` skill (from
  [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman)) makes an
  agent answer in terse caveman-speak — ~65% fewer output tokens, code and
  errors stay exact. Install more with `/skill add <name> <owner/repo|url>`.
- **MCP tools on agents** — declare servers with
  `/mcp add <name> <url | command…>` (stored in `mcp.json`), then attach
  with `/team edit <id> mcp <name>`. The agent can call the server's tools
  mid-answer (shown as dim `∗ server.tool {…}` lines); OpenRouter backend
  only, replies arrive whole because tool rounds can't stream. Try the demo:
  `/mcp tools everything`, then ask an attached agent to "use get-sum".
- **`claude` / `codex` backends** ride your Claude Pro / ChatGPT subscriptions
  through the CLI logins: no per-token cost, token counts are estimates, and
  codex replies arrive in one chunk (its exec mode can't stream). They need
  the CLI installed on whatever machine runs the engine — so on the VPS
  Docker stack, keep team agents on `openrouter`.

## Multi-agent API server (+ local GUI)

On top of the terminal agent there's a multi-agent engine
(`modules/agent_service.py`): role agents from `agents.json`, sessions
persisted as JSON under `.sessions/`, exposed by a stdlib-only REST + SSE
server:

```bash
ais        # serves http://127.0.0.1:8765
```

| Endpoint | What |
| :--- | :--- |
| `GET /api/agents` | The team roster (from `agents.json`) |
| `GET /api/sessions[?agent=]` · `POST /api/sessions` | List / create sessions |
| `POST /api/chat` `{session_id, message}` | Streamed reply (SSE), tools and cost included |

The terminal `@role` messages and this API share the same sessions, so any
frontend you build sees the same conversations.

> **Note:** a Next.js + Electron desktop GUI exists for this API but is kept
> out of the published repo (local `gui/` folder, gitignored). If you're
> deploying the Docker stack from `deploy/`, copy that folder up alongside
> the repo — everything else about the deploy is self-contained.

**Branding assets** live in [`assets/`](assets/): `dotai-logo.png` (wordmark),
`dotai-icon.png` (square app icon), and `favicon/` (full favicon set +
webmanifest for the web GUI). The local `gui/` folder carries ready-to-use
copies: `gui/public/` serves the favicons, and `gui/assets/dotai.icns` is the
macOS icon for Electron packaging (`electron/main.js` already sets the dock
and window icon in dev).

**Hosting it on a VPS:** the stack ships as Docker containers (API + GUI
behind a password-protected Caddy proxy), so all your devices share the same
sessions — see [deploy/README.md](deploy/README.md). Sync the local-only GUI
folder to the VPS with:

```bash
rsync -a --exclude node_modules --exclude .next gui/ user@vps:~/local-ai/gui/
```

---

## Command reference

### Shell commands

| Command | Description |
| :--- | :--- |
| `ai` | Interactive multi-turn chat |
| `ai <query>` | One-shot answer, straight back to your prompt |
| `ai init <path> [-skill]` | Launch (or create) a codebase-aware workspace agent |
| `ais` | Multi-agent API server on :8765 (for the web GUI) |
| `hs` / `hist` | Search / view the active workspace history |

### In-session commands

| Command | Description |
| :--- | :--- |
| `@<role> <msg>` | Message a team agent (`@debug`, `@review`, `@research`, `@chat`); `@<role> /new` starts a fresh session. Agents can read project files and run shell commands (`flutter analyze`, tests, …) — each command asks you y/n first |
| `@<role> /last [from] [note]` | Handoff: send a teammate's last answer to this agent (`@review /last debug find bugs`) |
| `/team` | List the team agents, their models, and session counts |
| `/team add [id]` | Create a team agent (wizard asks name, icon, backend, model, prompt) |
| `/team edit <id> <field> <value>` | Change `name`, `icon`, `model`, `backend`, `prompt`, `skills`, or `mcp` |
| `/team rm <id>` | Delete a team agent (asks about its sessions too) |
| `/team show <id>` | Show one agent's full config |
| `/agent <name>` | Switch backend mid-chat: `claude`, `codex`, `openrouter`, `gemini`, `local` (your llama.cpp model), or `auto` |
| `/model <name>` | Change the current backend's model (e.g. `/model haiku`, `/model deepseek/deepseek-v4-pro`) |
| `/effort <level>` | Codex reasoning effort: `minimal` `low` `medium` `high` |
| `/skill <name>` (or `/s`) | Load a skill on the fly |
| `/skill list` / `add <name> <owner/repo\|url>` / `rm <name>` | Manage skills (installs to `skills/custom/`) |
| `/mcp` / `add <name> <url\|command…>` / `tools <name>` / `rm <name>` | Manage MCP servers (`mcp.json`); attach with `/team edit <id> mcp <name>` |
| `view file <path>` | Read a local file into context |
| `/project [name\|path]` | List projects or focus one mid-chat — each project keeps its own memory, history, and cloud session; an unknown name creates the project |
| `/edit [on\|auto\|off]` | Edit mode: the agent can modify the focused project's files. `on` asks you **y/n before every write / shell command** (Claude Code-style permission prompt, via a PreToolUse hook on the `claude` CLI); `auto` skips the prompts; reads are always allowed. Claude/Codex run as full agents in the project; OpenRouter/local get read/write/list/run file tools — writes are confined to the project |
| `/usage` | Spend ledger: tokens & cost per model (all time + today) and your live OpenRouter credit balance; `/usage reset` zeroes it |
| `/save <tag>` / `/load` | Snapshot / roll back the conversation (SQLite) |
| `/f` `/t` `/b` `/a` | Follow-up / Thinking / Brainstorm / All prompt subroutines |
| `/clear` `/reset` | Wipe session, history, and memory for this workspace |
| `/m` | Toggle long-term memory |
| `/stats` / `/tok` | Toggle speed metrics / show token usage |
| `/d` / `/e` | Disable / enable the spellchecker |
| `/help` | Show all commands in the terminal |
| `/exit` / `/q` | Leave the session |

---

## Troubleshooting

| Symptom | Fix |
| :--- | :--- |
| `ai: command not found` | Open a new terminal, or `source ~/.zshrc` |
| Claude backend silent / erroring | Run `claude login` once; check `claude -p "hi"` works by itself |
| **Claude waiting for token reset** | `aio "…"` (OpenRouter) or `ail` (local) — the cascade also does this automatically |
| `localhost:8080 failed` | Your llama-server isn't running — `./start-hermes.sh` |
| Wrong/stale model shown in the startup box | The box reflects `AI_BACKEND` + keys at launch; check your exports |
| OpenRouter 402 / insufficient credits | Top up at openrouter.ai/credits, or use a `:free` model variant |

---

## Credits & License

Maintained by **suyadnya**. Based on the MIT-licensed Local-AI Agent — see
[LICENSE](LICENSE) for full attribution. Contributions welcome.
