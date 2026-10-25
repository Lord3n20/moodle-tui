"""Startet die TUI headless mit Testdaten und macht Screenshots."""
import asyncio, sys, time
sys.path.insert(0, ".")
from moodle_tui import app as A

now = int(time.time())
class Fake:
    userid = 1
    def courses(self): return [{"id": 1, "fullname": "Analysis I [WiSe 26/27]", "shortname": "Ana", "viewurl": "x"},
                               {"id": 2, "fullname": "Programmierung", "shortname": "Progra", "viewurl": "y"}]
    def deadlines(self): return [{"name": "Übungsblatt 3", "timesort": now + 3600, "course": {"id": 1, "shortname": "Ana", "fullname": "Analysis I"}, "description": "<p>Abgabe <b>bis</b> Mitternacht</p>", "url": "https://m/mod/assign/view.php?id=11"},
                                 {"name": "Quiz 2", "timesort": now + 5*86400, "course": {"id": 2, "shortname": "Progra", "fullname": "Programmierung"}, "description": "", "url": ""}]
    def notifications(self): return [{"id": 5, "subject": "Neue Ankündigung [Ana]", "read": False, "timecreated": now - 3600, "fullmessagehtml": "<p>Hallo</p>"}]
    def mark_notification_read(self, i): pass
    def course_contents(self, cid):
        return [{"name": "Allgemeines", "summary": "<p>Willkommen</p>", "modules": [
                    {"id": 10, "name": "Ankündigungen", "modname": "forum", "instance": 3, "contents": []},
                    {"id": 11, "name": "Blatt 3", "modname": "assign", "instance": 4, "contents": [], "dates": [{"label": "Fällig:", "timestamp": now + 3600}]},
                    {"id": 12, "name": "Skript", "modname": "resource", "contents": [{"type": "file", "filename": "skript.pdf", "filesize": 2_000_000, "timemodified": now + cid, "fileurl": "u"}]},
                    {"id": 13, "modname": "label", "description": "<p>Klausur am 20.02.</p>", "contents": []}]},
                {"name": "Woche 1", "summary": "", "modules": [{"id": 14, "name": "Folien", "modname": "folder", "contents": [{"type": "file", "filename": "a.pdf", "filepath": "/", "filesize": 10, "timemodified": 1, "fileurl": "u"}]}]}]
    def assignments(self, cid): return {11: {"id": 4, "name": "Blatt 3", "duedate": now + 3600, "intro": "<p>Aufgaben</p>"}}
    def submission_status(self, a): return {"lastattempt": {"submission": {"status": "new"}}}
    def forum_discussions(self, f): return [{"name": "Klausurtermin", "discussion": 7, "numreplies": 2, "timemodified": now}]
    def discussion_posts(self, d): return [{"author": {"fullname": "Prof"}, "timecreated": now, "message": "<p>Am 20.02.</p>"}]

A.Moodle = lambda: Fake()
A.STATE_DIR = A.CACHE_DIR / "test-state"

async def main():
    app = A.MoodleApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(1)
        app.save_screenshot("home.svg", path="/tmp/claude-1000/-home-enzo/6fda36b0-e3b2-443b-afde-90e25f070e64/scratchpad")
        SP = "/tmp/claude-1000/-home-enzo/6fda36b0-e3b2-443b-afde-90e25f070e64/scratchpad"
        await pilot.press("enter"); await pilot.pause(1)
        app.save_screenshot("course.svg", path=SP)
        await pilot.press("down", "enter"); await pilot.pause(1)
        await pilot.press("down"); await pilot.pause(0.3)
        await pilot.press("enter"); await pilot.pause(1)
        app.save_screenshot("forum.svg", path=SP)
        await pilot.press("down", "down", "enter"); await pilot.pause(1)
        app.save_screenshot("assign.svg", path=SP)
        await pilot.press("escape", "tab", "enter"); await pilot.pause(1)
        app.save_screenshot("deadline.svg", path=SP)
    print("ok")
asyncio.run(main())
