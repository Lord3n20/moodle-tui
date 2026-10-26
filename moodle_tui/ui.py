"""Schlanke curses-Oberfläche: Liste/Tabelle, Baum, Textansicht, Eingabezeile, Fußzeile, Meldungen.

Wartet ohne Abfrageschleife per select() auf Tasten oder Ergebnisse der Hintergrund-Threads.
"""

import base64
import curses
import os
import queue
import re
import select
import signal
import shutil
import struct
import subprocess
import sys
import threading
import time

from .util import char_width, width

KEYS = {curses.KEY_UP: "up", curses.KEY_DOWN: "down", curses.KEY_LEFT: "left", curses.KEY_RIGHT: "right",
        curses.KEY_PPAGE: "pageup", curses.KEY_NPAGE: "pagedown", curses.KEY_HOME: "home", curses.KEY_END: "end",
        curses.KEY_BACKSPACE: "backspace", curses.KEY_DC: "delete", curses.KEY_ENTER: "enter",
        curses.KEY_BTAB: "shift+tab", "\n": "enter", "\r": "enter", "\t": "tab", "\x1b": "escape",
        "\x7f": "backspace", "\x08": "backspace", "\x0e": "ctrl+n", "\x15": "ctrl+u"}
WHEEL_UP = curses.BUTTON4_PRESSED
WHEEL_DOWN = getattr(curses, "BUTTON5_PRESSED", 0x200000)

STYLE = {}  # Stil-Buchstabe → curses-Attribut (siehe util.Doc), gefüllt in _init_styles
_attr_cache = {}


def _init_styles():
    curses.start_color()
    curses.use_default_colors()  # Terminal-Hintergrund und -Farben übernehmen
    many = curses.COLORS >= 16
    pairs = {"accent": (12 if many else curses.COLOR_BLUE, -1), "yellow": (curses.COLOR_YELLOW, -1),
             "red": (curses.COLOR_RED, -1), "cyan": (curses.COLOR_CYAN, -1), "magenta": (curses.COLOR_MAGENTA, -1),
             "dimsel": (-1, 8 if many else curses.COLOR_BLACK), "toast": (curses.COLOR_BLACK, 12 if many else 4),
             "toasterr": (curses.COLOR_WHITE, curses.COLOR_RED)}
    color = {}
    for n, (name, (fg, bg)) in enumerate(pairs.items(), 1):
        curses.init_pair(n, fg, bg)
        color[name] = curses.color_pair(n)
    STYLE.update(b=curses.A_BOLD, i=curses.A_ITALIC, h=color["accent"] | curses.A_BOLD, c=color["magenta"],
                 d=curses.A_DIM, n=color["yellow"] | curses.A_BOLD, e=color["red"] | curses.A_BOLD,
                 r=curses.A_DIM, l=color["cyan"] | curses.A_UNDERLINE, a=color["accent"],
                 sel=curses.A_REVERSE, dimsel=color["dimsel"], toast=color["toast"], toasterr=color["toasterr"])
    _attr_cache.clear()


def attr(style, link=None):
    key = (style, bool(link))
    if key not in _attr_cache:
        a = curses.A_NORMAL
        for ch in style + ("l" if link else ""):
            a |= STYLE.get(ch, 0)
        _attr_cache[key] = a
    return _attr_cache[key]


def fit(text, maxw):
    """Wie viele Zeichen von text passen in maxw Spalten."""
    used = 0
    for i, ch in enumerate(text):
        used += char_width(ch)
        if used > maxw:
            return i
    return len(text)


def put(win, y, x, text, a=0, maxw=None):
    """Schreibt text ab (y, x), abgeschnitten auf maxw Spalten; gibt die belegte Breite zurück."""
    if maxw is not None:
        if maxw <= 0:
            return 0
        if width(text) > maxw:
            text = text[:fit(text, maxw)]
    try:
        win.addstr(y, x, text, a)
    except curses.error:
        pass  # untere rechte Ecke
    return width(text)


