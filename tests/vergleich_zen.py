"""Sauberer Vergleich Moodle-TUI vs. Zen.
Jeder Lauf: Start -> Übersicht -> Seminar Soziologie -> Foucault-PDF 35 s -> Kurs 2 -> Übersicht -> Lesepause -> Beenden.
Fenster fokussiert, aber nicht Vollbild (normale niri-Spalte, so nutzt Enzo es).
Gemessen: Akkuleistung (ganzes System inkl. Display, 1/s), CPU-Paketenergie (RAPL), CPU-Zeit + RAM der Programm-cgroups.
Aufruf: vergleich_zen.py login | login-ende | serie [N]
Vorher: Profilkopie in ~/.cache/mess-zen-profil (Reflink von Enzos Zen-Profil, user.js mit marionette + startup.page=3)."""
import json, os, socket, statistics, subprocess, sys, threading, time
from pathlib import Path

SP = Path.home() / ".cache/moodle-vergleich"  # Ergebnisse (serie.jsonl), TUI-Zustand und -Cache
PROFIL = Path.home() / ".cache/mess-zen-profil"
M = "https://moodle.rwth-aachen.de"
C1, C2 = 62409, 62726
DUR = int(os.environ.get("DUR", 150))  # Sekunden pro Lauf, Ablauf braucht mind. ~110
PDF_CMID = 2382021  # Foucault 1999, 13 MB, Lesetext 29.10.
BAT = Path("/sys/class/power_supply/BAT0")


def net():
    tot = 0
    for line in open("/proc/net/dev").readlines()[2:]:
        name, rest = line.split(":", 1)
        if name.strip() != "lo":
            f = rest.split(); tot += int(f[0]) + int(f[8])
    return tot


def rapl():
    return int(subprocess.check_output(["sudo", "cat", "/sys/class/powercap/intel-rapl:0/energy_uj"]))


class Akku(threading.Thread):
    """Mittelt Spannung * Strom im Sekundentakt."""
    def __init__(self):
        super().__init__(daemon=True)
        self.w, self.stop = [], False

    def run(self):
        while not self.stop:
            u = int((BAT / "voltage_now").read_text()) / 1e6
            i = int((BAT / "current_now").read_text()) / 1e6
            self.w.append(u * i)
            time.sleep(1)


def scopes(unit):
    p = subprocess.check_output(["systemctl", "--user", "show", "-p", "ControlGroup", "--value", unit], text=True).strip()
    if not p:
        return []
    base = Path("/sys/fs/cgroup" + p)
    out = [base]
    for pid in (base / "cgroup.procs").read_text().split():  # kitty verschiebt Kinder nach kitty-<pid>-N.scope
        out += base.parent.glob(f"kitty-{pid}-*.scope")
    return out


def stat(paths, name, key=None):
    tot = 0
    for p in paths:
        try:
            txt = (p / name).read_text()
        except FileNotFoundError:
            continue
        if key:
            tot += next(int(l.split()[1]) for l in txt.splitlines() if l.startswith(key + " "))
        else:
            tot += int(txt)
    return tot


class Prog:
    """Sammelt während des Laufs CPU-Zeit und RAM-Spitze (die Scopes verschwinden beim Beenden)."""
    def __init__(self, unit):
        self.unit, self.cpu, self.ram, self.anon = unit, 0, 0, 0

    def poll(self):
        ps = scopes(self.unit)
        if ps:
            self.cpu = max(self.cpu, stat(ps, "cpu.stat", "usage_usec"))
            self.ram = max(self.ram, stat(ps, "memory.current"))
            self.anon = max(self.anon, stat(ps, "memory.stat", "anon"))


def niri_window(pids_of, timeout=30, app_id=None):
    """Wartet auf das Fenster des Programms und fokussiert es."""
    t = time.time()
    while time.time() - t < timeout:
        pids = pids_of()
        for w in json.loads(subprocess.check_output(["niri", "msg", "-j", "windows"])):
            if w["pid"] in pids and (app_id is None or w["app_id"] == app_id):
                subprocess.run(["niri", "msg", "action", "focus-window", "--id", str(w["id"])], check=True)
                return w["id"]
        time.sleep(0.2)
    raise RuntimeError("kein Fenster")


