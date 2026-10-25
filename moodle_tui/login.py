"""Anmeldung über RWTH-SSO im Browser – wie bei der Moodle-App.

Moodle leitet nach dem Login auf moodlemobile://token=… weiter. Diesen Link fängt
`moodle handle-url` ab (registriert per .desktop-Datei als URL-Handler).
"""

import base64
import hashlib
import secrets
import select
import subprocess
import sys
import time
from urllib.parse import unquote

from .api import CONFIG_DIR, SITE, TOKEN_FILE, Moodle, MoodleError, save_token

PASSPORT_FILE = CONFIG_DIR / "passport"


def _notify(text):
    subprocess.run(["notify-send", "-a", "Moodle", "Moodle", text], check=False)


def parse_token_url(url):
    """Liefert das Token aus moodlemobile://token=BASE64 und prüft die Signatur."""
    url = unquote(url.strip())
    if "token=" not in url:
        raise ValueError("Kein token= im Link gefunden.")
    payload = url.split("token=", 1)[1].split("&")[0].strip("/")
    payload += "=" * (-len(payload) % 4)
    parts = base64.b64decode(payload).decode().split(":::")
    if len(parts) < 2:
        raise ValueError("Unerwartetes Token-Format.")
    signature, token = parts[0], parts[1]
    try:
        passport = PASSPORT_FILE.read_text().strip()
    except FileNotFoundError:
        raise ValueError("Kein laufender Login – erst `moodle login` starten.")
    if hashlib.md5((SITE + passport).encode()).hexdigest() != signature:
        raise ValueError("Signatur passt nicht – bitte `moodle login` neu starten.")
    return token


def handle_url(url):
    try:
        token = parse_token_url(url)
        Moodle(token).call("core_webservice_get_site_info")
    except (ValueError, MoodleError) as e:
        _notify(f"Anmeldung fehlgeschlagen: {e}")
        print(e, file=sys.stderr)
        return 1
    save_token(token)
    PASSPORT_FILE.unlink(missing_ok=True)
    _notify("Angemeldet ✓")
    return 0


def login():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    passport = str(secrets.randbelow(10**9))
    PASSPORT_FILE.write_text(passport)
    old = TOKEN_FILE.stat().st_mtime if TOKEN_FILE.exists() else 0
    url = (f"{SITE}/admin/tool/mobile/launch.php?service=moodle_mobile_app"
           f"&passport={passport}&urlscheme=moodlemobile")
    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    print("Im Browser über RWTH-SSO anmelden. Danach fragt der Browser, ob er den")
    print("moodlemobile://-Link öffnen darf → mit „Moodle-Anmeldung“ öffnen.\n")
    print("Klappt das nicht: den moodlemobile://token=…-Link (z. B. aus der Netzwerkanalyse)")
    print("hier einfügen und Enter drücken. Abbrechen mit Strg+C.\n")
    deadline = time.time() + 600
    while time.time() < deadline:
        if TOKEN_FILE.exists() and TOKEN_FILE.stat().st_mtime > old:
            print("Angemeldet ✓  – jetzt einfach `moodle` starten.")
            return 0
        ready, _, _ = select.select([sys.stdin], [], [], 1)
        if ready:
            line = sys.stdin.readline().strip()
            if line:
                return handle_url(line)
    print("Zeit abgelaufen.")
    return 1
