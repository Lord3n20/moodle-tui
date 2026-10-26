# moodle-tui

Terminal-Oberfläche (Textual) für RWTHmoodle – Kurse, Fristen, Mitteilungen und Kursinhalte ansehen, ohne Browser.

## Installation

```sh
python -m venv ~/.local/share/moodle-tui/venv
~/.local/share/moodle-tui/venv/bin/pip install -r requirements.txt
```

Startskript `~/.local/bin/moodle`:

```sh
#!/bin/sh
cd "$HOME/moodle-tui" && exec "$HOME/.local/share/moodle-tui/venv/bin/python" -m moodle_tui "$@"
```

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
- `c` – Chat mit Claude über den Kursinhalt (braucht die `claude`-CLI).
- `moodle dump …` – rein lesender Zugriff für Skripte.

Dateien werden nach `~/.cache/moodle-tui/files` geladen und mit `xdg-open` geöffnet.
