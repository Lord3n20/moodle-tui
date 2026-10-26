"""Hilfen, um die TUI in tmux fernzusteuern und Bildschirme als Text mitzuschneiden."""
import subprocess, time
from pathlib import Path

SESSION = "moodle-tui-test"


def tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True).stdout


def start(cmd, size=(140, 40)):
    tmux("kill-session", "-t", SESSION)
    tmux("new-session", "-d", "-s", SESSION, "-x", str(size[0]), "-y", str(size[1]), cmd)
    time.sleep(1)


def keys(*names, wait=0.5):
    for name in names:
        tmux("send-keys", "-t", SESSION, name)
        time.sleep(0.1)
    time.sleep(wait)


def shot(name, out):
    text = tmux("capture-pane", "-p", "-t", SESSION)
    Path(out, name + ".txt").write_text(text)
    print(f"===== {name}\n{text}")


def stop():
    tmux("kill-session", "-t", SESSION)