def put_runs(win, y, x, runs, maxw, extra=0):
    used = 0
    for text, style, *link in runs:
        used += put(win, y, x + used, text, attr(style, link and link[0]) | extra, maxw - used)
        if used >= maxw:
            break
    return used


def hline(win, y, x, w):
    put(win, y, x, "─" * w, STYLE["d"])


def vline(win, y, x, h, char="│", a=None):
    for row in range(h):
        put(win, y + row, x, char, STYLE["d"] if a is None else a)


TOKEN = re.compile(r"\s+|\S+")


def wrap(runs, w):
    """Bricht eine Zeile aus (text, stil, link)-Stücken auf w Spalten um; Folgezeilen hängen am Einzug."""
    indent = 0
    for text, style, _ in runs:
        if "p" not in style:
            break
        indent += width(text)
    indent = indent if indent < w // 2 else 0
    lines, line, used = [], [], 0

    def newline():
        nonlocal line, used
        lines.append(line)
        line, used = ([(" " * indent, "p", None)] if indent else []), indent

    for text, style, link in runs:
        for tok in TOKEN.findall(text):
            tw = width(tok)
            if used + tw > w and used > indent and "p" not in style:
                newline()
                if tok.isspace():
                    continue
            while used + tw > w and len(tok) > 1:  # Wort länger als die Zeile: hart trennen
                cut = max(fit(tok, w - used), 1)
                line.append((tok[:cut], style, link))
                newline()
                tok = tok[cut:]
                tw = width(tok)
            line.append((tok, style, link))
            used += tw
    lines.append(line)
    return lines


# --------------------------------------------------------------------------- Bilder (kitty)

# kitty-Grafikprotokoll mit Unicode-Platzhaltern: Das Bild wird einmal übertragen und dann von
# normalen Textzellen (U+10EEEE + Zeilen-Diakritikum, Bild-ID als Vordergrundfarbe) dargestellt.
# Dadurch scrollt und schneidet curses es wie Text.
KITTY = os.environ.get("TERM") == "xterm-kitty"  # in tmux o. ä. gehen Bilder nicht durch
PLACEHOLDER = "\U0010EEEE"
ROW_MARKS = [chr(int(c, 16)) for c in (
    "0305 030D 030E 0310 0312 033D 033E 033F 0346 034A 034B 034C 0350 0351 0352 0357 035B 0363 0364 0365 "
    "0366 0367 0368 0369 036A 036B 036C 036D 036E 036F 0483 0484 0485 0486 0487 0592 0593 0594 0595 0597 "
    "0598 0599 059C 059D 059E 059F 05A0 05A1 05A8 05A9 05AB 05AC 05AF 05C4 0610 0611 0612 0613 0614 0615 "
    "0616 0617 0657 0658").split()]
MAX_IMAGE_ROWS = len(ROW_MARKS)


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def to_png(path):
    """PNG unverändert; andere Bildformate über vipsthumbnail oder ffmpeg umwandeln (kitty kann nur PNG)."""
    if png_size(path):
        return path
    out = f"{path}.tui.png"
    if not os.path.exists(out):
        if shutil.which("vipsthumbnail"):
            cmd = ["vipsthumbnail", str(path), "-s", "2000x2000", "-o", out]
        elif shutil.which("ffmpeg"):
            cmd = ["ffmpeg", "-loglevel", "error", "-i", str(path), "-frames:v", "1", out]
        else:
            return None
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out if os.path.exists(out) else None


