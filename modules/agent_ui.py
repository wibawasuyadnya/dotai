# File: ~/.config/local-ai/modules/agent_ui.py
import os
import shutil
import sys
import textwrap
import threading
import time
import select
import re
from typing import Optional, Callable

try:
    import tty
    import termios
except ImportError:
    pass

class InlineSpinner:
    """A lightweight, thread-safe on-demand ANSI terminal spinner for CLI operations.
    Carries a live activity label (thinking / checking / updating / running…)
    so the user sees what the agent is doing, not just that it is busy."""
    def __init__(self, chars: str = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
        self.chars: str = chars
        self.active: bool = False
        self.label: str = "thinking"
        self.thread: Optional[threading.Thread] = None

    def _spin(self) -> None:
        idx: int = 0
        char_len: int = len(self.chars)
        while self.active:
            try:
                char = self.chars[idx % char_len]
                sys.stderr.write(f"\r\x1b[2K\033[1;32m{char}\033[0m \033[2m{self.label}…\033[0m ")
                sys.stderr.flush()
            except Exception:
                pass
            idx += 1
            time.sleep(0.08)
        sys.stderr.write("\r\x1b[2K\r")
        sys.stderr.flush()

    def set_label(self, label: str) -> None:
        self.label = label or "thinking"

    def start(self, label: str = None) -> None:
        if label:
            self.label = label
        elif not self.active:
            self.label = "thinking"
        if not self.active:
            self.active = True
            self.thread = threading.Thread(target=self._spin, daemon=True)
            self.thread.start()

    def stop(self) -> None:
        if self.active:
            self.active = False
            if self.thread:
                self.thread.join()
                self.thread = None

def get_key() -> str:
    """Reads a single key or escape sequence from /dev/tty directly or falls back to stdin."""
    tty_file = None
    try:
        tty_file = open("/dev/tty", "r+")
        fd = tty_file.fileno()
    except Exception:
        fd = sys.stdin.fileno()

    try:
        import fcntl
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
    except Exception:
        pass

    try:
        old_settings = termios.tcgetattr(fd)
    except Exception:
        try:
            char_bytes = os.read(fd, 1)
            return char_bytes.decode("utf-8", errors="ignore")
        except Exception:
            return ""
        finally:
            if tty_file:
                tty_file.close()

    try:
        tty.setraw(fd)
        try:
            termios.tcflush(fd, termios.TCIFLUSH)
        except Exception:
            pass
        char_bytes = os.read(fd, 1)
        if char_bytes == b'\x1b' and select.select([fd], [], [], 0.05)[0]:
            char_bytes += os.read(fd, 2)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        if tty_file:
            tty_file.close()
    return char_bytes.decode("utf-8", errors="ignore")

# ── Bottom-pinned composer (opencode-style, via DECSTBM scroll region) ──────
# The last two terminal rows are reserved: ❯ composer + dim statusline.
# Content scrolls in the region above them — no TUI framework involved.

_RESERVED = 2
_region_active = False
_region_rows = 0


def _term():
    return shutil.get_terminal_size((80, 24))


def bottom_input_on() -> None:
    """(Re)establish the scroll region above the reserved bottom rows."""
    global _region_active, _region_rows
    if not sys.stdout.isatty():
        return
    rows = _term().lines
    if _region_active and _region_rows == rows:
        return
    if not _region_active:
        # First enable: push content up so the reserved rows start empty
        sys.stdout.write("\n" * _RESERVED + f"\033[{_RESERVED}A")
    sys.stdout.write("\0337" + f"\033[1;{max(3, rows - _RESERVED)}r" + "\0338")
    _region_active, _region_rows = True, rows
    sys.stdout.flush()


def bottom_input_off() -> None:
    """Reset the scroll region and park the cursor on a clean last row."""
    global _region_active
    if not sys.stdout.isatty() or not _region_active:
        return
    rows = _term().lines
    sys.stdout.write(f"\033[r\033[{rows};1H\033[2K\r")
    _region_active = False
    sys.stdout.flush()


def composer_prepare(status: str) -> None:
    """Save the content cursor, draw the statusline, park on the composer row.
    Call input() right after; call composer_done() once it returns."""
    if not sys.stdout.isatty():
        return
    bottom_input_on()
    t = _term()
    rows, cols = t.lines, t.columns
    sys.stdout.write("\0337")
    sys.stdout.write(f"\033[{rows};1H\033[2K\033[2m{status[:cols - 1]}\033[0m")
    sys.stdout.write(f"\033[{rows - 1};1H\033[2K")
    sys.stdout.flush()


def composer_done() -> None:
    """Clear the composer rows and return the cursor to the content area."""
    if not sys.stdout.isatty() or not _region_active:
        return
    rows = _term().lines
    sys.stdout.write(f"\033[{rows - 1};1H\033[2K\033[{rows};1H\033[2K")
    sys.stdout.write("\0338")
    sys.stdout.flush()


def current_model_name() -> str:
    """The model the main chat effectively talks to (mirrors the cascade)."""
    backend = os.environ.get("AI_BACKEND", "").strip().lower()
    gkey = os.environ.get("GEMINI_API_KEY")
    okey = os.environ.get("OPENROUTER_API_KEY")
    if backend == "claude":
        return f"claude ({os.environ.get('CLAUDE_MODEL', 'sonnet')})"
    if backend == "codex":
        return f"codex ({os.environ.get('CODEX_MODEL', 'default')})"
    if backend == "openrouter" and okey:
        return os.environ.get("OPENROUTER_MODEL", "openrouter/free")
    if backend == "local":
        return "local-model"
    if gkey:
        return os.environ.get("CLOUD_MODEL", "gemini-3.1-flash-lite")
    if okey:
        return os.environ.get("OPENROUTER_MODEL", "openrouter/free")
    return "local-model"


def draw_session_box(
    workspace_path: str,
    home_dir: str,
    is_agent: bool,
    db_turns: int,
    tpm_count: int,
    memory_active: bool,
    active_system_prompt: str,
    clean_name: str
) -> None:
    """DotAI banner left, dim context right (opencode-style split)."""
    cols = shutil.get_terminal_size((80, 24)).columns
    display_dir = workspace_path
    if display_dir.startswith(home_dir):
        display_dir = display_dir.replace(home_dir, "~", 1)

    right_parts = [current_model_name()]
    try:
        import agent_usage
        spend = agent_usage.header_spend()
        if spend:
            right_parts.append(spend)
    except Exception:
        pass
    if clean_name:
        right_parts.append(clean_name)
    if is_agent:
        right_parts.append(f"mem {tpm_count}f/{db_turns}t" if memory_active else "mem off")
    right_parts.append(display_dir)
    left_plain = "● dotai"
    right_plain = " · ".join(right_parts)
    pad = max(2, cols - len(left_plain) - len(right_plain))
    print(f"\033[1;36m●\033[0m \033[1mdotai\033[0m{' ' * pad}\033[2m{right_plain}\033[0m\n")


def echo_user_block(query: str) -> None:
    """Renders the submitted message as an opencode-style shaded block with a
    left accent bar (the typed line itself lives on the composer row)."""
    if not sys.stdout.isatty():
        return
    cols = shutil.get_terminal_size((80, 24)).columns
    lines = []
    for ln in query.splitlines() or [""]:
        lines.extend(textwrap.wrap(ln, max(10, cols - 4)) or [""])
    # Thin 1px accent bar, single-line block (no padding rows)
    bar = "\033[38;5;110;48;5;235m▏\033[0m\033[48;5;235m"
    for ln in lines:
        print(f"{bar} {ln.ljust(cols - 3)}\033[0m")
    print()


def render_diff(old: str, new: str, path: str = "", max_lines: int = 60) -> str:
    """Colored unified diff of a file change: red - removed, green + added,
    dim @@ hunk headers with line numbers. Used before every edit so the user
    sees exactly which lines change. Returns '' when nothing differs."""
    import difflib
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))
    if not diff:
        return ""
    out = []
    if path:
        out.append(f"\033[1m  {path}\033[0m")
    shown = 0
    for ln in diff:
        if ln.startswith(("---", "+++")):
            continue
        if shown >= max_lines:
            out.append(f"\033[2m  … diff truncated ({len(diff) - shown} more lines)\033[0m")
            break
        if ln.startswith("@@"):
            out.append(f"\033[2m  {ln}\033[0m")
        elif ln.startswith("+"):
            out.append(f"\033[32m  + {ln[1:]}\033[0m")
        elif ln.startswith("-"):
            out.append(f"\033[31m  - {ln[1:]}\033[0m")
        else:
            out.append(f"\033[2m    {ln[1:]}\033[0m")
        shown += 1
    added = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
    out.append(f"\033[2m  +{added} -{removed} lines\033[0m")
    return "\n".join(out)


