"""Oberfläche: Übersicht (Kurse, Fristen, Mitteilungen) und Kursansicht."""

import html
import json
import re
import shutil
import subprocess
import threading
import time

from . import ui
from .api import CACHE_DIR, SITE, STATE_DIR, Moodle, MoodleError, NotLoggedIn
from .chat import ChatMixin
from .util import Doc, fmt_size, fmt_time, html_lines

NEW = "●"
HINT = "di"  # Stil für Bedienhinweise
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg", ".heic", ".avif"}
TEXT_TYPES = {".txt", ".md", ".csv", ".tsv", ".py", ".java", ".c", ".h", ".cpp", ".js", ".tex", ".json", ".xml",
              ".yml", ".yaml", ".log", ".r", ".m", ".sql", ".sh"}
PDF_BATCH = 6  # Seiten je pdftoppm-Aufruf; nach jedem Stapel wird die Anzeige ergänzt


def txt(value):
    """Namen kommen mit HTML-Entities (&amp;) – für die Anzeige dekodieren."""
    return html.unescape(value or "")


def open_external(target):
    subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def cmid_from_url(url):
    m = re.search(r"[?&]id=(\d+)", url or "")
    return int(m.group(1)) if m else None


def module_timestamp(module):
    info = module.get("contentsinfo") or {}
    if info.get("lastmodified"):
        return info["lastmodified"]
    return max((c.get("timemodified") or 0 for c in module.get("contents", [])), default=0)


class Seen:
    """Merkt sich pro Kurs, welchen Stand jedes Modul beim letzten Ansehen hatte."""

    def __init__(self):
        self.path = STATE_DIR / "seen.json"
        try:
            self.data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            self.data = {}

    def save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data))

    def new_modules(self, courseid, sections):
        """Gibt die cmids mit neuem Inhalt zurück; beim ersten Mal gilt alles als gesehen."""
        modules = [m for s in sections for m in s.get("modules", [])]
        key = str(courseid)
        if key not in self.data:
            self.data[key] = {str(m["id"]): module_timestamp(m) for m in modules}
            self.save()
            return set()
        known = self.data[key]
        return {m["id"] for m in modules
                if str(m["id"]) not in known or module_timestamp(m) > known[str(m["id"])]}

    def mark(self, courseid, module):
        self.data.setdefault(str(courseid), {})[str(module["id"])] = module_timestamp(module)
        self.save()


# --------------------------------------------------------------------------- Übersicht


