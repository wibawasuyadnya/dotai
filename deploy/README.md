# Deploying OrkesAI to a VPS (Docker)

Runs the multi-agent app on a small VPS (tested target: Biznet Gio 4 vCPU /
8 GB / 60 GB). Inference happens on OpenRouter, so the VPS only serves the
GUI and the session API — sessions are stored server-side, so every device
(Mac browser, Mac Electron app, phone) sees the same conversations.

Three containers, one exposed port:

```
browser / Electron ──► Caddy :80/:443 (basic auth)
                         ├── /api/* ──► api  (Python, server/server.py)
                         └── /      ──► gui  (Next.js standalone)
```

## 1. VPS setup

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh

git clone https://github.com/suyadnya/orkesai.git
cd orkesai/deploy
cp .env.example .env
```

The `gui/` app is not in the git repo (kept local-only) — copy it up from
your machine before building:

```bash
# run on your Mac
rsync -a --exclude node_modules --exclude .next gui/ user@vps:~/orkesai/gui/
```

Edit `deploy/.env`:

- `OPENROUTER_API_KEY` — your key
- `GUI_PASS_HASH` — generate pre-escaped for compose (`$` must be doubled) with
  `docker run --rm caddy:2-alpine caddy hash-password --plaintext 'your-password' | sed 's/\$/$$/g'`
- `SITE_ADDRESS`:
  - **Option A — Tailscale (recommended, no domain needed):** keep `:80`.
    Install [Tailscale](https://tailscale.com) on the VPS and your Mac
    (`curl -fsSL https://tailscale.com/install.sh | sh` then `tailscale up`).
    The app is then reachable only inside your tailnet at
    `http://<vps-tailscale-ip>` — nothing is exposed to the internet, and in
    your VPS firewall / Biznet Gio security group you keep 80/443 closed to
    the public.
  - **Option B — public domain:** set `SITE_ADDRESS=your.domain.com` (DNS A
    record → VPS IP, ports 80+443 open). Caddy fetches Let's Encrypt HTTPS
    automatically. Basic auth is what stands between the internet and your
    OpenRouter balance — use a strong password.

Then:

```bash
docker compose up -d --build
curl -u admin:your-password http://localhost/api/health   # {"ok": true}
```

## 2. Point your Mac at it

Browser: open `http://<vps-tailscale-ip>` (or `https://your.domain.com`) and
log in with GUI_USER / your password.

Electron app (remote mode — no local server is spawned):

```bash
cd gui
AI_GUI_URL=http://<vps-address> AI_GUI_USER=admin AI_GUI_PASS='your-password' npm run electron
```

Running it with no env vars keeps the original all-local behavior
(`next dev` + local Python server on :8765).

## Notes

- Sessions persist in the `sessions` Docker volume; `docker compose down`
  keeps them, `down -v` wipes them.
- Update after a git pull: `docker compose up -d --build`.
- Change agents/models by editing `agents.json` and rebuilding the api image.
- Logs: `docker compose logs -f api`.
