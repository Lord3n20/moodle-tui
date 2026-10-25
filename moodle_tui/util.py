"""Formatierung, die Oberfläche und `moodle dump` gemeinsam nutzen."""

from datetime import datetime

import html2text

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def html_to_md(html):
    if not html:
        return ""
    h = html2text.HTML2Text()
    h.body_width = 0
    h.ignore_images = True
    return h.handle(html).strip()


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
