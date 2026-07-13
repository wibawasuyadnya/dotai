# ~/.config/orkesai/modules/speed_test.py
import time
import sys

_start_time = None
_token_count = 0
_label = ""

def set_label(label: str) -> None:
    """Model/backend tag shown on the end-of-turn status line."""
    global _label
    _label = label or ""

def start() -> None:
    """Begins the timer and resets the token count."""
    global _start_time, _token_count
    _start_time = time.time()
    _token_count = 0

def count_token(content: str) -> None:
    """Increments the generated token count based on streaming chunks."""
    global _token_count
    if content:
        # In streaming SSE responses (like llama-server), each delta content 
        # packet represents exactly one newly generated token.
        _token_count += 1

def end() -> None:
    """Calculates, prints the token statistics, and resets state."""
    global _start_time, _token_count
    if _start_time is None:
        return
    
    elapsed = time.time() - _start_time
    if elapsed <= 0:
        elapsed = 0.001
        
    tps = _token_count / elapsed
    
    # Opencode-style dim status line below the answer: ▪ model · tokens · speed
    # (full words, no abbreviations — the user reads these lines literally)
    tag = f"{_label} · " if _label else ""
    sys.stdout.write(f"\033[2m▪ {tag}{_token_count} tokens · {elapsed:.1f} seconds · {tps:.1f} tokens/sec\033[0m\n")
    sys.stdout.flush()
    
    # Clean up state
    _start_time = None
    _token_count = 0
