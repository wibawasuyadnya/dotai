# macOS Setup Notes (this machine) — DotAI

Project rebranded "DotAI" on 2026-07-05 (was "Local-AI Agent"); command is
still `ai` and the config path is still `~/.config/local-ai`.

This repo lives in `~/Documents/local-ai-main`, symlinked to `~/.config/local-ai`
(the path the code hardcodes). The hook is sourced from `~/.zshrc`, and
`ai-hook.sh` was patched for zsh (`precmd` instead of bash's `PROMPT_COMMAND`).

Full usage documentation is in [README.md](README.md). Machine-specific facts:

- **Hardware**: M4 Pro, 24 GB RAM — 14B Q4 models fit; DeepSeek V4 (284B MoE) does not, so it's used via OpenRouter only.
- **Backends configured in `.env`** (repo root, gitignored): `AI_BACKEND=openrouter` default (`deepseek/deepseek-v4-flash`), Claude via `claude` CLI account login, local Hermes-4-14B. DeepSeek direct API removed — all DeepSeek models go through OpenRouter (single top-up balance). Gemini intentionally unused.
- **Aliases**: `aic` (Claude) · `aio` (OpenRouter) · `ail` (local Hermes) · `ais` (API server :8765).
- **Multi-agent app** (added 2026-07-05, GUI-only — TUI was removed by choice): `agents.json` roles + `.sessions/` state; GUI in `gui/` (Next.js 15 + Electron 37 — on npm 11 run `npm approve-scripts electron && npm rebuild electron` once, and if the Electron binary check still fails, extract `~/Library/Caches/electron/*/electron-*.zip` into `gui/node_modules/electron/dist` with `ditto` and write `path.txt`).
- **Local model**: `./start-hermes.sh` serves Hermes-4-14B Q4_K_M on :8080; the ~8.8 GB GGUF is already cached in `~/.cache/huggingface`.
- **Rate-limit escape hatch**: if Claude is waiting for token reset, `aio <question>` — or just keep typing, the cascade falls through to OpenRouter/local on failure.
