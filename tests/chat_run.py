"""Echter Chat-Test in tmux: ersten Kurs öffnen, Frage stellen, Antwort abwarten.

Aufruf: python tests/chat_run.py AUSGABEORDNER "FRAGE\""""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tmux import keys, shot, start, stop, tmux

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
start(f"cd {Path(__file__).resolve().parent.parent} && {sys.executable} tests/real_run.py --app", (150, 45))
keys(wait=5)
keys("Enter", wait=3)
keys("c", wait=0.5)
tmux("send-keys", "-t", "moodle-tui-test", "-l", sys.argv[2])
keys("Enter", wait=2)
t0 = time.time()
while "Claude denkt" in tmux("capture-pane", "-p", "-t", "moodle-tui-test") or "⚙" in tmux("capture-pane", "-p", "-t", "moodle-tui-test"):
    if time.time() - t0 > 240:
        break
    time.sleep(1)
print(f"Dauer {time.time() - t0:.0f} s")
shot("chat", out)
keys("Escape", "q")
stop()
