import asyncio, sys
sys.path.insert(0, ".")
exec(open("tests/fake_run.py").read().split("async def main")[0])
from textual.widgets import Tree, Markdown
async def main():
    app = A.MoodleApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(1)
        await pilot.press("enter"); await pilot.pause(1)
        t = app.screen.query_one(Tree)
        def st(tag):
            n = t.cursor_node
            print(tag, "| cursor:", n and n.label.plain, "| focus:", app.focused)
        st("start")
        for k in ["down", "down", "enter", "down", "enter", "up", "up", "enter"]:
            await pilot.press(k); await pilot.pause(0.5); st(k)
        print(app.screen.query_one(Markdown).source[:300])
asyncio.run(main())
