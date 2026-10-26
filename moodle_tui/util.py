"""Formatierung, die Oberfläche und `moodle dump` gemeinsam nutzen."""

import re
import unicodedata
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def fmt_time(ts, relative=False):
    if not ts:
        return "–"
    d = datetime.fromtimestamp(ts)
    text = f"{WEEKDAYS[d.weekday()]} {d:%d.%m. %H:%M}"
    if relative:
        days = (d.date() - datetime.now().date()).days
        rel = {0: "heute", 1: "morgen", -1: "gestern"}.get(days, f"in {days} T" if days > 0 else f"vor {-days} T")
        text += f"  ({rel})"
    return text


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def char_width(c):
    if c < "̀":
        return 1
    if unicodedata.combining(c) or c in "​‍︎️":
        return 0
    return 2 if unicodedata.east_asian_width(c) in "WF" else 1


def width(text):
    """Spaltenbreite im Terminal (Emoji und CJK zählen doppelt)."""
    return len(text) if text.isascii() else sum(map(char_width, text))


# --------------------------------------------------------------------------- Text mit Auszeichnung
#
# Ein Doc ist eine Liste von Zeilen, jede Zeile eine Liste von Stücken (text, stil, link).
# Stil-Buchstaben: b fett, i kursiv, h Überschrift, c Code, d gedimmt, n neu, e Fehler,
# p Einzug (wird beim Umbruch wiederholt), r Trennlinie, I Bild (link = Bild-URL, text = Alt-Text).


class Doc:
    def __init__(self):
        self.lines = []

    def gap(self):
        if self.lines and self.lines[-1]:
            self.lines.append([])
        return self

    def line(self, *runs):
        self.lines.append([r if len(r) == 3 else (*r, None) for r in runs])
        return self

    def text(self, text, style=""):
        for part in str(text).split("\n"):
            self.lines.append([(part, style, None)] if part else [])
        return self

    def para(self, text, style=""):
        if text:
            self.gap()
            self.text(text, style)
        return self

    def heading(self, text):
        return self.gap().line((text, "h")) if text else self

    def html(self, html, base=None):
        lines = html_lines(html, base)
        if lines:
            self.gap()
            self.lines += lines
        return self

    def rule(self):
        self.gap()
        self.line(("─", "r"))
        self.lines.append([])
        return self

    def plain(self):
        return lines_to_text(self.lines)