def print_diff(old: str, new: str, path: str = "") -> None:
    rendered = render_diff(old, new, path)
    if rendered:
        sys.stderr.write(rendered + "\n")
        sys.stderr.flush()


def confirm_tool(tool: str) -> bool:
    """Prompt user to authorize executing a dynamic tool, defaulting to Yes on Enter."""
    sys.stderr.write(f"\033[1;33m[sys] Authorize tool: {tool}? [Y/n]: \033[0m")
    sys.stderr.flush()
    try:
        char = get_key()
    except Exception:
        char = ""
    is_yes = char.lower() == 'y' or char in ('\r', '\n', '')
    if char in ('\r', '\n', ''):
        sys.stderr.write("y\n")
    elif char.startswith('\x1b') or char == '\x03':
        sys.stderr.write("n\n")
    else:
        sys.stderr.write(f"{char}\n")
    sys.stderr.flush()
    return is_yes


def run_interactive_selection(
    intent: str,
    jaccard_search_fn: Callable[[str], Optional[str]],
    clean_tool_prefix_fn: Callable[[str], str],
    print_stock_error_fn: Callable[[str], None],
    ensure_mysys_exists_fn: Callable[[], None]
) -> None:
    """Displays a menu overlay allowing arrow-selection and execution of mapped tools."""
    if re.search(r'[\[\]{}()=\'"",;|<>#]', intent):
        print_stock_error_fn(intent)
        sys.exit(127)

    matched_base = jaccard_search_fn(intent)
    if not matched_base:
        print_stock_error_fn(intent)
        sys.exit(127)

    options = matched_base.split("\n")
    num_opts = len(options)
    current_idx = 0
    
    sys.stderr.write("\033[?25l")
    sys.stderr.flush()

    try:
        while True:
            current_intent, current_cmd = options[current_idx].split("|||", 1)
            current_cmd = clean_tool_prefix_fn(current_cmd)
            is_danger = current_cmd.startswith("DANGER_FLAGGED:")
            cmd_to_show = current_cmd.replace("DANGER_FLAGGED:", "")
            display_cmd = cmd_to_show.replace(" >/dev/null 2>&1", "").replace(os.path.expanduser("~"), "~")
            
            if "/.config/local-ai/projects/" in display_cmd:
                display_cmd = display_cmd.replace("/.config/local-ai/projects/", "/")

            idx_str = f"{current_idx + 1:02d}/{num_opts:02d}"
            
            if is_danger:
                sys.stderr.write(
                    f"\r\x1b[K\033[1;31m▲ WARNING: Destructive payload detected\033[0m\n"
                    f"\r\x1b[K\033[1;31m[{idx_str}]\033[0m ❯ \x1b[1;36m[{current_intent}]\x1b[0m {display_cmd}\n"
                    f"\r\x1b[K\033[2m::\033[0m execute payload? [y/N]: "
                )
            else:
                sys.stderr.write(
                    f"\r\x1b[K\033[1;32m[{idx_str}]\033[0m ❯ \x1b[1;36m[{current_intent}]\x1b[0m {display_cmd}\n"
                    f"\r\x1b[K\033[2m::\033[0m ↵ run  Esc: "
                )
            sys.stderr.flush()
            
            key = get_key()
            if key in ('\x03', '\x1b') or (not is_danger and key not in ('\r', '', '\x1b[A', '\x1b[B')):
                sys.stderr.write("\r\x1b[K\x1b[1A\r\x1b[KCancelled.\n")
                sys.stderr.flush()
                break

            if is_danger:
                sys.stderr.write("\r\x1b[K\x1b[1A\r\x1b[K\x1b[1A\r\x1b[K")
                sys.stderr.flush()
                if key.lower() == 'y':
                    if "system" in cmd_to_show:
                        ensure_mysys_exists_fn()
                    sys.stdout.write(cmd_to_show)
                else:
                    sys.stderr.write("Aborted safely.\n")
                sys.stdout.flush()
                break

            if key in ('\r', ''):
                sys.stderr.write("\n")
                sys.stderr.flush()
                if "system" in cmd_to_show:
                    ensure_mysys_exists_fn()
                sys.stdout.write(cmd_to_show)
                sys.stdout.flush()
                break
            elif key in ('\x1b[A', '\x1b[B'):
                current_idx = (current_idx + (1 if key == '\x1b[B' else -1) + num_opts) % num_opts
                sys.stderr.write("\r\x1b[K\x1b[1A\r\x1b[K")
        sys.exit(0)
    except KeyboardInterrupt:
        sys.stderr.write("\r\x1b[K\x1b[1A\r\x1b[KCancelled.\n")
        sys.stderr.flush()
        sys.exit(130)
    finally:
        sys.stderr.write("\033[?25h")
        sys.stderr.flush()