def focused(wid):
    return any(w["id"] == wid and w["is_focused"] for w in json.loads(subprocess.check_output(["niri", "msg", "-j", "windows"])))


def procs(unit):
    return {int(p) for s in scopes(unit) for p in (s / "cgroup.procs").read_text().split()}


# ---------------------------------------------------------------- Marionette
class Mario:
    def __init__(self, port=2828, timeout=30):
        t = time.time()
        while True:
            try:
                self.s = socket.create_connection(("127.0.0.1", port))
                break
            except OSError:
                if time.time() - t > timeout:
                    raise
                time.sleep(0.2)
        self.i = 0
        self.recv()
        self.cmd("WebDriver:NewSession", {"capabilities": {}})

    def recv(self):
        buf = b""
        while b":" not in buf:
            buf += self.s.recv(1)
        n, rest = buf.split(b":", 1)
        while len(rest) < int(n):
            rest += self.s.recv(int(n) - len(rest))
        return json.loads(rest)

    def cmd(self, name, params=None):
        self.i += 1
        msg = json.dumps([0, self.i, name, params or {}]).encode()
        self.s.sendall(str(len(msg)).encode() + b":" + msg)
        r = self.recv()
        if r[2]:
            raise RuntimeError(r[2])
        return r[3]

    def go(self, url):
        self.cmd("WebDriver:Navigate", {"url": url})
        return self.cmd("WebDriver:GetCurrentURL")["value"]


def zen_cmd(*url):
    return ["systemd-run", "--user", "--scope", "--unit=mess-zen", "zen-browser", "--no-remote", "--marionette",
            "--profile", str(PROFIL), *url]


def zen_quit(m):
    """Nur einen leeren Tab übrig lassen, damit die Sitzung (mit Login-Cookies) gespeichert wird, aber beim nächsten Start nichts lädt."""
    hs = m.cmd("WebDriver:GetWindowHandles")
    for h in hs[1:]:
        m.cmd("WebDriver:SwitchToWindow", {"handle": h}); m.cmd("WebDriver:CloseWindow")
    m.cmd("WebDriver:SwitchToWindow", {"handle": hs[0]})
    m.go("about:blank")
    try:
        m.cmd("Marionette:Quit", {"flags": ["eAttemptQuit"]})
    except Exception:
        pass
    while procs("mess-zen.scope"):
        time.sleep(0.2)


def at(t0, s, prog=None):
    while time.time() < t0 + s:
        if prog:
            prog.poll()
        time.sleep(0.5)