def cell_size():
    """Pixelgröße einer Terminalzelle (für das Seitenverhältnis von Bildern)."""
    try:
        import fcntl
        import termios
        rows, cols, xpx, ypx = struct.unpack("HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8))
        if xpx and ypx:
            return xpx / cols, ypx / rows
    except OSError:
        pass
    return 8.0, 16.0


class Images:
    """Lädt Bilder im Hintergrund (loader: url → Pfad) und überträgt PNGs an kitty."""

    def __init__(self, app, loader):
        self.app, self.loader = app, loader
        self.files = {}        # url → Pfad, "loading" oder None (Fehler)
        self.placed = {}       # (Pfad, Spalten) → Bild-ID
        self.sizes = {}        # Pfad → (Breite, Höhe) in Pixeln
        self.version = 0       # steigt mit jedem fertig geladenen Bild (macht Layouts ungültig)
        self.pairs = {}        # Bild-ID → curses-Farbpaar
        self.inline = False  # wird nach dem Start von curses gesetzt

    def path(self, url):
        state = self.files.get(url)
        if url not in self.files:
            self.files[url] = "loading"
            self.app.thread(self._load, url)
        return state if state != "loading" else None

    def _load(self, url):
        try:
            path = url if url.startswith("/") else self.loader(url)  # lokale Datei oder aus Moodle laden
            if self.inline:
                path = to_png(path)
        except Exception:
            path = None
        self.app.post(self._loaded, url, path)

    def _loaded(self, url, path):
        self.files[url] = path
        self.version += 1

    def geometry(self, url, maxcols):
        """(Pfad, Spalten, Zeilen) für die Inline-Anzeige oder None – ohne etwas an kitty zu schicken."""
        if not self.inline:
            return None
        path = self.path(url)
        if not path:
            return None
        if path not in self.sizes:
            self.sizes[path] = png_size(path)
        size = self.sizes[path]
        if not size:
            return None
        cw, ch = cell_size()
        cols = max(1, min(maxcols, round(size[0] / cw)))
        rows = max(1, min(MAX_IMAGE_ROWS, round(cols * cw * size[1] / size[0] / ch)))
        cols = min(cols, max(1, round(rows * ch * size[0] / size[1] / cw)))  # bei gekappter Höhe schmaler
        return str(path), cols, rows

    def color(self, path, cols, rows):
        """Farbattribut (= Bild-ID) für ein sichtbares Bild; überträgt es beim ersten Mal an kitty."""
        key = (path, cols)
        if key not in self.placed:
            image_id = len(self.placed) % 254 + 1
            if image_id in self.pairs:  # ID wird wiederverwendet: alte Platzierung verwerfen
                self.placed = {k: v for k, v in self.placed.items() if v != image_id}
            else:
                n = 32 + image_id
                curses.init_pair(n, image_id, -1)
                self.pairs[image_id] = curses.color_pair(n)
            payload = base64.b64encode(path.encode()).decode()
            self._send(f"\x1b_Ga=T,U=1,q=2,f=100,t=f,i={image_id},c={cols},r={rows};{payload}\x1b\\")
            self.placed[key] = image_id
        return self.pairs[self.placed[key]]

    @staticmethod
    def _send(seq):
        os.write(sys.stdout.fileno(), seq.encode())

    def clear(self):
        if self.placed:
            self._send("".join(f"\x1b_Ga=d,d=I,q=2,i={i}\x1b\\" for i in self.pairs))


# --------------------------------------------------------------------------- Bausteine


class Widget:
    rect = (0, 0, 0, 0)
    focusable = True
    captures = False  # bekommt Tasten vor den Tastenkürzeln (Eingabezeile)

    def hit(self, y, x):
        y0, x0, h, w = self.rect
        return y0 <= y < y0 + h and x0 <= x < x0 + w

    def key(self, k):
        return False

    def click(self, y, x):
        pass

    def wheel(self, d):
        pass


class ListView(Widget):
    """Zeilen mit Cursor; jede Zeile ist eine Liste von Zellen, jede Zelle eine Liste von (text, stil[, link])."""

    def __init__(self, on_highlight=None, on_select=None, header=None, click_selects=True):
        self.rows, self.header = [], header
        self.cursor = self.top = 0
        self.follow = True
        self.on_highlight, self.on_select, self.click_selects = on_highlight, on_select, click_selects

    def set_rows(self, rows):
        self.rows = rows
        self.cursor = min(self.cursor, max(len(rows) - 1, 0))
        self.follow = True

    def move_to(self, i, force=False):
        if not self.rows:
            return
        i = max(0, min(i, len(self.rows) - 1))
        self.follow = True
        if i != self.cursor or force:
            self.cursor = i
            if self.on_highlight:
                self.on_highlight(i)

    def key(self, k):
        page = max(self.rect[2] - 2, 1)
        step = {"up": -1, "down": 1, "pageup": -page, "pagedown": page, "home": -10**9, "end": 10**9}.get(k)
        if step is not None:
            self.move_to(self.cursor + step)
            return True
        if k == "enter" and self.rows and self.on_select:
            self.on_select(self.cursor)
            return True
        return False

    def click(self, y, x):
        i = self.top + y - self.rect[0] - bool(self.header)
        if not 0 <= i < len(self.rows):
            return
        again = i == self.cursor
        self.move_to(i)
        if (again or self.click_selects) and self.on_select:
            self.on_select(i)

    def wheel(self, d):
        self.top += 3 * d
        self.follow = False

    def column_widths(self, w):
        n = max((len(r) for r in self.rows), default=len(self.header or []))
        if n <= 1:
            return [w]
        sample = self.rows + ([[[(h, "")] for h in self.header]] if self.header else [])
        widths = [min(max((sum(width(r[0]) for r in row[c]) for row in sample if c < len(row)), default=0), w // 3)
                  for c in range(n - 1)]
        return widths + [max(w - sum(widths) - 2 * (n - 1), 1)]

    def draw(self, win, y, x, h, w, focused):
        self.rect = (y, x, h, w)
        widths = self.column_widths(w - 2)
        if self.header:
            cx = x + 1
            for title, cw in zip(self.header, widths):
                put(win, y, cx, title, STYLE["b"] | STYLE["d"], cw)
                cx += cw + 2
            y, h = y + 1, h - 1
        if h <= 0:
            return
        if self.follow:
            if self.cursor < self.top:
                self.top = self.cursor
            elif self.cursor >= self.top + h:
                self.top = self.cursor - h + 1
        self.top = max(0, min(self.top, len(self.rows) - h))
        for row in range(min(h, len(self.rows) - self.top)):
            i = self.top + row
            extra = 0
            if i == self.cursor:
                extra = STYLE["sel"] if focused else STYLE["dimsel"]
                put(win, y + row, x, " " * w, extra)
            cx = x + 1
            for cell, cw in zip(self.rows[i], widths):
                put_runs(win, y + row, cx, cell, cw, extra)
                cx += cw + 2


class Node:
    def __init__(self, label, data=None, parent=None, expanded=False):
        self.label, self.data, self.parent, self.expanded = label, data or {}, parent, expanded
        self.children = []
        self.loaded = False

    def add(self, label, data=None, expand=False):
        node = Node(label, data, self, expand)
        self.children.append(node)
        return node

    def remove_children(self):
        self.children = []


class Tree(ListView):
    def __init__(self, on_highlight, on_select):
        super().__init__(lambda i: on_highlight(self.nodes[i]), self._select)
        self.root = Node(None, expanded=True)
        self.nodes = []
        self.node_selected = on_select

    def clear(self):
        self.root.children = []
        self.cursor = self.top = 0
        self.rebuild()

    @property
    def cursor_node(self):
        return self.nodes[self.cursor] if self.cursor < len(self.nodes) else None

    def rebuild(self):
        current = self.cursor_node
        self.nodes, rows = [], []

        def walk(node, depth):
            for child in node.children:
                self.nodes.append(child)
                arrow = ("▼ " if child.expanded else "▶ ") if child.children else "  "
                rows.append([[("  " * depth, "p"), (arrow, "d"), *child.label]])
                if child.expanded:
                    walk(child, depth + 1)

        walk(self.root, 0)
        if current in self.nodes:
            self.cursor = self.nodes.index(current)
        self.set_rows(rows)

    def move_cursor(self, node):
        parent = node.parent
        while parent:
            parent.expanded = True
            parent = parent.parent
        self.rebuild()
        if node in self.nodes:
            self.move_to(self.nodes.index(node), force=True)

    def toggle(self, node, expand=None):
        node.expanded = (not node.expanded) if expand is None else expand
        self.rebuild()

    def _select(self, i):
        node = self.nodes[i]
        if node.children:
            self.toggle(node)
        self.node_selected(node)

    def key(self, k):
        node = self.cursor_node
        if node and k == "space" and node.children:
            self.toggle(node)
        elif node and k == "right" and node.children:
            if node.expanded:
                self.move_to(self.cursor + 1)
            else:
                self.toggle(node, True)
        elif node and k == "left":
            if node.expanded and node.children:
                self.toggle(node, False)
            elif node.parent is not self.root:
                self.move_to(self.nodes.index(node.parent))
        else:
            return super().key(k)
        return True


class TextView(Widget):
    """Scrollbarer Text aus Doc-Zeilen; Links und Bilder lassen sich anklicken."""

    images = None  # Images-Instanz der App
    on_link = None  # Aufruf beim Klick auf einen Link: on_link(url, ist_bild)

    def __init__(self):
        self.doc, self.top, self.at_end = [], 0, False
        self.links = []
        self._layout = None

    def set(self, lines, end=False, keep=False):
        self.doc = lines
        self.at_end = end
        if not (end or keep):
            self.top = 0

    def layout(self, w):
        """Zeilen für die Anzeige: ('text', runs), ('image', (pfad, spalten, zeilen, zeile)) oder ('rule', None)."""
        key = (id(self.doc), len(self.doc), w, self.images and self.images.version)
        if self._layout and self._layout[0] == key:
            return self._layout[1]
        out = []
        for line in self.doc:
            if line and line[0][1] == "r":
                out.append(("rule", None))
            elif line and line[0][1] == "I":
                alt, url = line[0][0], line[0][2]
                place = self.images.geometry(url, w) if self.images else None
                if place:
                    path, cols, rows = place
                    out += [("image", (path, cols, rows, r)) for r in range(rows)]
                else:
                    if self.images:
                        self.images.path(url)  # vorab laden, damit ein Klick es im Bildbetrachter öffnet
                    out.append(("text", [("🖼 ", "dI", url), (alt, "I", url)]))
            else:
                out += [("text", runs) for runs in wrap(line, w)]
        self._layout = (key, out)
        return out

    def key(self, k):
        page = max(self.rect[2] - 1, 1)
        step = {"up": -1, "down": 1, "pageup": -page, "pagedown": page, "space": page,
                "home": -10**9, "end": 10**9}.get(k)
        if step is None:
            return False
        self.top += step
        return True

    def wheel(self, d):
        self.top += 3 * d

    def click(self, y, x):
        for ly, x0, x1, url, image in self.links:
            if ly == y and x0 <= x < x1 and self.on_link:
                self.on_link(url, image)
                return

    def draw(self, win, y, x, h, w, focused=False):
        self.rect = (y, x, h, w)
        lines = self.layout(w)
        if self.at_end:
            self.top = len(lines)
        self.top = max(0, min(self.top, len(lines) - h))
        self.links = []
        for row, (kind, value) in enumerate(lines[self.top:self.top + h]):
            if kind == "rule":
                hline(win, y + row, x, w)
            elif kind == "image":
                path, cols, rows, r = value
                a = self.images.color(path, cols, rows)
                put(win, y + row, x, PLACEHOLDER + ROW_MARKS[r] + ROW_MARKS[0] + (PLACEHOLDER + ROW_MARKS[r]) * (cols - 1), a)
            else:
                cx = x
                for text, style, link in value:
                    used = put(win, y + row, cx, text, attr(style, link and style != "p"), x + w - cx)
                    if link and style != "p":
                        self.links.append((y + row, cx, cx + used, link, "I" in style))
                    cx += used


class Input(Widget):
    captures = True

    def __init__(self, on_submit, placeholder=""):
        self.on_submit, self.placeholder = on_submit, placeholder
        self.text, self.pos = "", 0

    def key(self, k):
        if len(k) == 1 and k.isprintable():
            self.text = self.text[:self.pos] + k + self.text[self.pos:]
            self.pos += 1
        elif k == "space":
            return self.key(" ")
        elif k == "backspace" and self.pos:
            self.text = self.text[:self.pos - 1] + self.text[self.pos:]
            self.pos -= 1
        elif k == "delete":
            self.text = self.text[:self.pos] + self.text[self.pos + 1:]
        elif k in ("left", "right", "home", "end"):
            self.pos = {"left": max(self.pos - 1, 0), "right": min(self.pos + 1, len(self.text)),
                        "home": 0, "end": len(self.text)}[k]
        elif k == "ctrl+u":
            self.text, self.pos = "", 0
        elif k == "enter":
            self.on_submit(self.text)
        else:
            return k == "backspace"
        return True

    def clear(self):
        self.text, self.pos = "", 0

    def draw(self, win, y, x, h, w, focused=True):
        """Zeichnet die Zeile und gibt die Cursorposition zurück."""
        self.rect = (y, x, 1, w)
        put(win, y, x, "› ", STYLE["a"] | STYLE["b"])
        x, w = x + 2, w - 2
        if not self.text:
            put(win, y, x, self.placeholder, STYLE["d"], w)
            return y, x
        before = width(self.text[:self.pos])
        shift = max(0, before - w + 1)
        start = fit(self.text, shift)
        while width(self.text[:start]) < shift:
            start += 1
        put(win, y, x, self.text[start:], 0, w)
        return y, x + before - width(self.text[:start])


# --------------------------------------------------------------------------- Bildschirme und App


class Screen:
    bindings = []  # (Tasten, Beschriftung oder None, Methode an Bildschirm oder App)
    modal = False
    focus = None
    cursor_pos = None  # (y, x) für den Textcursor, sonst None

    def mount(self):
        pass

    def draw(self, win, h, w):
        pass

    def focusables(self):
        return []

    def widgets(self):
        return self.focusables()

    def action(self, name):
        return getattr(self, name, None) or getattr(self.app, name)

    def key(self, k):
        if self.focus and self.focus.captures and self.focus.key(k):
            return
        for keys, _, name in self.bindings:
            if k in keys.split(",") and self.app.allowed(name):
                self.action(name)()
                return
        if k in ("tab", "shift+tab") and self.focusables():
            items = self.focusables()
            i = items.index(self.focus) if self.focus in items else -1
            self.focus = items[(i + (1 if k == "tab" else -1)) % len(items)]
        elif self.focus:
            self.focus.key(k)

    def click(self, y, x, button):
        for widget in self.widgets():
            if widget.hit(y, x):
                if button == "click":
                    if widget.focusable and widget in self.focusables():
                        self.focus = widget
                    widget.click(y, x)
                else:
                    widget.wheel(-1 if button == "up" else 1)
                return


class App:
    def __init__(self):
        self.screens, self.toasts, self.footer = [], [], []
        self.running = False
        self._queue = queue.SimpleQueue()
        self._wake_r, self._wake_w = os.pipe()
        self.images = None

    @property
    def screen(self):
        return self.screens[-1]

    def push(self, screen):
        screen.app = self
        self.screens.append(screen)
        screen.mount()

    def pop(self):
        if len(self.screens) > 1:
            self.screens.pop()

    def exit(self):
        self.running = False

    def allowed(self, action):
        return True

    def on_start(self):
        pass

    def post(self, fn, *args):
        """Aus einem Hintergrund-Thread: fn(*args) im Haupt-Thread ausführen und neu zeichnen."""
        self._queue.put((fn, args))
        os.write(self._wake_w, b".")

    def thread(self, fn, *args):
        def run():
            try:
                fn(*args)
            except Exception as e:
                self.post(self.notify, f"Fehler: {e}", True)
        threading.Thread(target=run, daemon=True).start()

    def notify(self, text, error=False, timeout=4):
        self.toasts.append((text, error, time.monotonic() + (6 if error else timeout)))

    def run(self):
        try:
            curses.wrapper(self._main)
        except KeyboardInterrupt:
            pass
        finally:
            if self.images:
                self.images.clear()

    def _main(self, scr):
        self.scr = scr
        curses.set_escdelay(25)
        curses.curs_set(0)
        _init_styles()
        scr.keypad(True)
        scr.nodelay(True)
        curses.mousemask(curses.BUTTON1_PRESSED | WHEEL_UP | WHEEL_DOWN)
        curses.mouseinterval(0)
        signal.signal(signal.SIGWINCH, lambda *_: os.write(self._wake_w, b"r"))
        self.running = True
        self.on_start()
        while self.running and self.screens:
            now = time.monotonic()
            self.toasts = [t for t in self.toasts if t[2] > now]
            self._draw()
            timeout = min((t[2] for t in self.toasts), default=None)
            ready, _, _ = select.select([sys.stdin, self._wake_r], [], [], None if timeout is None else max(timeout - now, 0))
            if self._wake_r in ready and b"r" in os.read(self._wake_r, 4096):
                cols, lines = os.get_terminal_size()
                curses.resizeterm(lines, cols)
            while not self._queue.empty():
                fn, args = self._queue.get()
                fn(*args)
            while self.running:
                try:
                    ch = scr.get_wch()
                except curses.error:
                    break
                self._key(ch)

    def _key(self, ch):
        if ch == curses.KEY_MOUSE:
            try:
                _, x, y, _, state = curses.getmouse()
            except curses.error:
                return
            button = "up" if state & WHEEL_UP else "down" if state & WHEEL_DOWN else "click"
            h, _ = self.scr.getmaxyx()
            if y == h - 1 and button == "click":
                for x0, x1, screen, name in self.footer:
                    if x0 <= x < x1:
                        screen.action(name)()
                return
            self.screen.click(y, x, button)
            return
        k = KEYS.get(ch, ch if isinstance(ch, str) else None)
        if k == " ":
            k = "space"
        if k:
            self.screen.key(k)

    def _draw(self):
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        base = len(self.screens) - 1
        while base > 0 and self.screens[base].modal:
            base -= 1
        for screen in self.screens[base:]:
            screen.draw(scr, h - 1, w)
        top = self.screen
        self.footer, x = [], 1
        for keys, label, name in top.bindings:
            if not label or not self.allowed(name):
                continue
            key = {"escape": "Esc", "ctrl+n": "^N"}.get(keys.split(",")[0], keys.split(",")[0])
            x0 = x
            x += put(scr, h - 1, x, f" {key} ", STYLE["a"] | STYLE["b"] | STYLE["sel"], w - x)
            x += put(scr, h - 1, x, f" {label}  ", 0, w - x)
            self.footer.append((x0, x, top, name))
        for row, (text, error, _) in enumerate(reversed(self.toasts[-4:])):
            text = f" {text} "[:max(w - 4, 1)]
            put(scr, h - 3 - 2 * row, max(w - width(text) - 2, 0), text, STYLE["toasterr" if error else "toast"])
        if top.cursor_pos:
            curses.curs_set(1)
            try:
                scr.move(*top.cursor_pos)
            except curses.error:
                pass
        else:
            curses.curs_set(0)
        scr.noutrefresh()
        curses.doupdate()
