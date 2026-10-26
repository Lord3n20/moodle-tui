"""Schlanker Client für die Moodle-Webservice-Schnittstelle (dieselbe wie die Moodle-App)."""

import hashlib
import http.client
import json
import os
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

SITE = "https://moodle.rwth-aachen.de"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "moodle-tui"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "moodle-tui"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "moodle-tui"
TOKEN_FILE = CONFIG_DIR / "token"


class MoodleError(Exception):
    def __init__(self, message, code=""):
        super().__init__(message)
        self.code = code


class NotLoggedIn(MoodleError):
    pass


def load_token():
    try:
        return TOKEN_FILE.read_text().strip() or None
    except FileNotFoundError:
        return None


def save_token(token):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.touch(mode=0o600)
    TOKEN_FILE.chmod(0o600)
    TOKEN_FILE.write_text(token + "\n")


_local = threading.local()
_ssl = ssl.create_default_context()
NET_ERRORS = (OSError, http.client.HTTPException)


def request(url, data=None, timeout=20):
    """GET (oder POST mit data) über eine offen gehaltene Verbindung je Thread und Host.

    Gibt die Antwort zurück; der Aufrufer muss sie ganz lesen, bevor er die nächste Anfrage stellt."""
    for _ in range(5):  # Weiterleitungen
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        body = urlencode(data).encode() if data is not None else None
        headers = {"User-Agent": "moodle-tui"}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conns = _local.__dict__.setdefault("conns", {})
        for attempt in (0, 1):
            conn = conns.get(parts.netloc)
            if conn is None:
                conn = conns[parts.netloc] = http.client.HTTPSConnection(parts.netloc, context=_ssl)
            conn.timeout = timeout
            if conn.sock:
                conn.sock.settimeout(timeout)
            try:
                conn.request("GET" if body is None else "POST", path, body, headers)
                r = conn.getresponse()
                break
            except NET_ERRORS as e:
                conn.close()
                # Der Server schließt ruhende Verbindungen – dann einmal neu verbinden
                if attempt or isinstance(e, TimeoutError):
                    raise
        if r.status in (301, 302, 303, 307, 308) and r.getheader("Location"):
            r.read()
            url, data = urljoin(url, r.getheader("Location")), None
            continue
        if r.status >= 400:
            r.read()
            raise MoodleError(f"HTTP {r.status} bei {parts.path}", "http")
        return r
    raise MoodleError("Zu viele Weiterleitungen.", "http")


def _flatten(params, prefix=""):
    """Moodle erwartet Listen/Dicts als key[0][feld]=wert."""
    out = {}
    items = params.items() if isinstance(params, dict) else enumerate(params)
    for key, value in items:
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, (dict, list, tuple)):
            out.update(_flatten(value, name))
        elif isinstance(value, bool):
            out[name] = int(value)
        else:
            out[name] = value
    return out


class Moodle:
    def __init__(self, token=None):
        self.token = token or load_token()
        if not self.token:
            raise NotLoggedIn("Nicht angemeldet – erst `moodle login` ausführen.", "notoken")
        self._userid = None
        (CACHE_DIR / "json").mkdir(parents=True, exist_ok=True)

    def call(self, function, **params):
        """Ruft eine Webservice-Funktion auf; bei Netzfehlern gibt es die letzte Antwort aus dem Cache."""
        key = hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:10]
        cache = CACHE_DIR / "json" / f"{function}-{key}.json"
        data = {"wstoken": self.token, "wsfunction": function, "moodlewsrestformat": "json",
                "moodlewssettingfilter": "true", "moodlewssettinglang": "de", **_flatten(params)}
        try:
            result = json.loads(request(f"{SITE}/webservice/rest/server.php", data).read())
        except NET_ERRORS:
            if cache.exists():
                return json.loads(cache.read_text())
            raise MoodleError("Keine Verbindung zu Moodle.", "offline")
        if isinstance(result, dict) and "exception" in result:
            code = result.get("errorcode", "")
            if code in ("invalidtoken", "accessexception"):
                raise NotLoggedIn("Anmeldung abgelaufen – `moodle login` ausführen.", code)
            raise MoodleError(result.get("message", "Unbekannter Fehler"), code)
        cache.write_text(json.dumps(result))
        return result

    @property
    def userid(self):
        if self._userid is None:
            self._userid = self.call("core_webservice_get_site_info")["userid"]
        return self._userid

    # --- Übersicht -------------------------------------------------------

    def courses(self):
        res = self.call("core_course_get_enrolled_courses_by_timeline_classification", classification="inprogress", sort="fullname")
        return sorted(res["courses"], key=lambda c: c["fullname"].lower())

    def deadlines(self, days=60):
        now = int(time.time())
        res = self.call(
            "core_calendar_get_action_events_by_timesort",
            timesortfrom=now - 86400,
            timesortto=now + days * 86400,
            limitnum=50,
        )
        return res["events"]

    def notifications(self, limit=40):
        res = self.call("message_popup_get_popup_notifications", useridto=self.userid, limit=limit)
        return res["notifications"]

    def mark_notification_read(self, notification_id):
        self.call("core_message_mark_notification_read", notificationid=notification_id)

    # --- Kursinhalte -----------------------------------------------------

    def course_by_id(self, courseid):
        return self.call("core_course_get_courses_by_field", field="id", value=courseid)["courses"][0]

    def course_module(self, cmid):
        return self.call("core_course_get_course_module", cmid=cmid)["cm"]

    def course_contents(self, courseid):
        return self.call("core_course_get_contents", courseid=courseid)

    def forum_discussions(self, forumid):
        res = self.call("mod_forum_get_forum_discussions", forumid=forumid, sortorder=-1, page=0, perpage=30)
        return res["discussions"]

    def discussion_posts(self, discussionid):
        res = self.call("mod_forum_get_discussion_posts", discussionid=discussionid, sortby="created", sortdirection="ASC")
        return res["posts"]

    def assignments(self, courseid):
        res = self.call("mod_assign_get_assignments", courseids=[courseid])
        return {a["cmid"]: a for c in res["courses"] for a in c["assignments"]}

    def submission_status(self, assignid):
        return self.call("mod_assign_get_submission_status", assignid=assignid)

    # --- Dateien ---------------------------------------------------------

    def _authed(self, url):
        if "/webservice/" not in url:
            url = url.replace("/pluginfile.php", "/webservice/pluginfile.php", 1)
        return url + ("&" if "?" in url else "?") + urlencode({"token": self.token})

    def fetch_text(self, fileurl):
        r = request(self._authed(fileurl))
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")

    def download(self, fileurl, target: Path, timemodified=0):
        """Lädt eine Datei in den Cache, falls sie dort fehlt oder veraltet ist."""
        if target.exists() and target.stat().st_mtime >= timemodified:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        # Das Token nur an Moodle schicken, nie an fremde Server (Bilder in Beschreibungen)
        r = request(self._authed(fileurl) if fileurl.startswith(SITE) else fileurl, timeout=60)
        with open(tmp, "wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)
        tmp.rename(target)
        if timemodified:
            os.utime(target, (timemodified, timemodified))
        return target

    def image(self, url):
        """Bild aus einer Beschreibung in den Cache laden; gibt den Pfad zurück."""
        name = url.split("?")[0].rsplit("/", 1)[-1]
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        target = CACHE_DIR / "img" / (hashlib.md5(url.encode()).hexdigest()[:16] + suffix[:6])
        return self.download(url, target)