# ---------------------------------------------------------------- Läufe
def lauf_tui():
    sock = SP / "k.sock"
    sock.unlink(missing_ok=True)
    def keys(*ks):
        for k in ks:
            subprocess.run(["kitty", "@", "--to", f"unix:{sock}", "send-key", k], check=True)
            time.sleep(0.4)
    prog, ok = Prog("mess-tui.scope"), True
    t0 = time.time()
    env = dict(os.environ, XDG_STATE_HOME=str(SP / "tui-state"), XDG_CACHE_HOME=str(SP / "tui-cache"))
    p = subprocess.Popen(["systemd-run", "--user", "--scope", "--unit=mess-tui", "kitty", "-o", "allow_remote_control=yes",
                          "--listen-on", f"unix:{sock}", "moodle"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    wid = niri_window(lambda: procs("mess-tui.scope"))
    at(t0, 20, prog); ok &= focused(wid); keys("enter")                       # Kurs 1 (Seminar Soziologie)
    at(t0, 30, prog); keys(*["down"] * 5, "enter", "down", "enter", "down", "down")  # Lerninhalte > Texte > Foucault 1999
    at(t0, 40, prog); keys("enter")                                           # PDF laden + zathura
    pdf = niri_window(lambda: procs("mess-tui.scope"), timeout=30, app_id="org.pwmt.zathura")
    at(t0, 75, prog); ok &= focused(pdf)
    subprocess.run(["niri", "msg", "action", "close-window", "--id", str(pdf)], check=True)
    time.sleep(0.5)
    subprocess.run(["niri", "msg", "action", "focus-window", "--id", str(wid)], check=True)
    at(t0, 80, prog); keys("escape"); time.sleep(1); keys("down", "enter")    # Kurs 2
    at(t0, 90, prog); keys("down", "down", "down"); ok &= focused(wid)
    at(t0, 100, prog); keys("escape")                                         # Übersicht, Lesepause
    at(t0, DUR - 5, prog); ok &= focused(wid); keys("q")
    p.wait()
    at(t0, DUR)
    return prog, {"fokus_ok": ok}


def lauf_zen():
    prog, ok, info = Prog("mess-zen.scope"), True, {}
    t0 = time.time()
    subprocess.Popen(zen_cmd(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    m = Mario()
    wid = niri_window(lambda: procs("mess-zen.scope"))
    hs = m.cmd("WebDriver:GetWindowHandles")  # nur einen (dann sichtbaren) Tab behalten
    for h in hs[1:]:
        m.cmd("WebDriver:SwitchToWindow", {"handle": h}); m.cmd("WebDriver:CloseWindow")
    m.cmd("WebDriver:SwitchToWindow", {"handle": hs[0]})
    info["url_uebersicht"] = m.go(M + "/my/")
    at(t0, 20, prog); ok &= focused(wid); info["url_kurs1"] = m.go(f"{M}/course/view.php?id={C1}")
    at(t0, 40, prog); info["url_pdf"] = m.go(f"{M}/mod/resource/view.php?id={PDF_CMID}&redirect=1")
    at(t0, 75, prog); ok &= focused(wid); m.go(f"{M}/course/view.php?id={C1}")
    at(t0, 80, prog); m.go(M + "/my/"); info["url_kurs2"] = m.go(f"{M}/course/view.php?id={C2}"); ok &= focused(wid)
    at(t0, 100, prog); m.go(M + "/my/")
    at(t0, DUR - 5, prog); ok &= focused(wid)
    zen_quit(m)
    at(t0, DUR)
    info["fokus_ok"] = ok
    info["eingeloggt"] = all("login" not in info[k] and "sso" not in info[k] for k in ("url_uebersicht", "url_kurs1", "url_kurs2")) and info["url_pdf"].endswith(".pdf")
    return prog, info


def lauf_idle():
    t0 = time.time()
    at(t0, DUR)
    return None, {}


def messen(name, fn):
    akku = Akku(); akku.start()
    e0, n0, t0 = rapl(), net(), time.time()
    prog, info = fn()
    e1, n1, t1 = rapl(), net(), time.time()
    akku.stop = True; akku.join()
    r = {"lauf": name, "sek": round(t1 - t0, 1), "akku_W": round(statistics.mean(akku.w), 2),
         "paket_W": round((e1 - e0) / 1e6 / (t1 - t0), 2), "netz_MB": round((n1 - n0) / 1e6, 1), **info}
    if prog:
        r |= {"cpu_s": round(prog.cpu / 1e6, 2), "ram_MB": round(prog.ram / 2**20), "anon_MB": round(prog.anon / 2**20)}
    print(json.dumps(r, ensure_ascii=False), flush=True)
    with open(SP / "serie.jsonl", "a") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
    time.sleep(10)  # kurz ausklingen lassen


def main():
    if sys.argv[1] == "login":
        subprocess.Popen(zen_cmd(M + "/my/"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    if sys.argv[1] == "login-ende":
        zen_quit(Mario())
        return
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    messen("aufwaermen-tui", lauf_tui)
    messen("aufwaermen-zen", lauf_zen)
    for i in range(n):
        messen("idle", lauf_idle)
        messen("tui", lauf_tui) if i % 2 == 0 else messen("zen", lauf_zen)
        messen("zen", lauf_zen) if i % 2 == 0 else messen("tui", lauf_tui)
    messen("idle", lauf_idle)


main()
