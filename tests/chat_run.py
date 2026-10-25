"""Echter Chat-Test: Kurs öffnen, Frage stellen, auf Antwort warten, Screenshot."""
import asyncio, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, ".")
from moodle_tui import app as A
A.STATE_DIR = Path(tempfile.mkdtemp())
SP, QUESTION = sys.argv[1], sys.argv[2]

async def main():
    app = A.MoodleApp()
    async with app.run_test(size=(150, 45)) as pilot:
        await pilot.pause(5)
        await pilot.press("enter"); await pilot.pause(3)
        await pilot.press("c"); await pilot.pause(0.5)
        for ch in QUESTION:
            await pilot.press(ch if ch != " " else "space")
        await pilot.press("enter")
        t0 = time.time()
        await pilot.pause(2)
        while app.chat_busy and time.time() - t0 < 240:
            await pilot.pause(1)
        print(f"Dauer {time.time()-t0:.0f}s, Session {app.chat_session}")
        print(app.chat_history[-1]["text"])
        app.save_screenshot("chat.svg", path=SP)
asyncio.run(main())