class HomeScreen(ui.Screen):
    bindings = [
        ("r", "Neu laden", "refresh"),
        ("o", "Im Browser", "open_browser"),
        ("c", "Claude fragen", "chat"),
        ("q", "Beenden", "quit"),
    ]
    TABS = ("Fristen", "Mitteilungen")

    def mount(self):
        self.courses, self.events, self.notes = [], [], []
        self.course_list = ui.ListView(on_select=self.open_course)
        self.tables = [ui.ListView(lambda i: self.show_row(), self.open_deadline, ["Fällig", "Kurs", "Aufgabe"], False),
                       ui.ListView(lambda i: self.show_row(), None, ["", "Datum", "Betreff"], False)]
        self.detail = ui.TextView()
        self.tab, self.unread, self.tab_hits = 0, 0, []
        self.focus = self.course_list
        self.refresh()

    @property
    def table(self):
        return self.tables[self.tab]

    def focusables(self):
        return [self.course_list, self.table, self.detail]

    def draw(self, win, h, w):
        lw = min(int(w * 0.38), 60)
        ui.put(win, 0, 1, "Kurse", ui.STYLE["h"])
        self.course_list.draw(win, 1, 0, h - 1, lw, self.focus is self.course_list)
        ui.vline(win, 0, lw, h)
        rx, rw = lw + 1, w - lw - 1
        self.tab_hits, x = [], rx + 1
        for i, name in enumerate(self.TABS):
            if i == 1 and self.unread:
                name += f" ({self.unread})"
            a = ui.STYLE["h"] | ui.curses.A_UNDERLINE if i == self.tab else ui.STYLE["d"]
            x0 = x
            x += ui.put(win, 0, x, f" {name} ", a, rw)
            self.tab_hits.append((x0, x, i))
            x += 1
        th = max(int(h * 0.45), 4)
        self.table.draw(win, 1, rx, th - 1, rw, self.focus is self.table)
        ui.hline(win, th, rx, rw)
        self.detail.draw(win, th + 1, rx + 1, h - th - 1, rw - 2, self.focus is self.detail)

    def click(self, y, x, button):
        if y == 0 and button == "click":
            for x0, x1, i in self.tab_hits:
                if x0 <= x < x1:
                    self.switch_tab(i)
            return
        super().click(y, x, button)

    def key(self, k):
        if self.focus is self.table and k in ("left", "right"):
            self.switch_tab(1 - self.tab)
        else:
            super().key(k)

    def switch_tab(self, i):
        if self.focus is self.table:
            self.focus = self.tables[i]
        self.tab = i
        self.show_row()

    def refresh(self):
        self.app.thread(self.load_overview)

    def load_overview(self):
        api = self.app.api
        try:
            courses = api.courses()
            self.app.post(self.show_courses, courses, {})
            events = api.deadlines()
            self.app.post(self.show_deadlines, events)
            notes = api.notifications()
            self.app.post(self.show_notifications, notes)
            contents = {}

            def fetch(c):
                try:
                    contents[c["id"]] = api.course_contents(c["id"])
                except MoodleError as e:
                    contents[c["id"]] = e

            threads = [threading.Thread(target=fetch, args=(c,)) for c in courses]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            for value in contents.values():
                if isinstance(value, MoodleError):
                    raise value
            new_counts = {cid: len(self.app.seen.new_modules(cid, secs)) for cid, secs in contents.items()}
            self.app.post(self.show_courses, courses, new_counts)
        except MoodleError as e:
            self.app.post(self.app.fail, e)

    def show_courses(self, courses, new_counts):
        self.courses = courses
        rows = []
        for c in courses:
            n = new_counts.get(c["id"], 0)
            rows.append([[(txt(c["shortname"]), "")] + ([(f"  {NEW} {n}", "n")] if n else [])])
        self.course_list.set_rows(rows)

    def show_deadlines(self, events):
        self.events = events
        rows = []
        for ev in events:
            due = (fmt_time(ev["timesort"], relative=True), "e" if ev["timesort"] - time.time() < 2 * 86400 else "")
            course = txt(ev["course"]["shortname"]) if ev.get("course") else ""
            rows.append([[due], [(course, "")], [(txt(ev["name"]), "")]])
        self.tables[0].set_rows(rows)
        if not events and self.tab == 0:
            self.detail.set(Doc().text("Keine anstehenden Fristen.", "i").lines)
        elif self.tab == 0:
            self.show_row()

    def show_notifications(self, notes):
        self.notes = notes
        self.unread = sum(not n["read"] for n in notes)
        self.tables[1].set_rows([[[("" if n["read"] else NEW, "n")], [(fmt_time(n["timecreated"]), "")],
                                  [(txt(n["subject"]), "")]] for n in notes])
        if self.tab == 1:
            self.show_row()

    def show_row(self):
        """Zeigt die markierte Zeile der sichtbaren Tabelle im Detailbereich."""
        row = self.table.cursor
        doc = Doc()
        if self.tab == 0 and row < len(self.events):
            ev = self.events[row]
            course = ev.get("course", {}).get("fullname", "")
            doc.heading(ev["name"]).gap()
            doc.line((course, "b"), (f" · fällig {fmt_time(ev['timesort'], relative=True)}", ""))
            doc.html(ev.get("description"))
            doc.para("Enter: im Kurs zeigen · o: im Browser", HINT)
        elif self.tab == 1 and row < len(self.notes):
            n = self.notes[row]
            doc.heading(n["subject"]).para(fmt_time(n["timecreated"]))
            if n.get("fullmessagehtml"):
                doc.html(n["fullmessagehtml"])
            else:
                doc.para(n.get("fullmessage") or n.get("smallmessage", ""))
            if not n["read"]:
                n["read"] = True
                self.unread -= 1
                self.tables[1].rows[row][0] = [("", "")]
                self.app.thread(self.mark_read, n["id"])
        else:
            return
        self.detail.set(doc.lines)

    def mark_read(self, notification_id):
        try:
            self.app.api.mark_notification_read(notification_id)
        except MoodleError:
            pass

    def open_deadline(self, i):
        if i < len(self.events):
            ev = self.events[i]
            course = next((c for c in self.courses if c["id"] == ev.get("course", {}).get("id")), None)
            if course:
                self.app.push(CourseScreen(course, focus_cmid=cmid_from_url(ev.get("url"))))

    def open_course(self, i):
        if i < len(self.courses):
            self.app.push(CourseScreen(self.courses[i]))

    def chat_context(self):
        parts = ["Übersicht (Kursliste, Fristen, Mitteilungen)."]
        if self.course_list.cursor < len(self.courses):
            c = self.courses[self.course_list.cursor]
            parts.append(f"Markierter Kurs: {txt(c['shortname'])} (Kurs-ID {c['id']}).")
        items = self.events if self.tab == 0 else self.notes
        if self.table.cursor < len(items):
            item = items[self.table.cursor]
            parts.append(f"Markierte {'Frist' if self.tab == 0 else 'Mitteilung'}: "
                         f"{item.get('name') or item.get('subject')}.")
        return " ".join(parts)

    def open_browser(self):
        if self.focus is self.course_list:
            if self.course_list.cursor < len(self.courses):
                open_external(self.courses[self.course_list.cursor]["viewurl"])
            return
        items = self.events if self.tab == 0 else self.notes
        if self.table.cursor < len(items):
            item = items[self.table.cursor]
            url = item.get("url") or item.get("contexturl")
            if url:
                open_external(url)


