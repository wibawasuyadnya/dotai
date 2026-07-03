<h1 align="center">Local-AI Agent</h1>

<p align="center">
  A zero-daemon, multi-backend AI agent for your terminal.<br>
  Chat with <b>Claude</b> (your claude.ai login — no API key), <b>DeepSeek</b>, <b>OpenRouter</b>, <b>Gemini</b>,
  or any <b>local GGUF model</b> (Hermes, Qwen, Llama, …) — with automatic fallback between them.
</p>

<p align="center">
  Maintained by <b>suyadnya</b>
</p>

---

## Features

- **5 interchangeable backends** — switch with a single env var, no restart, no config file
- **Automatic fallback cascade** — if your primary backend is down or rate-limited, the next one answers
- **Claude via your subscription** — uses the official `claude` CLI login, so no Anthropic API key is needed
- **Unlimited local agents** — every folder can become a persistent, codebase-aware agent with its own memory, history, and personality (skill)
- **Zero idle cost** — no daemon; nothing runs until you type `ai`
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
| **At least one backend** | Any of: Claude login, DeepSeek key, OpenRouter key, Gemini key, or a local model |

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
# Primary backend: claude | deepseek | openrouter | gemini | local
AI_BACKEND=claude

# Claude — NO API key needed, uses your claude.ai login via the claude CLI
CLAUDE_MODEL=sonnet                              # sonnet | haiku | opus

# DeepSeek (direct API) — key from platform.deepseek.com/api_keys
DEEPSEEK_API_KEY=sk-your-key-here
DEEPSEEK_MODEL=deepseek-v4-flash

# OpenRouter — one key, hundreds of models — openrouter.ai/keys
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENROUTER_MODEL=deepseek/deepseek-v4-flash:free

# Gemini — optional; leave commented out if you don't use it
# GEMINI_API_KEY=AIza-your-key-here
```

**Every line is optional** — leave anything commented out and the agent skips
that backend in the fallback chain. `.env` is gitignored, so your keys can
never be committed or published. The file is read both by the shell hook and
by the Python agent itself, so it works no matter how the agent is launched.
(Power users: plain environment variables also work and take priority over `.env`.)

These aliases are available out of the box for hopping between backends:

| Alias | Backend |
| :--- | :--- |
| `aic` | Claude (account login) |
| `aid` | DeepSeek direct API |
| `aio` | OpenRouter |
| `aig` | Gemini |
| `ail` | Local model (llama.cpp) |

### How routing works

`AI_BACKEND` promotes one engine to the front of the line; everything else you
have configured stays behind it as a fallback. So with `AI_BACKEND=claude` and
a DeepSeek key set, a Claude outage (or hitting your subscription's token
limit) automatically falls through to DeepSeek — or force it yourself for one
question:

```bash
aid "explain this error"        # DeepSeek, right now, regardless of default
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
> hardware — use them through the DeepSeek API or OpenRouter instead.

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

## Command reference

### Shell commands

| Command | Description |
| :--- | :--- |
| `ai` | Interactive multi-turn chat |
| `ai <query>` | One-shot answer, straight back to your prompt |
| `ai init <path> [-skill]` | Launch (or create) a codebase-aware workspace agent |
| `hs` / `hist` | Search / view the active workspace history |

### In-session commands

| Command | Description |
| :--- | :--- |
| `/skill <name>` (or `/s`) | Load a skill on the fly |
| `view file <path>` | Read a local file into context |
| `-save <tag>` / `-load` | Snapshot / roll back the conversation (SQLite) |
| `/f` `/t` `/b` `/a` | Follow-up / Thinking / Brainstorm / All prompt subroutines |
| `/clear` `/reset` | Wipe session, history, and memory for this workspace |
| `/m` | Toggle long-term memory |
| `/stats` / `/tok` | Toggle speed metrics / show token usage |
| `/d` / `/e` | Disable / enable the spellchecker |
| `exit` / `q` | Leave the session |

---

## Troubleshooting

| Symptom | Fix |
| :--- | :--- |
| `ai: command not found` | Open a new terminal, or `source ~/.zshrc` |
| Claude backend silent / erroring | Run `claude login` once; check `claude -p "hi"` works by itself |
| **Claude waiting for token reset** | `aid "…"` (DeepSeek) or `ail` (local) — the cascade also does this automatically |
| `localhost:8080 failed` | Your llama-server isn't running — `./start-hermes.sh` |
| Wrong/stale model shown in the startup box | The box reflects `AI_BACKEND` + keys at launch; check your exports |
| DeepSeek 402 / insufficient balance | Top up at platform.deepseek.com, or use the free OpenRouter variant |

---

## Credits & License

Maintained by **suyadnya**. Based on the MIT-licensed Local-AI Agent — see
[LICENSE](LICENSE) for full attribution. Contributions welcome.
