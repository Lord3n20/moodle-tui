"""Chatfenster: Fragen an Claude über die `claude`-Kommandozeile (läuft über Enzos Abo)."""

import json
import os
import shutil
import subprocess
import time
from datetime import date

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Markdown, Static

from .api import CACHE_DIR
from .dump import USAGE

SYSTEM = """Du bist als Chat in Enzos Moodle-TUI (RWTHmoodle, RWTH Aachen) eingebettet.
Antworte auf Deutsch, knapp und gut lesbar im Terminal: kurze Absätze, Listen ok, keine Tabellen.

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


class ChatScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "dismiss", "Schließen"),
        Binding("ctrl+n", "new_chat", "Neuer Chat"),
    ]
    DEFAULT_CSS = """
    ChatScreen { align: right top; background: $background 50%; }
    #chat { width: 55%; min-width: 50; height: 100%; background: $surface; border-left: thick $accent; padding: 0 1; }
    #chat-title { color: $accent; text-style: bold; }
    #chat-log { height: 1fr; }
    #chat-log Markdown { margin: 0; padding: 0; }
    .user { margin-top: 1; color: $text-muted; text-style: bold; }
    #chat-status { color: $text-muted; height: 1; }
    """

    def __init__(self, context):
        super().__init__()
        self.context = context
        self.widgets = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="chat"):
            yield Label("Claude  ·  Esc schließt  ·  Strg+N neuer Chat", id="chat-title")
            yield VerticalScroll(id="chat-log")
            yield Label("", id="chat-status")
            yield Input(placeholder="Frage zu Moodle …", id="chat-input")

    def on_mount(self):
        for entry in self.app.chat_history:
            self.mount_entry(entry)
        if not self.app.chat_history:
            self.query_one("#chat-log").mount(Static(
                "[dim]Frag z. B. „Was muss ich bis nächste Woche lesen?“ oder „Fass das markierte PDF zusammen“.[/]"))
        self.set_status(self.app.chat_status)
        self.query_one(Input).focus()

    def mount_entry(self, entry):
        log = self.query_one("#chat-log", VerticalScroll)
        if entry["role"] == "user":
            log.mount(Static(f"Du: {escape(entry['text'])}", classes="user"))
        else:
            md = Markdown(entry["text"] or "…")
            self.widgets[id(entry)] = md
            log.mount(md)
        log.scroll_end(animate=False)

    def refresh_entry(self, entry):
        md = self.widgets.get(id(entry))
        if md is not None:
            md.update(entry["text"] or "…")
            self.query_one("#chat-log", VerticalScroll).scroll_end(animate=False)

    def set_status(self, text):
        self.query_one("#chat-status", Label).update(escape(text))

    def on_input_submitted(self, event: Input.Submitted):
        question = event.value.strip()
        if not question or self.app.chat_busy:
            return
        event.input.clear()
        user = {"role": "user", "text": question}
        answer = {"role": "assistant", "text": ""}
        self.app.chat_history += [user, answer]
        self.mount_entry(user)
        self.mount_entry(answer)
        self.app.ask_claude(question, self.context, answer)

    def action_new_chat(self):
        if self.app.chat_busy:
            return
        self.app.chat_history.clear()
        self.app.chat_session = None
        self.widgets.clear()
        self.query_one("#chat-log").remove_children()
        self.set_status("Neuer Chat.")


class ChatMixin:
    """Chat-Zustand der App; der Worker läuft an der App, damit Schließen des Fensters ihn nicht abbricht."""

    def init_chat(self):
        # Chat ist optional: nur mit installierter `claude`-CLI, abschaltbar mit MOODLE_TUI_CHAT=0
        self.chat_enabled = bool(shutil.which("claude")) and os.environ.get("MOODLE_TUI_CHAT", "1") != "0"
        self.chat_history = []
        self.chat_session = None
        self.chat_status = ""
        self.chat_busy = False

    def check_action(self, action, parameters):
        if action == "chat" and not self.chat_enabled:
            return False  # Taste c ausblenden
        return super().check_action(action, parameters)

    def action_chat(self):
        if isinstance(self.screen, ChatScreen):
            return
        context = getattr(self.screen, "chat_context", lambda: "Übersicht")()
        self.push_screen(ChatScreen(context))

    def _chat_screen(self):
        return self.screen if isinstance(self.screen, ChatScreen) else None

    def _chat_status(self, text):
        self.chat_status = text
        if screen := self._chat_screen():
            screen.set_status(text)

    def _chat_refresh(self, entry):
        if screen := self._chat_screen():
            screen.refresh_entry(entry)

    def ask_claude(self, question, context, entry):
        self.chat_busy = True
        self._chat_status("Claude denkt nach …")

        def work():
            last = [0.0]

            def on_text(text):
                entry["text"] = text
                if time.monotonic() - last[0] > 0.15:
                    last[0] = time.monotonic()
                    self.call_from_thread(self._chat_refresh, entry)

            def on_status(status):
                self.call_from_thread(self._chat_status, status)

            text, session, error = run_claude(question, context, self.chat_session, on_text, on_status)
            entry["text"] = text + (f"\n\n**Fehler:** {error}" if error else "")
            self.chat_session = session

            def done():
                self.chat_busy = False
                self._chat_refresh(entry)
                self._chat_status("")

            self.call_from_thread(done)

        self.run_worker(work, thread=True, group="chat")
