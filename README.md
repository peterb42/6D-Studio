# 6D Studio

WLAN-Fernsteuerung für die **Canon EOS 6D** vom Mac aus, ohne EOS Utility – gebaut für Nachtaufnahmen
des Sternenhimmels: Livebild mit 5×/10×-Lupe der Kamera, Schärfe-Messung am Stern, Bulb Start/Stopp mit Timer,
ISO/Blende/Zeit, automatischer RAW-Download.

```
 Browser ──HTTP :8066──┐
                       ├──  6d-studio (Python)  ──PTP/IP :15740 (WLAN)──  Canon EOS 6D
 gphoto2 ─ptpip:127.0.0.1┘   hält EINE Verbindung offen
```

## Benutzen

1. iPhone: Persönlicher Hotspot an, **„Maximale Kompatibilität“** an. Mac in den Hotspot.
2. Kamera: Wi-Fi-Funktion → **Fernsteuerung (EOS Utility)**. Geht EOS Utility auf dem Mac auf → beenden.
3. Terminal:

```bash
6d-fernsteuerung studio
```

Die Oberfläche öffnet sich unter <http://127.0.0.1:8066>. Weitere Befehle: `6d-fernsteuerung` ohne Argument.
Fotos landen in `~/Pictures/6D-Tethering`, das Protokoll in `~/.gphoto/6d-proxy.log`.

## Projekt

| Pfad | Inhalt |
|---|---|
| `studio/6d-studio.py` | Server: PTP/IP zur Kamera, Vermittler für gphoto2, Web-API |
| `studio/6d-studio.html` | Bedienoberfläche (HTML/CSS/JS, keine Abhängigkeiten) |
| `bin/6d-fernsteuerung` | Kommandozeilen-Werkzeug (zsh) – startet/stoppt das Studio, gphoto2-Befehle |
| `tools/ptpip-handshake.py` | Anmeldung/Kopplung direkt an der Kamera testen (90 s Wartezeit) |
| `tools/ptp-caps.py` | Unterstützte Befehle und Eigenschaften auslesen (über das laufende Studio) |
| `tools/ptp-probe.py` | Einzelne PTP-Befehle über das Studio schicken |
| `tools/legacy/` | Erster Vermittler, ersetzt durch `6d-studio` |
| `tests/` | Tests ohne Kamera (`python3 -m unittest discover -s tests -v`) |
| `docs/canon-6d-ptpip.md` | **Alles, was wir über das Protokoll der 6D herausgefunden haben** |

Nur Python-Standardbibliothek, läuft mit dem System-Python (`/usr/bin/python3`, 3.9+).

## Entwickeln

```bash
./install.sh
```

legt Symlinks in `~/.local/bin` auf dieses Projekt an – Änderungen wirken sofort:

- **Oberfläche** (`6d-studio.html`): Browser neu laden (Cmd+R).
- **Server** (`6d-studio.py`): `6d-fernsteuerung stop` und `6d-fernsteuerung studio`.
  Danach muss an der Kamera die Fernsteuerung neu gestartet werden (die 6D beendet sie beim Trennen).

In VS Code (Cmd+Shift+P → „Tasks: Run Task“): Studio starten/stoppen, Log verfolgen, Tests, Installieren.
Debuggen: Startkonfiguration „6D Studio (Debug)“ – stoppt vorher das Hintergrund-Studio.

## Erstes Koppeln

Nur nötig, wenn `~/.gphoto/settings` keine `ptp2_ip=guid=`-Zeile hat oder die Kamera zurückgesetzt wurde:

1. GUID erzeugen und eintragen (gphoto2-Format, 16 Bytes mit Doppelpunkten).
2. Kamera: Fernsteuerung (EOS Utility) → „Kopplung starten“.
3. `tools/ptpip-handshake.py 172.20.10.3` – wartet bis 90 s; fragt die Kamera nach, mit OK bestätigen.
