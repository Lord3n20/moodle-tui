"""Startet die TUI headless mit echtem Konto (ohne Gesehen-Stand zu verändern) und macht Screenshots."""
import asyncio, sys, tempfile
from pathlib import Path
sys.path.insert(0, ".")
from moodle_tui import app as A
from textual.widgets import Tree
A.STATE_DIR = Path(tempfile.mkdtemp())
SP = sys.argv[1]

async def main():
    app = A.MoodleApp()
    async with app.run_test(size=(150, 45)) as pilot:
        await pilot.pause(6)
        app.save_screenshot("real-home.svg", path=SP)
        await pilot.press("enter"); await pilot.pause(3)
        t = app.screen.query_one(Tree)
        for k in ["down"] * 9:
            await pilot.press(k)
        await pilot.press("enter"); await pilot.pause(1)
        await pilot.press("down"); await pilot.pause(1)
        app.save_screenshot("real-course.svg", path=SP)
        print("cursor:", t.cursor_node and t.cursor_node.label.plain)
asyncio.run(main())
