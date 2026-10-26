"""Chatfenster: Fragen an Claude über die `claude`-Kommandozeile (läuft über Enzos Abo)."""

import json
import os
import shutil
import subprocess
import time
from datetime import date

from . import ui
from .api import CACHE_DIR
from .dump import USAGE
from .util import Doc

SYSTEM = """Du bist als Chat in Enzos Moodle-TUI (RWTHmoodle, RWTH Aachen) eingebettet.
Antworte auf Deutsch, knapp und gut lesbar im Terminal: kurze Absätze, reiner Text ohne Markdown-Auszeichnung
(kein **, #, ` oder Tabellen); Aufzählungen mit „- “ sind ok.

Moodle-Daten holst du nur lesend über Bash mit `moodle dump …` – immer als einzelnen Befehl,
ohne Pipes, ohne `&&`, ohne andere Programme:
{usage}

PDFs: erst `moodle dump file KURS-ID CMID`, dann den ausgegebenen Pfad mit Read lesen
(bei großen PDFs gezielt einzelne Seiten). Du kannst nichts abgeben, posten oder ändern –
dafür verweist du auf die Taste o (öffnet den Eintrag im Browser).

Heute ist {today}. Aktuelle Ansicht in der TUI: {context}"""


def run_claude(question, context, session_id, on_text, on_status):
    """Startet `claude -p`, streamt Text über on_text; gibt (Antwort, Session-ID, Fehler) zurück."""
    workdir = CACHE_DIR / "chat"
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--append-system-prompt", SYSTEM.format(usage=USAGE, today=date.today().strftime("%d.%m.%Y"),
                                                   context=context),
           "--tools", "Bash,Read",
           "--allowedTools", "Bash(moodle dump:*)", f"Read(/{CACHE_DIR}/**)"]
    if session_id:
        cmd += ["--resume", session_id]
    try:
        proc = subprocess.Popen(cmd, cwd=workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        return "", session_id, "`claude` ist nicht installiert."
    proc.stdin.write(question)
    proc.stdin.close()

    text, error = "", None
    for line in proc.stdout:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        kind = msg.get("type")
        if kind == "system" and msg.get("session_id"):
            session_id = msg["session_id"]
        elif kind == "stream_event":
            ev = msg.get("event", {})
            if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                text += ev["delta"]["text"]
                on_text(text)
            elif ev.get("type") == "content_block_start" and ev["content_block"].get("type") == "text" and text:
                text += "\n\n"
        elif kind == "assistant":
            for block in msg["message"].get("content", []):
                if block.get("type") == "tool_use":
                    inp = block.get("input", {})
                    on_status("⚙ " + (inp.get("command") or inp.get("file_path", "").rsplit("/", 1)[-1] or block["name"]))
        elif kind == "result":
            session_id = msg.get("session_id", session_id)
            if msg.get("is_error"):
                error = msg.get("result") or "Fehler bei Claude."
            elif not text:
                text = msg.get("result", "")
    proc.wait()
    if proc.returncode and not error and not text:
        error = (proc.stderr.read() or "claude ist abgebrochen.").strip()[-500:]
    return text, session_id, error


class ChatScreen(ui.Screen):
    modal = True
    bindings = [
        ("escape", "Schließen", "back"),
        ("ctrl+n", "Neuer Chat", "new_chat"),
    ]

    def __init__(self, context):
        self.context = context

    def mount(self):
        self.log = ui.TextView()
        self.input = ui.Input(self.submit, "Frage zu Moodle …")
        self.focus = self.input
        self.update()

    def focusables(self):
        return [self.input]

    def widgets(self):
        return [self.log, self.input]

    def update(self):
        doc = Doc()
        if not self.app.chat_history:
            doc.text("Frag z. B. „Was muss ich bis nächste Woche lesen?“ oder „Fass das markierte PDF zusammen“.", "d")
        for entry in self.app.chat_history:
            doc.gap()
            if entry["role"] == "user":
                doc.text(f"Du: {entry['text']}", "bd")
            else:
                doc.text(entry["text"] or "…")
        self.log.set(doc.lines, end=True)

    def draw(self, win, h, w):
        cw = max(min(w, 50), int(w * 0.55))
        x = w - cw
        for row in range(h):
            ui.put(win, row, x, " " * cw)
        ui.vline(win, 0, x, h, "┃", ui.STYLE["a"])
        x, cw = x + 2, cw - 3
        ui.put(win, 0, x, "Claude  ·  Esc schließt  ·  Strg+N neuer Chat", ui.STYLE["h"], cw)
        self.log.draw(win, 2, x, h - 5, cw)
        ui.put(win, h - 2, x, self.app.chat_status, ui.STYLE["d"], cw)
        self.cursor_pos = self.input.draw(win, h - 1, x, 1, cw)

    def key(self, k):
        if k in ("up", "down", "pageup", "pagedown"):
            self.log.key(k)
        else:
            super().key(k)

    def submit(self, text):
        question = text.strip()
        if not question or self.app.chat_busy:
            return
        self.input.clear()
        answer = {"role": "assistant", "text": ""}
        self.app.chat_history += [{"role": "user", "text": question}, answer]
        self.update()
        self.app.ask_claude(question, self.context, answer)

    def new_chat(self):
        if self.app.chat_busy:
            return
        self.app.chat_history.clear()
        self.app.chat_session = None
        self.app.chat_status = "Neuer Chat."
        self.update()


class ChatMixin:
    """Chat-Zustand der App; der Thread läuft an der App, damit Schließen des Fensters ihn nicht abbricht."""

    def init_chat(self):
        # Chat ist optional: nur mit installierter `claude`-CLI, abschaltbar mit MOODLE_TUI_CHAT=0
        self.chat_enabled = bool(shutil.which("claude")) and os.environ.get("MOODLE_TUI_CHAT", "1") != "0"
        self.chat_history = []
        self.chat_session = None
        self.chat_status = ""
        self.chat_busy = False

    def allowed(self, action):
        return action != "chat" or self.chat_enabled

    def chat(self):
        if isinstance(self.screen, ChatScreen):
            return
        context = getattr(self.screen, "chat_context", lambda: "Übersicht")()
        self.push(ChatScreen(context))

    def _chat_refresh(self):
        if isinstance(self.screen, ChatScreen):
            self.screen.update()

    def _chat_status(self, text):
        self.chat_status = text

    def ask_claude(self, question, context, entry):
        self.chat_busy = True
        self.chat_status = "Claude denkt nach …"

        def work():
            last = [0.0]

            def on_text(text):
                entry["text"] = text
                if time.monotonic() - last[0] > 0.15:
                    last[0] = time.monotonic()
                    self.post(self._chat_refresh)

            def on_status(status):
                self.post(self._chat_status, status)

            text, session, error = run_claude(question, context, self.chat_session, on_text, on_status)
            entry["text"] = text + (f"\n\nFehler: {error}" if error else "")
            self.chat_session = session

            def done():
                self.chat_busy = False
                self.chat_status = ""
                self._chat_refresh()

            self.post(done)

        self.thread(work)
