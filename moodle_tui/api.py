"""Schlanker Client für die Moodle-Webservice-Schnittstelle (dieselbe wie die Moodle-App)."""

import hashlib
import json
import os
import time
from pathlib import Path

import requests

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
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "moodle-tui"
        self._userid = None
        (CACHE_DIR / "json").mkdir(parents=True, exist_ok=True)

    def call(self, function, **params):
        """Ruft eine Webservice-Funktion auf; bei Netzfehlern gibt es die letzte Antwort aus dem Cache."""
        key = hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:10]
        cache = CACHE_DIR / "json" / f"{function}-{key}.json"
        data = {"wstoken": self.token, "wsfunction": function, "moodlewsrestformat": "json",
                "moodlewssettingfilter": "true", "moodlewssettinglang": "de", **_flatten(params)}
        try:
            r = self.session.post(f"{SITE}/webservice/rest/server.php", data=data, timeout=20)
            r.raise_for_status()
            result = r.json()
        except (requests.ConnectionError, requests.Timeout):
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
        url = url.replace("/pluginfile.php", "/webservice/pluginfile.php", 1) if "/webservice/" not in url else url
        return url, {"token": self.token}

    def fetch_text(self, fileurl):
        url, params = self._authed(fileurl)
        r = self.session.get(url, params=params, timeout=20)
        r.raise_for_status()
        r.encoding = r.encoding or "utf-8"
        return r.text

    def download(self, fileurl, target: Path, timemodified=0):
        """Lädt eine Datei in den Cache, falls sie dort fehlt oder veraltet ist."""
        if target.exists() and target.stat().st_mtime >= timemodified:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        url, params = self._authed(fileurl)
        tmp = target.with_suffix(target.suffix + ".part")
        with self.session.get(url, params=params, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
        tmp.rename(target)
        if timemodified:
            os.utime(target, (timemodified, timemodified))
        return target
