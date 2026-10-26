# moodle-tui

Terminal-Oberfläche für RWTHmoodle – Kurse, Fristen, Mitteilungen und Kursinhalte ansehen, ohne Browser.
Nur Python-Standardbibliothek (curses, http.client, html.parser), keine Abhängigkeiten.

## Installation

Keine Abhängigkeiten, also auch kein venv nötig:

```sh
git clone https://github.com/Lord3n20/moodle-tui ~/.local/share/moodle-tui/src
```

Startskript `~/.local/bin/moodle`:

```sh
#!/bin/sh
PYTHONPATH="$HOME/.local/share/moodle-tui/src" exec python3 -m moodle_tui "$@"
```

Aktualisieren: `git -C ~/.local/share/moodle-tui/src pull`. (Alternativ geht weiterhin `pip install git+https://github.com/Lord3n20/moodle-tui`.)

Für den Login den Link-Handler `~/.local/share/applications/moodle-tui-login.desktop` anlegen:

```ini
[Desktop Entry]
Type=Application
Name=Moodle-Anmeldung
Exec=moodle handle-url %u
MimeType=x-scheme-handler/moodlemobile;
NoDisplay=true
```

und registrieren: `xdg-mime default moodle-tui-login.desktop x-scheme-handler/moodlemobile`.

## Benutzung

- `moodle login` – Anmeldung über RWTH-SSO im Browser; das Token landet in `~/.config/moodle-tui/token`.
- `moodle` – Übersicht (Kurse mit ● für Neues, Fristen, Mitteilungen), Enter öffnet einen Kurs, `o` im Browser.
- `c` – optional: Chat mit Claude über den Kursinhalt. Nur aktiv, wenn die `claude`-CLI installiert ist; sonst (oder mit `MOODLE_TUI_CHAT=0`) fehlt die Taste einfach.
- `moodle dump …` – rein lesender Zugriff für Skripte.

Dateien werden nach `~/.cache/moodle-tui/files` geladen und in der TUI angezeigt: Bilder und PDF-Seiten als Bild
(kitty; JPEG & Co. über `vipsthumbnail` oder `ffmpeg`, PDF über `pdftoppm`), sonst PDF-Text (`pdftotext`) bzw. Textdateien.
Andere Programme öffnen sich nur mit `o` (Standardprogramm/Browser) oder per Klick auf einen Link.

Bedienung mit Tastatur oder Maus (Klick, Mausrad, Links und Fußzeile anklickbar). Bilder in Beschreibungen
zeigt kitty direkt an (PNG, über das Grafikprotokoll); sonst steht dort „🖼 Name“ – ein Klick öffnet das Bild.

Tests: `python tests/fake_run.py ORDNER` (Testdaten), `tests/real_run.py` und `tests/chat_run.py` (echtes Konto),
alle per tmux ferngesteuert.
