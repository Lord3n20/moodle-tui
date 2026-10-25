"""Textual-Oberfläche: Übersicht (Kurse, Fristen, Mitteilungen) und Kursansicht."""

import html
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Label, ListItem, ListView, Markdown, TabbedContent, TabPane, Tree

from .api import CACHE_DIR, STATE_DIR, Moodle, MoodleError, NotLoggedIn
from .chat import ChatMixin
from .util import fmt_size, fmt_time, html_to_md

NEW = "●"


def txt(value):
    """Namen kommen mit HTML-Entities (&amp;) – für die Anzeige dekodieren und escapen."""
    return escape(html.unescape(value or ""))


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


class HomeScreen(Screen):
    BINDINGS = [
        Binding("r", "refresh", "Neu laden"),
        Binding("o", "open_browser", "Im Browser"),
        Binding("c", "app.chat", "Claude fragen"),
        Binding("tab", "app.focus_next", "Bereich wechseln", show=False),
        Binding("q", "app.quit", "Beenden"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="left"):
                yield Label("Kurse", classes="title")
                yield ListView(id="courses")
            with Vertical(id="right"):
                with TabbedContent(id="tabs"):
                    with TabPane("Fristen", id="tab-deadlines"):
                        yield DataTable(id="deadlines", cursor_type="row", zebra_stripes=True)
                    with TabPane("Mitteilungen", id="tab-notifications"):
                        yield DataTable(id="notifications", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(id="detail-box"):
                    yield Markdown(id="detail")
        yield Footer()

    def on_mount(self):
        self.courses, self.events, self.notes = [], [], []
        self.query_one("#deadlines", DataTable).add_columns("Fällig", "Kurs", "Aufgabe")
        self.query_one("#notifications", DataTable).add_columns("", "Datum", "Betreff")
        self.query_one("#courses", ListView).focus()
        self.action_refresh()

    def action_refresh(self):
        self.load_overview()

    @work(thread=True, exclusive=True, group="overview")
    def load_overview(self):
        api = self.app.api
        try:
            courses = api.courses()
            self.app.call_from_thread(self.show_courses, courses, {})
            events = api.deadlines()
            self.app.call_from_thread(self.show_deadlines, events)
            notes = api.notifications()
            self.app.call_from_thread(self.show_notifications, notes)
            with ThreadPoolExecutor(4) as pool:
                contents = dict(zip([c["id"] for c in courses], pool.map(lambda c: api.course_contents(c["id"]), courses)))
            new_counts = {cid: len(self.app.seen.new_modules(cid, secs)) for cid, secs in contents.items()}
            self.app.call_from_thread(self.show_courses, courses, new_counts)
        except MoodleError as e:
            self.app.call_from_thread(self.app.fail, e)

    def show_courses(self, courses, new_counts):
        lv = self.query_one("#courses", ListView)
        index = lv.index or 0
        self.courses = courses
        lv.clear()
        for c in courses:
            n = new_counts.get(c["id"], 0)
            label = txt(c["shortname"]) + (f"  [b yellow]{NEW} {n}[/]" if n else "")
            lv.append(ListItem(Label(Text.from_markup(label))))
        if courses:
            lv.index = min(index, len(courses) - 1)

    def show_deadlines(self, events):
        self.events = events
        table = self.query_one("#deadlines", DataTable)
        table.clear()
        for ev in events:
            due = fmt_time(ev["timesort"], relative=True)
            if ev["timesort"] - time.time() < 2 * 86400:
                due = f"[b red]{due}[/]"
            table.add_row(due, txt(ev["course"]["shortname"]) if ev.get("course") else "", txt(ev["name"]))
        if not events:
            self.query_one("#detail", Markdown).update("_Keine anstehenden Fristen._")

    def show_notifications(self, notes):
        self.notes = notes
        table = self.query_one("#notifications", DataTable)
        table.clear()
        unread = 0
        for n in notes:
            unread += not n["read"]
            table.add_row("" if n["read"] else NEW, fmt_time(n["timecreated"]), txt(n["subject"]))
        tab = self.query_one("#tabs", TabbedContent).get_tab("tab-notifications")
        tab.label = f"Mitteilungen ({unread})" if unread else "Mitteilungen"

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        self.show_row(event.data_table)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated):
        self.show_row(self.query_one("#deadlines" if event.pane.id == "tab-deadlines" else "#notifications", DataTable))

    def show_row(self, table):
        """Zeigt die markierte Zeile der gerade sichtbaren Tabelle im Detailbereich."""
        if self.query_one("#tabs", TabbedContent).active != f"tab-{table.id}":
            return
        md = self.query_one("#detail", Markdown)
        row = table.cursor_row
        if table.id == "deadlines" and row < len(self.events):
            ev = self.events[row]
            course = ev.get("course", {}).get("fullname", "")
            md.update(f"## {ev['name']}\n\n**{course}** · fällig {fmt_time(ev['timesort'], relative=True)}\n\n"
                      f"{html_to_md(ev.get('description'))}\n\n_Enter: im Kurs zeigen · o: im Browser_")
        elif table.id == "notifications" and row < len(self.notes):
            n = self.notes[row]
            body = html_to_md(n.get("fullmessagehtml")) or n.get("fullmessage") or n.get("smallmessage", "")
            md.update(f"## {n['subject']}\n\n{fmt_time(n['timecreated'])}\n\n{body}")
            if not n["read"]:
                n["read"] = True
                table.update_cell_at((row, 0), "")
                self.mark_read(n["id"])

    @work(thread=True)
    def mark_read(self, notification_id):
        try:
            self.app.api.mark_notification_read(notification_id)
        except MoodleError:
            pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if event.data_table.id == "deadlines" and event.cursor_row < len(self.events):
            ev = self.events[event.cursor_row]
            course = next((c for c in self.courses if c["id"] == ev.get("course", {}).get("id")), None)
            if course:
                self.app.push_screen(CourseScreen(course, focus_cmid=cmid_from_url(ev.get("url"))))

    def on_list_view_selected(self, event: ListView.Selected):
        if event.list_view.index is not None and event.list_view.index < len(self.courses):
            self.app.push_screen(CourseScreen(self.courses[event.list_view.index]))

    def chat_context(self):
        parts = ["Übersicht (Kursliste, Fristen, Mitteilungen)."]
        lv = self.query_one("#courses", ListView)
        if lv.index is not None and lv.index < len(self.courses):
            c = self.courses[lv.index]
            parts.append(f"Markierter Kurs: {html.unescape(c['shortname'])} (Kurs-ID {c['id']}).")
        tabs = self.query_one("#tabs", TabbedContent)
        table = self.query_one("#deadlines" if tabs.active == "tab-deadlines" else "#notifications", DataTable)
        items = self.events if table.id == "deadlines" else self.notes
        if table.row_count and table.cursor_row < len(items):
            item = items[table.cursor_row]
            parts.append(f"Markierte {'Frist' if table.id == 'deadlines' else 'Mitteilung'}: "
                         f"{item.get('name') or item.get('subject')}.")
        return " ".join(parts)

    def action_open_browser(self):
        focused = self.focused
        if isinstance(focused, DataTable) and focused.cursor_row is not None:
            items = self.events if focused.id == "deadlines" else self.notes
            if focused.cursor_row < len(items):
                item = items[focused.cursor_row]
                url = item.get("url") or item.get("contexturl")
                if url:
                    open_external(url)
        elif isinstance(focused, ListView) and focused.index is not None and self.courses:
            open_external(self.courses[focused.index]["viewurl"])


# --------------------------------------------------------------------------- Kursansicht


class CourseScreen(Screen):
    BINDINGS = [
        Binding("escape,backspace", "app.pop_screen", "Zurück"),
        Binding("o", "open_browser", "Im Browser"),
        Binding("c", "app.chat", "Claude fragen"),
        Binding("a", "mark_all", "Alles gesehen"),
        Binding("r", "refresh", "Neu laden"),
        Binding("q", "app.quit", "Beenden"),
    ]

    def __init__(self, course, focus_cmid=None):
        super().__init__()
        self.course = course
        self.focus_cmid = focus_cmid
        self.sections = []
        self.assignments = {}
        self.new = set()

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Tree(txt(self.course["shortname"]), id="tree")
            with VerticalScroll(id="detail-box"):
                yield Markdown("_Lade Kurs …_", id="detail")
        yield Footer()

    def on_mount(self):
        tree = self.query_one("#tree", Tree)
        tree.show_root = False
        tree.focus()
        self.action_refresh()

    def action_refresh(self):
        self.load_course()

    @work(thread=True, exclusive=True, group="course")
    def load_course(self):
        try:
            sections = self.app.api.course_contents(self.course["id"])
            assignments = self.app.api.assignments(self.course["id"])
        except MoodleError as e:
            self.app.call_from_thread(self.app.fail, e)
            return
        self.app.call_from_thread(self.build_tree, sections, assignments)

    def build_tree(self, sections, assignments):
        self.sections, self.assignments = sections, assignments
        self.new = self.app.seen.new_modules(self.course["id"], sections)
        self.subsections = {s["itemid"]: s for s in sections if s.get("component") == "mod_subsection"}
        self.focus_node = None
        tree = self.query_one("#tree", Tree)
        tree.clear()
        for sec in sections:
            if sec.get("component") == "mod_subsection":
                continue
            modules = [m for m in sec.get("modules", []) if m.get("uservisible", True)]
            if not modules and not sec.get("summary"):
                continue
            has_new = any(m["id"] in self.new for m in self.all_modules(modules))
            sec_label = txt(sec.get("name") or "Allgemein") + (f" [yellow]{NEW}[/]" if has_new else "")
            sec_node = tree.root.add(sec_label, data={"kind": "section", "section": sec},
                                     expand=has_new or sec is sections[0])
            for m in modules:
                self.add_module(sec_node, m)
        focus_node = self.focus_node
        if focus_node:
            parent = focus_node.parent
            while parent:
                parent.expand()
                parent = parent.parent
            self.call_after_refresh(tree.move_cursor, focus_node)
        else:
            tree.cursor_line = 0
        self.query_one("#detail", Markdown).update(
            f"## {html.unescape(self.course['shortname'])}\n\n"
            + (f"**{len(self.new)} neue/geänderte Einträge** ({NEW})\n\n" if self.new else "")
            + "_Enter: öffnen · o: im Browser · a: alles als gesehen markieren · Esc: zurück_")

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
        icon = {"resource": "📄", "folder": "📁", "forum": "💬", "assign": "📝", "url": "🔗", "page": "📃",
                "quiz": "❓", "label": "ℹ", "subsection": "📂", "opencast": "🎬", "lti": "🎬"}.get(m["modname"], "•")
        name = m.get("name") or m["modname"]
        if m["modname"] == "label":
            lines = [l for l in html_to_md(m.get("description")).splitlines() if l.strip()]
            name = re.sub(r"[*_#>`]+", "", lines[0]).strip()[:80] if lines else "Hinweis"
        label = f"{icon} {txt(name)}" + (f" [yellow]{NEW}[/]" if m["id"] in self.new else "")
        data = {"kind": "module", "module": m}
        files = [c for c in m.get("contents", []) if c.get("type") == "file"]
        if m["modname"] == "folder" or (m["modname"] == "resource" and len(files) > 1):
            node = parent.add(label, data=data)
            for f in files:
                path = (f.get("filepath") or "/").strip("/")
                fname = f"{path}/{f['filename']}" if path else f["filename"]
                node.add_leaf(f"{escape(fname)}  [dim]{fmt_size(f.get('filesize', 0))}[/]",
                              data={"kind": "file", "file": f, "module": m})
            return node
        if m["modname"] == "subsection":
            sub = self.subsections.get(m["instance"], {})
            children = [c for c in sub.get("modules", []) if c.get("uservisible", True)]
            if any(c["id"] in self.new for c in self.all_modules(children)):
                label += f" [yellow]{NEW}[/]"
            node = parent.add(label, data={"kind": "section", "section": sub or {"name": name}})
            for c in children:
                self.add_module(node, c)
            return node
        if m["modname"] == "forum":
            node = parent.add(label, data=data)
            node.add_leaf("[dim]Enter zum Laden …[/]", data={"kind": "placeholder"})
            return node
        return parent.add_leaf(label, data=data)

    # ----- Anzeige

    def detail(self, text):
        self.query_one("#detail", Markdown).update(text)
        self.query_one("#detail-box", VerticalScroll).scroll_home(animate=False)

    def module_header(self, m):
        parts = [f"## {html.unescape(m.get('name', ''))}"]
        if m.get("dates"):
            parts.append(" · ".join(f"{d['label']} {fmt_time(d['timestamp'])}" for d in m["dates"]))
        desc = html_to_md(m.get("description"))
        if desc:
            parts.append(desc)
        return "\n\n".join(parts)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted):
        data = event.node.data or {}
        kind = data.get("kind")
        if kind == "section":
            sec = data["section"]
            self.detail(f"## {html.unescape(sec.get('name') or 'Allgemein')}\n\n{html_to_md(sec.get('summary'))}")
        elif kind == "module":
            m = data["module"]
            text = self.module_header(m)
            files = [c for c in m.get("contents", []) if c.get("type") == "file"]
            if m["modname"] == "resource" and files:
                f = files[0]
                text += f"\n\n`{f['filename']}` · {fmt_size(f.get('filesize', 0))} · {fmt_time(f.get('timemodified'))}"
                text += "\n\n_Enter: öffnen_"
            elif m["modname"] == "url" and m.get("contents"):
                text += f"\n\n<{m['contents'][0]['fileurl']}>\n\n_Enter: im Browser öffnen_"
            elif m["modname"] in ("page", "assign", "forum"):
                text += "\n\n_Enter: Inhalt laden_"
            elif m["modname"] not in ("label", "folder", "resource"):
                text += "\n\n_o: im Browser öffnen_"
            self.detail(text)
        elif kind == "file":
            f = data["file"]
            self.detail(f"## {f['filename']}\n\n{fmt_size(f.get('filesize', 0))} · {fmt_time(f.get('timemodified'))}"
                        "\n\n_Enter: öffnen_")

    def on_tree_node_selected(self, event: Tree.NodeSelected):
        node = event.node
        data = node.data or {}
        kind = data.get("kind")
        if kind in ("module", "file") and data["module"]["id"] in self.new:
            self.mark_seen(data["module"], node if kind == "module" else node.parent)
        if kind == "file":
            self.open_file(data["file"])
        elif kind == "discussion":
            self.load_discussion(data["discussion"])
        elif kind == "module":
            m = data["module"]
            files = [c for c in m.get("contents", []) if c.get("type") == "file"]
            if m["modname"] == "resource" and len(files) == 1:
                self.open_file(files[0])
            elif m["modname"] == "url" and m.get("contents"):
                open_external(m["contents"][0]["fileurl"])
            elif m["modname"] == "page":
                self.load_page(m)
            elif m["modname"] == "assign":
                self.load_assignment(m)
            elif m["modname"] == "forum" and not getattr(node, "_loaded", False):
                node._loaded = True
                self.load_forum(node, m)

    def mark_seen(self, m, node):
        self.new.discard(m["id"])
        self.app.seen.mark(self.course["id"], m)
        node.set_label(Text(node.label.plain.replace(f" {NEW}", "")))

    def chat_context(self):
        text = f"Kurs {html.unescape(self.course['shortname'])} (Kurs-ID {self.course['id']})."
        node = self.query_one("#tree", Tree).cursor_node
        data = (node.data or {}) if node else {}
        if data.get("kind") == "section":
            text += f" Markierter Abschnitt: {html.unescape(data['section'].get('name') or 'Allgemein')}."
        elif data.get("kind") in ("module", "file", "discussion"):
            m = data["module"]
            text += f" Markiert: {m['modname']} „{html.unescape(m.get('name') or '')}“ (cmid {m['id']})."
            if data["kind"] == "file":
                text += f" Datei: {data['file']['filename']}."
            elif data["kind"] == "discussion":
                text += f" Diskussion: {data['discussion']['name']}."
        return text

    def action_mark_all(self):
        for sec in self.sections:
            for m in sec.get("modules", []):
                self.app.seen.mark(self.course["id"], m)
        self.new.clear()
        self.build_tree(self.sections, self.assignments)

    def action_open_browser(self):
        node = self.query_one("#tree", Tree).cursor_node
        data = (node.data or {}) if node else {}
        if "discussion" in data:
            open_external(f"https://moodle.rwth-aachen.de/mod/forum/discuss.php?d={data['discussion']['discussion']}")
        elif "module" in data and data["module"].get("url"):
            open_external(data["module"]["url"])
        else:
            open_external(f"https://moodle.rwth-aachen.de/course/view.php?id={self.course['id']}")

    # ----- Laden im Hintergrund

    @work(thread=True)
    def open_file(self, f):
        path = (f.get("filepath") or "/").strip("/")
        target = CACHE_DIR / "files" / str(self.course["id"]) / path / f["filename"]
        self.app.call_from_thread(self.notify, f"Öffne {f['filename']} …", timeout=3)
        try:
            self.app.api.download(f["fileurl"], target, f.get("timemodified") or 0)
        except Exception as e:  # Netzfehler, HTTP-Fehler
            self.app.call_from_thread(self.notify, f"Download fehlgeschlagen: {e}", severity="error")
            return
        open_external(target)

    @work(thread=True, exclusive=True, group="detail")
    def load_page(self, m):
        index = next((c for c in m.get("contents", []) if c.get("filename") == "index.html"), None)
        if not index:
            return
        try:
            body = html_to_md(self.app.api.fetch_text(index["fileurl"]))
        except Exception as e:
            body = f"_Fehler beim Laden: {e}_"
        self.app.call_from_thread(self.detail, f"## {m['name']}\n\n{body}")

    @work(thread=True, exclusive=True, group="detail")
    def load_assignment(self, m):
        a = self.assignments.get(m["id"])
        if not a:
            return
        lines = [f"## {a['name']}"]
        if a.get("allowsubmissionsfromdate"):
            lines.append(f"Abgabe ab: {fmt_time(a['allowsubmissionsfromdate'])}")
        if a.get("duedate"):
            lines.append(f"**Fällig: {fmt_time(a['duedate'], relative=True)}**")
        if a.get("cutoffdate"):
            lines.append(f"Letzte Frist: {fmt_time(a['cutoffdate'])}")
        try:
            status = self.app.api.submission_status(a["id"])
            sub = (status.get("lastattempt") or {}).get("submission") or {}
            state = {"submitted": "✅ abgegeben", "draft": "✏ Entwurf", "new": "❌ nicht abgegeben"}.get(
                sub.get("status"), sub.get("status", "–"))
            lines.append(f"Status: {state}")
            grade = (status.get("feedback") or {}).get("gradefordisplay")
            if grade:
                lines.append(f"Bewertung: {html_to_md(grade)}")
        except MoodleError:
            pass
        lines.append(html_to_md(a.get("intro")))
        files = a.get("introattachments") or []
        if files:
            lines.append("Anhänge: " + ", ".join(f["filename"] for f in files))
        lines.append("_Abgeben geht über den Browser (o)._")
        self.app.call_from_thread(self.detail, "\n\n".join(lines))

    @work(thread=True)
    def load_forum(self, node, m):
        try:
            discussions = self.app.api.forum_discussions(m["instance"])
        except MoodleError as e:
            self.app.call_from_thread(self.notify, str(e), severity="error")
            return

        def fill():
            node.remove_children()
            for d in discussions:
                replies = f" ({d['numreplies']})" if d.get("numreplies") else ""
                node.add_leaf(f"{txt(d['name'])}{replies}  [dim]{fmt_time(d['timemodified'])}[/]",
                              data={"kind": "discussion", "discussion": d, "module": m})
            if not discussions:
                node.add_leaf("[dim]keine Beiträge[/]")
            node.expand()

        self.app.call_from_thread(fill)

    @work(thread=True, exclusive=True, group="detail")
    def load_discussion(self, d):
        try:
            posts = self.app.api.discussion_posts(d["discussion"])
        except MoodleError as e:
            self.app.call_from_thread(self.detail, f"_Fehler: {e}_")
            return
        parts = [f"## {d['name']}"]
        for p in posts:
            author = (p.get("author") or {}).get("fullname", "")
            parts.append(f"---\n\n**{author}** · {fmt_time(p.get('timecreated'))}\n\n{html_to_md(p.get('message'))}")
        self.app.call_from_thread(self.detail, "\n\n".join(parts))


# --------------------------------------------------------------------------- App


class MoodleApp(ChatMixin, App):
    TITLE = "RWTHmoodle"
    CSS = """
    #left { width: 38%; max-width: 60; border-right: solid $panel; }
    .title { padding: 0 1; text-style: bold; color: $accent; }
    #right { width: 1fr; }
    #tabs { height: 45%; }
    #detail-box { height: 1fr; border-top: solid $panel; }
    CourseScreen #tree { width: 45%; border-right: solid $panel; }
    CourseScreen #detail-box { border-top: none; }
    Markdown { margin: 0 1; }
    """

    def __init__(self):
        super().__init__()
        self.api = Moodle()
        self.seen = Seen()
        self.error = None
        self.init_chat()

    def on_mount(self):
        self.push_screen(HomeScreen())

    def fail(self, e):
        if isinstance(e, NotLoggedIn):
            self.error = e
            self.exit()
        else:
            self.notify(str(e), severity="error", timeout=6)
