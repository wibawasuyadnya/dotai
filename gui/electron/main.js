// Electron shell for the DotAI GUI.
//
// Local (default):  loads http://127.0.0.1:3000 and spawns the Python API
//                   server on :8765 if it isn't already running.
// Remote (VPS):     AI_GUI_URL=http://your-vps npm run electron
//                   — no local server is spawned; if the VPS is behind
//                   Caddy basic auth, set AI_GUI_USER / AI_GUI_PASS too.
const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const os = require("os");
const path = require("path");

const GUI_URL = process.env.AI_GUI_URL || "http://127.0.0.1:3000";
const IS_LOCAL = /^https?:\/\/(127\.0\.0\.1|localhost)[:/]/.test(GUI_URL + "/");
const API_URL = "http://127.0.0.1:8765/api/health";
const SERVER = path.join(os.homedir(), ".config", "local-ai", "server", "server.py");

let serverProc = null;

function ping(url) {
  return new Promise((resolve) => {
    http.get(url, (res) => resolve(res.statusCode === 200)).on("error", () => resolve(false));
  });
}

async function ensureServer() {
  if (await ping(API_URL)) return;
  serverProc = spawn("python3", [SERVER], { stdio: "inherit" });
  for (let i = 0; i < 20; i++) {
    if (await ping(API_URL)) return;
    await new Promise((r) => setTimeout(r, 250));
  }
}

async function createWindow() {
  if (IS_LOCAL) await ensureServer();
  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    backgroundColor: "#16181d",
    titleBarStyle: "hiddenInset",
    webPreferences: { contextIsolation: true },
  });
  win.loadURL(GUI_URL);
}

// Answer Caddy's basic-auth challenge when credentials are provided
app.on("login", (event, _wc, _details, _authInfo, callback) => {
  if (process.env.AI_GUI_USER) {
    event.preventDefault();
    callback(process.env.AI_GUI_USER, process.env.AI_GUI_PASS || "");
  }
});

app.whenReady().then(createWindow);

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on("window-all-closed", () => {
  if (serverProc) serverProc.kill();
  app.quit();
});
