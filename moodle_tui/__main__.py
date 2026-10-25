import sys

from .api import load_token


def main():
    args = sys.argv[1:]
    if args[:1] == ["login"]:
        from .login import login
        return login()
    if args[:1] == ["handle-url"] and len(args) == 2:
        from .login import handle_url
        return handle_url(args[1])
    if args:
        print("Aufruf: moodle [login | handle-url URL]")
        return 2
    if not load_token():
        from .login import login
        if login() != 0:
            return 1
    from .app import MoodleApp
    app = MoodleApp()
    app.run()
    if app.error:
        print(app.error)
        return 1
    return 0


sys.exit(main())
