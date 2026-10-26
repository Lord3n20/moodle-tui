"""Startet die TUI mit Testdaten in tmux und schneidet die Bildschirme mit: python tests/fake_run.py AUSGABEORDNER"""
import os, struct, subprocess, sys, time, zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
now = int(time.time())
SITE = "https://moodle.rwth-aachen.de"


def png(path, w=200, h=100):
    raw = b"".join(b"\0" + bytes(v for x in range(w) for v in (x * 255 // w, y * 255 // h, 128)) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


# Kleinste PDF mit zwei Seiten und Text
PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R 5 0 R]/Count 2>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 6 0 R>>>>>>endobj
4 0 obj<</Length 44>>stream
BT /F1 24 Tf 20 100 Td (Seite eins) Tj ET
endstream endobj
5 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 7 0 R/Resources<</Font<</F1 6 0 R>>>>>>endobj
6 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
7 0 obj<</Length 44>>stream
BT /F1 24 Tf 20 100 Td (Seite zwei) Tj ET
endstream endobj
trailer<</Root 1 0 R>>
%%EOF"""


class Fake:
    userid = 1
    def courses(self): return [{"id": 1, "fullname": "Analysis I [WiSe 26/27]", "shortname": "Ana", "viewurl": "x"},
                               {"id": 2, "fullname": "Programmierung", "shortname": "Progra", "viewurl": "y"}]
    def deadlines(self): return [{"name": "Übungsblatt 3", "timesort": now + 3600, "course": {"id": 1, "shortname": "Ana", "fullname": "Analysis I"}, "description": "<p>Abgabe <b>bis</b> Mitternacht</p><ul><li>Aufgabe 1</li><li>Aufgabe 2</li></ul>", "url": "https://m/mod/assign/view.php?id=11"},
                                 {"name": "Quiz 2", "timesort": now + 5*86400, "course": {"id": 2, "shortname": "Progra", "fullname": "Programmierung"}, "description": "", "url": ""}]
    def notifications(self): return [{"id": 5, "subject": "Neue Ankündigung [Ana]", "read": False, "timecreated": now - 3600, "fullmessagehtml": "<p>Hallo</p>", "contexturl": SITE + "/mod/assign/view.php?id=11"}]
    def mark_notification_read(self, i): pass
    def course_contents(self, cid):
        return [{"name": "Allgemeines", "summary": "<p>Willkommen</p>", "modules": [
                    {"id": 10, "name": "Ankündigungen", "modname": "forum", "instance": 3, "contents": []},
                    {"id": 11, "name": "Blatt 3", "modname": "assign", "instance": 4, "contents": [], "dates": [{"label": "Fällig:", "timestamp": now + 3600}]},
                    {"id": 12, "name": "Skript", "modname": "resource", "contents": [{"type": "file", "filename": "skript.pdf", "filesize": 2_000_000, "timemodified": now + cid, "fileurl": "u"}]},
                    {"id": 13, "modname": "label", "description": "<p>Klausur am 20.02.</p><p><img src='https://m/bild.png' alt='Poster'></p><p>Darunter Text</p>", "contents": []}]},
                {"name": "Woche 1", "summary": "", "modules": [{"id": 14, "name": "Folien", "modname": "folder", "contents": [{"type": "file", "filename": "a.pdf", "filepath": "/", "filesize": 10, "timemodified": 1, "fileurl": "u"}]},
                    {"id": 16, "name": "Zum Skript", "modname": "url", "contents": [{"type": "url", "fileurl": SITE + "/mod/resource/view.php?id=12"}]},
                    {"id": 17, "name": "Extern", "modname": "url", "contents": [{"type": "url", "fileurl": "https://example.org/x"}]},
                    {"id": 15, "name": "La ropa", "modname": "resource", "contents": [{"type": "file", "filename": "ropa.jpeg", "filesize": 85_000, "timemodified": 1, "fileurl": "u"}]}]}]
    def course_module(self, cmid): return {"course": 1}
    def course_by_id(self, cid): return self.courses()[0]
    def assignments(self, cid): return {11: {"id": 4, "name": "Blatt 3", "duedate": now + 3600, "intro": "<p>Aufgaben</p>"}}
    def submission_status(self, a): return {"lastattempt": {"submission": {"status": "new"}}}
    def forum_discussions(self, f): return [{"name": "Klausurtermin", "discussion": 7, "numreplies": 2, "timemodified": now}]
    def discussion_posts(self, d): return [{"author": {"fullname": "Prof"}, "timecreated": now, "message": "<p>Am 20.02.</p>"}]
    def download(self, url, target, timemodified=0):
        target.parent.mkdir(parents=True, exist_ok=True)
        src = png(Path(os.environ["FAKE_DIR"]) / "datei.png")
        if target.suffix == ".jpeg":
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), str(target)])
        elif target.suffix == ".pdf":
            target.write_bytes(PDF)
        else:
            target.write_bytes(src.read_bytes())
        return target
    def image(self, url): return png(Path(os.environ["FAKE_DIR"]) / "bild.png")


if sys.argv[1:2] == ["--app"]:
    from moodle_tui import app as A
    A.Moodle = Fake
    A.STATE_DIR = Path(os.environ["FAKE_DIR"]) / "state"
    A.MoodleApp().run()
    sys.exit()

from tmux import keys, shot, start, stop
out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
start(f"env FAKE_DIR={out} TERM=xterm-256color {sys.executable} {Path(__file__).resolve()} --app")
shot("home", out)
keys("Tab", "Right"); shot("mitteilungen", out)
keys("Left", "BTab", "Enter"); shot("kurs", out)
keys("Down", "Enter", "Down"); shot("forum", out)
keys("Enter"); shot("diskussion", out)
keys("Down"); shot("aufgabe-markiert", out)
keys("Enter"); shot("aufgabe", out)
keys("Down", "Down"); shot("hinweis-mit-bild", out)
keys("Escape", "Tab", "Enter"); shot("frist-im-kurs", out)
keys("c", wait=0.3); keys("H", "a", "l", "l", "o", "Space", "ü"); shot("chat", out)
keys("Escape", "q", wait=0.5)
stop()

# Dateien anzeigen: einmal ohne kitty (Text/Ersatz), einmal mit TERM=xterm-kitty (Platzhalterzeichen)
for term in ("xterm-256color", "xterm-kitty"):
    start(f"cd {Path(__file__).resolve().parent.parent} && env FAKE_DIR={out} TERM={term} {sys.executable} {Path(__file__).resolve()} --app")
    keys("Enter", wait=1)
    keys(*["Down"] * 5, "Enter", wait=0.5)  # Woche 1 aufklappen
    keys("Down", "Down", "Enter", wait=2); shot(f"jpeg-{term}", out)
    keys("Up", "Enter", "Down", "Enter", wait=3); shot(f"pdf-{term}", out)
    keys("q"); stop()

# Links: Mitteilung → Aufgabe im Kurs, Link-Modul → Datei im selben Kurs, externer Link → Nachfrage
start(f"cd {Path(__file__).resolve().parent.parent} && env FAKE_DIR={out} TERM=xterm-256color {sys.executable} {Path(__file__).resolve()} --app")
keys("Tab", "Right", "Enter", wait=1.5); shot("link-mitteilung", out)
keys(*["Down"] * 3, "Enter", "Down", "Down", wait=0.5); shot("link-modul-markiert", out)
keys("Enter", wait=1.5); shot("link-intern", out)
keys(*["Down"] * 5, "Enter", wait=0.5); shot("link-extern-nachfrage", out)
keys("n", "o", wait=0.5); shot("o-nachfrage", out)
keys("Escape", "q"); stop()