# --------------------------------------------------------------------------- Kursansicht


class CourseScreen(ui.Screen):
    bindings = [
        ("escape,backspace", "Zurück", "back"),
        ("o", "Extern öffnen", "open_browser"),
        ("c", "Claude fragen", "chat"),
        ("a", "Alles gesehen", "mark_all"),
        ("r", "Neu laden", "refresh"),
        ("q", "Beenden", "quit"),
    ]
    ICONS = {"resource": "📄", "folder": "📁", "forum": "💬", "assign": "📝", "url": "🔗", "page": "📃",
             "quiz": "❓", "label": "ℹ", "subsection": "📂", "opencast": "🎬", "lti": "🎬"}

    def __init__(self, course, focus_cmid=None):
        self.token = 0  # zählt Anzeigen hoch; späte Ergebnisse für eine alte Anzeige werden verworfen
        self.course = course
        self.focus_cmid = focus_cmid
        self.sections = []
        self.assignments = {}
        self.new = set()

    def mount(self):
        self.tree = ui.Tree(self.highlighted, self.selected)
        self.detail = ui.TextView()
        self.detail.set(Doc().text("Lade Kurs …", "i").lines)
        self.focus = self.tree
        self.refresh()

    def focusables(self):
        return [self.tree, self.detail]

    def draw(self, win, h, w):
        tw = int(w * 0.45)
        self.tree.draw(win, 0, 0, h, tw, self.focus is self.tree)
        ui.vline(win, 0, tw, h)
        self.detail.draw(win, 0, tw + 2, h, w - tw - 3, self.focus is self.detail)

    def refresh(self):
        self.app.thread(self.load_course)

    def load_course(self):
        try:
            sections = self.app.api.course_contents(self.course["id"])
            assignments = self.app.api.assignments(self.course["id"])
        except MoodleError as e:
            self.app.post(self.app.fail, e)
            return
        self.app.post(self.build_tree, sections, assignments)

    def build_tree(self, sections, assignments):
        self.sections, self.assignments = sections, assignments
        self.new = self.app.seen.new_modules(self.course["id"], sections)
        self.subsections = {s["itemid"]: s for s in sections if s.get("component") == "mod_subsection"}
        self.focus_node = None
        tree = self.tree
        tree.clear()
        for sec in sections:
            if sec.get("component") == "mod_subsection":
                continue
            modules = [m for m in sec.get("modules", []) if m.get("uservisible", True)]
            if not modules and not sec.get("summary"):
                continue
            has_new = any(m["id"] in self.new for m in self.all_modules(modules))
            label = [(txt(sec.get("name") or "Allgemein"), "")] + ([(f" {NEW}", "n")] if has_new else [])
            sec_node = tree.root.add(label, {"kind": "section", "section": sec}, expand=has_new or sec is sections[0])
            for m in modules:
                self.add_module(sec_node, m)
        tree.rebuild()
        doc = Doc().heading(txt(self.course["shortname"]))
        if self.new:
            doc.gap()
            doc.line((f"{len(self.new)} neue/geänderte Einträge", "b"), (f" ({NEW})", "n"))
        doc.para("Enter: anzeigen · o: extern öffnen (Browser/Standardprogramm) · a: alles als gesehen markieren · Esc: zurück", HINT)
        self.detail.set(doc.lines)
        if self.focus_node:
            tree.move_cursor(self.focus_node)

    def all_modules(self, modules):
        """Module eines Abschnitts inklusive der in Unterabschnitten."""
        for m in modules:
            yield m
            if m["modname"] == "subsection" and m["instance"] in self.subsections:
                yield from self.all_modules(self.subsections[m["instance"]].get("modules", []))

    def add_module(self, parent, m):
        node = self._add_module(parent, m)
        if m["id"] == self.focus_cmid:
            self.focus_node = node
        return node

    def _add_module(self, parent, m):
        name = m.get("name") or m["modname"]
        if m["modname"] == "label":
            text = Doc().html(m.get("description")).plain()
            lines = [l for l in text.splitlines() if l.strip()]
            name = lines[0].strip()[:80] if lines else "Hinweis"
        label = [(f"{self.ICONS.get(m['modname'], '•')} {txt(name)}", "")]
        if m["id"] in self.new:
            label.append((f" {NEW}", "n"))
        data = {"kind": "module", "module": m}
        files = [c for c in m.get("contents", []) if c.get("type") == "file"]
        if m["modname"] == "folder" or (m["modname"] == "resource" and len(files) > 1):
            node = parent.add(label, data)
            for f in files:
                path = (f.get("filepath") or "/").strip("/")
                fname = f"{path}/{f['filename']}" if path else f["filename"]
                node.add([(fname, ""), (f"  {fmt_size(f.get('filesize', 0))}", "d")],
                         {"kind": "file", "file": f, "module": m})
            return node
        if m["modname"] == "subsection":
            sub = self.subsections.get(m["instance"], {})
            children = [c for c in sub.get("modules", []) if c.get("uservisible", True)]
            if any(c["id"] in self.new for c in self.all_modules(children)):
                label.append((f" {NEW}", "n"))
            node = parent.add(label, {"kind": "section", "section": sub or {"name": name}})
            for c in children:
                self.add_module(node, c)
            return node
        if m["modname"] == "forum":
            node = parent.add(label, data)
            node.add([("Enter zum Laden …", "d")], {"kind": "placeholder"})
            return node
        return parent.add(label, data)

    # ----- Anzeige

    def show(self, doc, token=None, keep=False):
        """Zeigt doc an. Mit token nur, wenn seitdem nichts anderes angezeigt wurde; gibt das Token zurück."""
        if token is None:
            self.token += 1
        elif token != self.token:
            return None
        self.detail.set(doc.lines, keep=keep)
        return self.token

    def load(self, title, fn, *args):
        """Zeigt „Lade …“ und führt fn(token, *args) im Hintergrund aus."""
        token = self.show(Doc().heading(title).para("Lade …", HINT))
        self.app.thread(fn, token, *args)

    def module_doc(self, m):
        doc = Doc().heading(txt(m.get("name", "")))
        if m.get("dates"):
            doc.para(" · ".join(f"{d['label']} {fmt_time(d['timestamp'])}" for d in m["dates"]))
        return doc.html(m.get("description"))

    def highlighted(self, node):
        data = node.data
        kind = data.get("kind")
        if kind == "section":
            sec = data["section"]
            self.show(Doc().heading(txt(sec.get("name") or "Allgemein")).html(sec.get("summary")))
        elif kind == "module":
            m = data["module"]
            doc = self.module_doc(m)
            files = [c for c in m.get("contents", []) if c.get("type") == "file"]
            if m["modname"] == "resource" and files:
                f = files[0]
                doc.gap()
                doc.line((f["filename"], "c"), (f" · {fmt_size(f.get('filesize', 0))} · {fmt_time(f.get('timemodified'))}", ""))
                doc.para("Enter: anzeigen · o: mit Standardprogramm öffnen", HINT)
            elif m["modname"] == "url" and m.get("contents"):
                url = m["contents"][0]["fileurl"]
                doc.gap()
                doc.line((url, "", url))
                doc.para("o oder Klick auf den Link: im Browser öffnen", HINT)
            elif m["modname"] in ("page", "assign", "forum"):
                doc.para("Enter: Inhalt laden", HINT)
            elif m["modname"] not in ("label", "folder", "resource"):
                doc.para("o: im Browser öffnen", HINT)
            self.show(doc)
        elif kind == "file":
            f = data["file"]
            self.show(Doc().heading(f["filename"])
                      .para(f"{fmt_size(f.get('filesize', 0))} · {fmt_time(f.get('timemodified'))}")
                      .para("Enter: anzeigen · o: mit Standardprogramm öffnen", HINT))

    def selected(self, node):
        data = node.data
        kind = data.get("kind")
        if kind in ("module", "file") and data["module"]["id"] in self.new:
            self.mark_seen(data["module"], node if kind == "module" else node.parent)
        if kind == "file":
            self.load(data["file"]["filename"], self.load_file, data["file"])
        elif kind == "discussion":
            self.load(txt(data["discussion"]["name"]), self.load_discussion, data["discussion"])
        elif kind == "module":
            m = data["module"]
            files = [c for c in m.get("contents", []) if c.get("type") == "file"]
            if m["modname"] == "resource" and len(files) == 1:
                self.load(files[0]["filename"], self.load_file, files[0])
            elif m["modname"] == "page":
                self.load(txt(m["name"]), self.load_page, m)
            elif m["modname"] == "assign":
                self.load(txt(m["name"]), self.load_assignment, m)
            elif m["modname"] == "forum" and not node.loaded:
                node.loaded = True
                self.app.thread(self.load_forum, node, m)

    def mark_seen(self, m, node):
        self.new.discard(m["id"])
        self.app.seen.mark(self.course["id"], m)
        node.label = [r for r in node.label if r[0] != f" {NEW}"]
        self.tree.rebuild()

    def chat_context(self):
        text = f"Kurs {txt(self.course['shortname'])} (Kurs-ID {self.course['id']})."
        node = self.tree.cursor_node
        data = node.data if node else {}
        if data.get("kind") == "section":
            text += f" Markierter Abschnitt: {txt(data['section'].get('name') or 'Allgemein')}."
        elif data.get("kind") in ("module", "file", "discussion"):
            m = data["module"]
            text += f" Markiert: {m['modname']} „{txt(m.get('name'))}“ (cmid {m['id']})."
            if data["kind"] == "file":
                text += f" Datei: {data['file']['filename']}."
            elif data["kind"] == "discussion":
                text += f" Diskussion: {data['discussion']['name']}."
        return text

    def mark_all(self):
        for sec in self.sections:
            for m in sec.get("modules", []):
                self.app.seen.mark(self.course["id"], m)
        self.new.clear()
        self.build_tree(self.sections, self.assignments)

    def open_browser(self):
        """o: Dateien mit dem Standardprogramm, alles andere im Browser öffnen."""
        node = self.tree.cursor_node
        data = node.data if node else {}
        files = [c for c in data.get("module", {}).get("contents", []) if c.get("type") == "file"]
        if data.get("kind") == "file":
            self.app.thread(self.open_file, data["file"])
        elif data.get("kind") == "module" and data["module"]["modname"] == "resource" and len(files) == 1:
            self.app.thread(self.open_file, files[0])
        elif "discussion" in data:
            open_external(f"{SITE}/mod/forum/discuss.php?d={data['discussion']['discussion']}")
        elif "module" in data and data["module"].get("url"):
            open_external(data["module"]["url"])
        else:
            open_external(f"{SITE}/course/view.php?id={self.course['id']}")

    # ----- Laden im Hintergrund (läuft in eigenem Thread)

    def fetch_file(self, f):
        path = (f.get("filepath") or "/").strip("/")
        target = CACHE_DIR / "files" / str(self.course["id"]) / path / f["filename"]
        return self.app.api.download(f["fileurl"], target, f.get("timemodified") or 0)

    def open_file(self, f):
        self.app.post(self.app.notify, f"Öffne {f['filename']} …", False, 3)
        try:
            target = self.fetch_file(f)
        except Exception as e:  # Netzfehler, HTTP-Fehler
            self.app.post(self.app.notify, f"Download fehlgeschlagen: {e}", True)
            return
        open_external(target)

    def load_file(self, token, f):
        """Zeigt eine Datei im Detailbereich: Bilder und PDF-Seiten als Bild (kitty), sonst Text."""
        doc = Doc().heading(f["filename"]).para(f"{fmt_size(f.get('filesize', 0))} · {fmt_time(f.get('timemodified'))}")
        try:
            target = self.fetch_file(f)
        except Exception as e:
            self.app.post(self.show, doc.para(f"Download fehlgeschlagen: {e}", "e"), token)
            return
        suffix = target.suffix.lower()
        inline = self.app.images.inline
        if suffix in IMAGE_TYPES:
            doc.gap().line((f["filename"], "I", str(target)))
        elif suffix == ".pdf" and inline and shutil.which("pdftoppm"):
            self.load_pdf_pages(token, doc, target)
            return
        elif suffix == ".pdf" and shutil.which("pdftotext"):
            text = subprocess.run(["pdftotext", "-layout", str(target), "-"], capture_output=True, text=True).stdout
            doc.gap().text(text.replace("\f", "\n").rstrip() or "(kein Text in der PDF – o: mit Standardprogramm öffnen)")
        elif suffix in (".html", ".htm"):
            doc.html(target.read_text(errors="replace"), f["fileurl"])
        elif suffix in TEXT_TYPES:
            doc.gap().text(target.read_text(errors="replace")[:200_000], "c")
        else:
            doc.para("Keine Vorschau für diesen Dateityp.", "i")
        doc.para("o: mit Standardprogramm öffnen", HINT)
        self.app.post(self.show, doc, token)

    def load_pdf_pages(self, token, doc, target):
        """Rendert die PDF stapelweise zu PNG-Seiten (im Cache) und ergänzt die Anzeige nach jedem Stapel."""
        info = subprocess.run(["pdfinfo", str(target)], capture_output=True, text=True).stdout
        pages = int(m.group(1)) if (m := re.search(r"^Pages:\s+(\d+)", info, re.M)) else 1
        outdir = target.parent / f".{target.name}.seiten"
        outdir.mkdir(exist_ok=True)
        doc.para(f"{pages} Seiten · o: mit Standardprogramm öffnen", HINT)
        for first in range(1, pages + 1, PDF_BATCH):
            last = min(first + PDF_BATCH - 1, pages)
            if not all((outdir / f"s-{n:04d}.png").exists() for n in range(first, last + 1)):
                if token != self.token:  # inzwischen etwas anderes angezeigt: nicht weiter rendern
                    return
                subprocess.run(["pdftoppm", "-png", "-scale-to-x", "1200", "-scale-to-y", "-1", "-f", str(first),
                                "-l", str(last), str(target), str(outdir / "tmp")], capture_output=True)
                for png in outdir.glob("tmp-*.png"):  # pdftoppm nummeriert je nach Seitenzahl unterschiedlich breit
                    png.rename(outdir / f"s-{int(png.stem.rsplit('-', 1)[1]):04d}.png")
            for n in range(first, last + 1):
                doc.gap().line((f"Seite {n}", "I", str(outdir / f"s-{n:04d}.png")))
            self.app.post(self.show, doc, token, True)

    def load_page(self, token, m):
        index = next((c for c in m.get("contents", []) if c.get("filename") == "index.html"), None)
        if not index:
            self.app.post(self.show, self.module_doc(m), token)
            return
        doc = Doc().heading(txt(m["name"]))
        try:
            doc.lines += [[]] + html_lines(self.app.api.fetch_text(index["fileurl"]), index["fileurl"])
        except Exception as e:
            doc.para(f"Fehler beim Laden: {e}", "e")
        self.app.post(self.show, doc, token)

    def load_assignment(self, token, m):
        a = self.assignments.get(m["id"])
        if not a:
            return
        doc = Doc().heading(txt(a["name"])).gap()
        if a.get("allowsubmissionsfromdate"):
            doc.text(f"Abgabe ab: {fmt_time(a['allowsubmissionsfromdate'])}")
        if a.get("duedate"):
            doc.text(f"Fällig: {fmt_time(a['duedate'], relative=True)}", "b")
        if a.get("cutoffdate"):
            doc.text(f"Letzte Frist: {fmt_time(a['cutoffdate'])}")
        try:
            status = self.app.api.submission_status(a["id"])
            sub = (status.get("lastattempt") or {}).get("submission") or {}
            state = {"submitted": "✅ abgegeben", "draft": "✏ Entwurf", "new": "❌ nicht abgegeben"}.get(
                sub.get("status"), sub.get("status", "–"))
            doc.text(f"Status: {state}")
            grade = (status.get("feedback") or {}).get("gradefordisplay")
            if grade:
                doc.text("Bewertung: " + Doc().html(grade).plain())
        except MoodleError:
            pass
        doc.html(a.get("intro"))
        files = a.get("introattachments") or []
        if files:
            doc.para("Anhänge: " + ", ".join(f["filename"] for f in files))
        doc.para("Abgeben geht über den Browser (o).", HINT)
        self.app.post(self.show, doc, token)

    def load_forum(self, node, m):
        try:
            discussions = self.app.api.forum_discussions(m["instance"])
        except MoodleError as e:
            self.app.post(self.app.notify, str(e), True)
            return

        def fill():
            node.remove_children()
            for d in discussions:
                replies = f" ({d['numreplies']})" if d.get("numreplies") else ""
                node.add([(txt(d["name"]) + replies, ""), (f"  {fmt_time(d['timemodified'])}", "d")],
                         {"kind": "discussion", "discussion": d, "module": m})
            if not discussions:
                node.add([("keine Beiträge", "d")])
            node.expanded = True
            self.tree.rebuild()

        self.app.post(fill)

    def load_discussion(self, token, d):
        try:
            posts = self.app.api.discussion_posts(d["discussion"])
        except MoodleError as e:
            self.app.post(self.show, Doc().para(f"Fehler: {e}", "e"), token)
            return
        doc = Doc().heading(txt(d["name"]))
        for p in posts:
            author = (p.get("author") or {}).get("fullname", "")
            doc.rule()
            doc.line((author, "b"), (f" · {fmt_time(p.get('timecreated'))}", "d"))
            doc.html(p.get("message"))
        self.app.post(self.show, doc, token)


# --------------------------------------------------------------------------- App


class MoodleApp(ChatMixin, ui.App):
    def __init__(self):
        super().__init__()
        self.api = Moodle()
        self.seen = Seen()
        self.error = None
        self.init_chat()
        self.images = ui.TextView.images = ui.Images(self, self.api.image)
        ui.TextView.on_link = self.open_link

    def on_start(self):
        self.images.inline = ui.KITTY and ui.curses.COLORS >= 256
        self.push(HomeScreen())

    def open_link(self, url, image):
        """Klick auf einen Link öffnet den Browser; Bilder (nur ohne kitty anklickbar) den Bildbetrachter."""
        if image:
            path = url if url.startswith("/") else self.images.files.get(url)
            if path and path != "loading":
                open_external(path)
            return
        open_external(url)

    def quit(self):
        self.exit()

    def back(self):
        self.pop()

    def fail(self, e):
        if isinstance(e, NotLoggedIn):
            self.error = e
            self.exit()
        else:
            self.notify(str(e), True)
