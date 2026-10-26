"""Startet die TUI mit echtem Konto in tmux (Gesehen-Stand im Tempordner) und schneidet Bildschirme mit.

Aufruf: python tests/real_run.py AUSGABEORDNER [KURS-NR]"""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.argv[1:2] == ["--app"]:
    from moodle_tui import app as A
    A.STATE_DIR = Path(tempfile.mkdtemp())
    A.MoodleApp().run()
    sys.exit()

from tmux import keys, shot, start, stop
out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
start(f"cd {Path(__file__).resolve().parent.parent} && {sys.executable} tests/real_run.py --app", (150, 45))
keys(wait=5); shot("real-home", out)
keys(*["Down"] * int(sys.argv[2] if len(sys.argv) > 2 else 0), "Enter", wait=3); shot("real-course", out)
keys(*["Down"] * 9, "Enter", "Down", wait=1); shot("real-course-2", out)
keys("q"); stop()