class _Html(HTMLParser):
    BLOCK_GAP = {"p", "pre", "blockquote", "table", "ul", "ol", "dl", "figure",
                 "h1", "h2", "h3", "h4", "h5", "h6"}
    BLOCK = {"div", "section", "article", "header", "footer", "tr", "li", "dt", "dd", "figcaption"}
    SKIP = {"script", "style", "head", "title"}
    STYLE = {"b": "b", "strong": "b", "th": "b", "i": "i", "em": "i", "code": "c", "tt": "c", "kbd": "c",
             "pre": "c", **{f"h{n}": "h" for n in range(1, 7)}}

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.lines, self.line = [], []
        self.gap = 0          # Leerzeilen vor dem nächsten Text
        self.style = Counter()
        self.links = []
        self.lists = []       # None = Aufzählung, Zahl = nummerierte Liste
        self.bullet = None
        self.quote = self.pre = self.skip = self.cell = 0

    def url(self, href):
        return urljoin(self.base, href) if self.base and href else href

    def brk(self, gap=0):
        if self.line:
            self.lines.append(self.line)
            self.line = []
        if self.lines:
            self.gap = max(self.gap, gap)

    def emit(self, text, style=None, link=None):
        if not self.line:
            if not self.pre:
                text = text.lstrip()
            if not text:
                return
            if self.lines:
                self.lines += [[] for _ in range(self.gap)]
            self.gap = 0
            depth = max(len(self.lists) - 1, 0)
            if self.quote:
                self.line.append(("▌ " * self.quote, "dp", None))
            if self.bullet:
                self.line.append(("  " * depth + self.bullet, "p", None))
                self.bullet = None
            elif self.lists:
                self.line.append(("  " * (depth + 1), "p", None))
        elif not self.pre and text[:1] == " " and self.line[-1][0][-1:] == " ":
            text = text[1:]
        if text:
            style = "".join(k for k in "bihc" if self.style[k] > 0) if style is None else style
            self.line.append((text, style, link or (self.links[-1] if self.links else None)))

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if self.skip:
            return
        attrs = dict(attrs)
        if tag in self.BLOCK_GAP:
            self.brk(0 if tag in ("ul", "ol") and self.lists else 1)
        elif tag in self.BLOCK:
            self.brk()
        if tag in self.STYLE:
            self.style[self.STYLE[tag]] += 1
        if tag == "pre":
            self.pre += 1
        elif tag == "a":
            self.links.append(self.url(attrs.get("href")))
        elif tag == "ul":
            self.lists.append(None)
        elif tag == "ol":
            self.lists.append(0)
        elif tag == "li":
            if self.lists and self.lists[-1] is not None:
                self.lists[-1] += 1
                self.bullet = f"{self.lists[-1]}. "
            else:
                self.bullet = "•◦▪"[max(len(self.lists) - 1, 0) % 3] + " "
        elif tag == "blockquote":
            self.quote += 1
        elif tag == "br":
            if self.line:
                self.lines.append(self.line)
                self.line = []
            elif self.lines:
                self.lines.append([])
        elif tag == "hr":
            self.brk(1)
            self.lines.append([("─", "r", None)])
            self.brk(1)
        elif tag == "tr":
            self.cell = 0
        elif tag in ("td", "th"):
            if self.cell:
                self.emit(" │ ", "d", "")
            self.cell += 1
        elif tag == "img" and attrs.get("src"):
            self.brk()
            self.lines.append([(attrs.get("alt") or attrs.get("title") or "Bild", "I", self.url(attrs["src"]))])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(self.skip - 1, 0)
        if self.skip:
            return
        if tag in self.STYLE:
            self.style[self.STYLE[tag]] = max(self.style[self.STYLE[tag]] - 1, 0)
        if tag == "pre":
            self.pre = max(self.pre - 1, 0)
        elif tag == "a" and self.links:
            self.links.pop()
        elif tag in ("ul", "ol") and self.lists:
            self.lists.pop()
        elif tag == "blockquote":
            self.quote = max(self.quote - 1, 0)
        if tag in self.BLOCK_GAP:
            self.brk(0 if tag in ("ul", "ol") and self.lists else 1)
        elif tag in self.BLOCK:
            self.brk()

    def handle_data(self, data):
        if self.skip:
            return
        if not self.pre:
            self.emit(re.sub(r"\s+", " ", data))
            return
        for i, part in enumerate(data.split("\n")):
            if i:
                self.lines.append(self.line)
                self.line = []
            if part:
                self.emit(part)

    def result(self):
        self.close()
        self.brk()
        while self.lines and not self.lines[-1]:
            self.lines.pop()
        return self.lines


def html_lines(html, base=None):
    """HTML → Doc-Zeilen; base löst relative Links und Bildpfade auf."""
    if not html:
        return []
    parser = _Html(base)
    parser.feed(html)
    return parser.result()


def lines_to_text(lines):
    out = []
    for line in lines:
        parts = []
        for i, (text, style, link) in enumerate(line):
            if style == "r":
                text = "─" * 40
            elif style == "I":
                text = f"[Bild: {text}] ({link})"
            elif link and link not in text and (i + 1 == len(line) or line[i + 1][2] != link):
                text += f" ({link})"
            parts.append(text)
        out.append("".join(parts).rstrip())
    return "\n".join(out).strip()


def html_to_text(html, base=None):
    return lines_to_text(html_lines(html, base))
