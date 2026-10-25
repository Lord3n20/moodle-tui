"""`moodle dump …`: Moodle-Inhalte als Text ausgeben – nur lesend, für den Chat mit Claude."""

import html
import sys

from .api import CACHE_DIR, Moodle
from .util import fmt_size, fmt_time, html_to_md

USAGE = """Aufruf:
  moodle dump courses                  Kurse mit IDs
  moodle dump course KURS-ID           Abschnitte, Module (cmid), Dateien, Fristen
  moodle dump deadlines                anstehende Fristen
  moodle dump notifications            Mitteilungen (werden NICHT als gelesen markiert)
  moodle dump forum KURS-ID CMID       Forum mit Beiträgen
  moodle dump page KURS-ID CMID        Text einer Seite
  moodle dump assign KURS-ID CMID      Aufgabe mit Status
  moodle dump file KURS-ID CMID        Dateien des Moduls laden, lokale Pfade ausgeben"""


def _module(api, courseid, cmid):
    for sec in api.course_contents(courseid):
        for m in sec.get("modules", []):
            if m["id"] == cmid:
                return m
    sys.exit(f"Modul {cmid} nicht in Kurs {courseid} gefunden.")


def courses(api):
    for c in api.courses():
        print(f"{c['id']}\t{html.unescape(c['shortname'])}")


def course(api, courseid):
    sections = api.course_contents(courseid)
    assignments = api.assignments(courseid)
    subsections = {s["itemid"]: s for s in sections if s.get("component") == "mod_subsection"}

    def modules(mods, indent):
        pad = "  " * indent
        for m in mods:
            if not m.get("uservisible", True):
                continue
            name = html.unescape(m.get("name") or "")
            line = f"{pad}- [cmid {m['id']}] {m['modname']}: {name}"
            if m["modname"] == "assign" and m["id"] in assignments:
                line += f" (fällig {fmt_time(assignments[m['id']].get('duedate'))})"
            for d in m.get("dates") or []:
                line += f" · {d['label']} {fmt_time(d['timestamp'])}"
            print(line)
            desc = html_to_md(m.get("description"))
            if desc:
                print(pad + "  " + desc[:600].replace("\n", "\n" + pad + "  "))
            for f in m.get("contents", []):
                if f.get("type") == "file":
                    print(f"{pad}  · Datei {f['filename']} ({fmt_size(f.get('filesize', 0))}, {fmt_time(f.get('timemodified'))})")
                elif f.get("type") == "url":
                    print(f"{pad}  · Link {f.get('fileurl')}")
            if m["modname"] == "subsection" and m["instance"] in subsections:
                modules(subsections[m["instance"]].get("modules", []), indent + 1)

    for sec in sections:
        if sec.get("component") == "mod_subsection":
            continue
        print(f"\n## {html.unescape(sec.get('name') or 'Allgemein')}")
        summary = html_to_md(sec.get("summary"))
        if summary:
            print(summary[:800])
        modules(sec.get("modules", []), 0)


def deadlines(api):
    events = api.deadlines()
    if not events:
        print("Keine anstehenden Fristen.")
    for ev in events:
        print(f"{fmt_time(ev['timesort'], relative=True)}\t{html.unescape(ev['course']['shortname'])}\t{ev['name']}")


def notifications(api):
    for n in api.notifications():
        body = html_to_md(n.get("fullmessagehtml")) or n.get("fullmessage") or n.get("smallmessage", "")
        print(f"### {n['subject']}\n{fmt_time(n['timecreated'])} · {'gelesen' if n['read'] else 'ungelesen'}\n{body}\n")


def forum(api, courseid, cmid):
    m = _module(api, courseid, cmid)
    for d in api.forum_discussions(m["instance"])[:15]:
        print(f"\n### {d['name']} ({fmt_time(d['timemodified'])})")
        for p in api.discussion_posts(d["discussion"]):
            author = (p.get("author") or {}).get("fullname", "")
            print(f"- {author}, {fmt_time(p.get('timecreated'))}:\n  {html_to_md(p.get('message'))[:2000]}")


def page(api, courseid, cmid):
    m = _module(api, courseid, cmid)
    index = next((c for c in m.get("contents", []) if c.get("filename") == "index.html"), None)
    print(html_to_md(api.fetch_text(index["fileurl"])) if index else "Keine Seite.")


def assign(api, courseid, cmid):
    a = api.assignments(courseid).get(cmid)
    if not a:
        sys.exit("Keine Aufgabe mit dieser cmid.")
    print(f"{a['name']}\nFällig: {fmt_time(a.get('duedate'), relative=True)}")
    sub = (api.submission_status(a["id"]).get("lastattempt") or {}).get("submission") or {}
    print(f"Status: {sub.get('status', '–')}\n\n{html_to_md(a.get('intro'))}")


def file(api, courseid, cmid):
    m = _module(api, courseid, cmid)
    files = [c for c in m.get("contents", []) if c.get("type") == "file"]
    if not files:
        sys.exit("Das Modul hat keine Dateien.")
    for f in files[:20]:
        path = (f.get("filepath") or "/").strip("/")
        target = CACHE_DIR / "files" / str(courseid) / path / f["filename"]
        print(api.download(f["fileurl"], target, f.get("timemodified") or 0))


COMMANDS = {"courses": (courses, 0), "course": (course, 1), "deadlines": (deadlines, 0),
            "notifications": (notifications, 0), "forum": (forum, 2), "page": (page, 2),
            "assign": (assign, 2), "file": (file, 2)}


def main(args):
    if not args or args[0] not in COMMANDS or len(args) - 1 != COMMANDS[args[0]][1]:
        print(USAGE)
        return 2
    fn, _ = COMMANDS[args[0]]
    try:
        fn(Moodle(), *map(int, args[1:]))
    except ValueError:
        print(USAGE)
        return 2
    return 0
