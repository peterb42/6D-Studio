# Rechner: PixInsight auf einem zweiten Rechner

Stand 20.11.2026. Der Mac bleibt die Station an der Kamera (Aufnahme, Live-Stack, Bildverwaltung). Die schwere
Arbeit – WBPP und „Fertig entwickeln“ mit PixInsight – kann ein zweiter Rechner übernehmen, z. B. ein Linux-PC mit
RTX 4090 und 64 GB RAM. Auf ihm läuft **6D Rechner** (`rechner/6d-rechner.py`), ein kleiner Dienst ohne
Oberfläche; das Studio schickt ihm die Rohbilder und holt die Ergebnisse zurück.

```
 Mac: 6D Studio                       Tailscale (WireGuard)        PC: 6D Rechner (Python, systemd)
 ┌───────────────────────────┐   ─────────────────────────────▶  ┌──────────────────────────────────────┐
 │ Bilder → Sitzung          │   Rohbilder hoch (nur fehlende)    │ ~/6d-rechner/sitzungen/<Sitzung>/    │
 │ „Nachbearbeiten mit       │   Auftrag: WBPP / Fertig entw.     │   Rohbilder/  wbpp/  wbpp_*.xisf     │
 │  Rechner“                 │   Fortschritt jede Sekunde         │ PixInsight -n --automation-mode      │
 │ pixinsight/wbpp_*.xisf  ◀─┼── Ergebnis zurück                  │   (Linux: in xvfb-run)               │
 └───────────────────────────┘                                    └──────────────────────────────────────┘
```

## Was passiert

- **Auf dem Rechner stapeln:** Das Studio legt den Export für WBPP an (`pixinsight/Rohbilder/`, dieselben passenden
  Kalibrierbilder wie beim Stapeln auf dem Mac) und gleicht ihn mit dem Rechner ab: Fehlendes oder Geändertes geht
  hoch, was dort nicht mehr dazugehört (aussortiert, andere ISO), wird gelöscht. Dann läuft WBPP auf dem Rechner –
  derselbe Ablauf, dieselben Skripte wie auf dem Mac. Der Fortschritt erscheint im Studio wie sonst
  („Schritt 21/31: Lokal normalisieren · 12 Lights“). Das Master-Light kommt nach `pixinsight/wbpp_<n>x<Zeit>s.xisf`,
  die Zwischenbilder löscht der Rechner.
- **Auf dem Rechner entwickeln:** Der Stapel geht hoch, wenn er dort noch fehlt (ein WBPP-Ergebnis vom Rechner ist
  schon da), PixInsight entwickelt ihn fertig (`6d-fertig.js`), JPEG und 16-Bit-TIFF kommen neben den Stapel.
- **Vorab hochladen (wahlweise):** Alle 20 s schickt das Studio neue Rohbilder der laufenden Sitzungen zum Rechner –
  nur, was der Export auch nähme. Beim Stapeln fehlen dann nur noch die letzten Bilder. Während der Rechner an der
  Sitzung arbeitet, wartet das Vorab-Hochladen.
- **Ein Auftrag zur Zeit.** PixInsight nutzt ohnehin alle Kerne.

Die Übertragung läuft über HTTP durch den Dienst, nicht über rsync: eine Verbindung, keine SSH-Schlüssel,
Fortschritt im Studio, abgebrochene Dateien gehen beim nächsten Abgleich neu hoch (der Rechner schreibt erst in
`*.part` und benennt dann um). Verschlüsselt ist sie durch Tailscale.

## Einrichten – Linux-PC

So eingerichtet am 23.11.2026 auf „talos“ (Ubuntu 24.04.5, Ryzen 5 5600X, 64 GB, RTX 4090) – über SSH vom Mac aus.

1. **Ubuntu 24.04** mit SSH; der Mac meldet sich mit Schlüssel an (`ssh-copy-id -i ~/.ssh/id_ed25519.pub
   peter@<pc>`). Mit Passphrase am Schlüssel: `ssh -o UseKeychain=yes …` nimmt sie aus dem Schlüsselbund.
2. **NVIDIA-Treiber:** `sudo ubuntu-drivers install`, neu starten, `nvidia-smi` zeigt die Karte (auf talos schon da:
   595.99, CUDA 13.2).
3. **Pakete:** `sudo apt install -y xvfb x11vnc apt-file`.
4. **PixInsight für Linux** (GPU-Ausgabe, `PI-linux-x64-gpu-1.9.6-….tar.xz`, 2 GB) aus dem Softwareverteilungssystem
   von PixInsight laden, auf den PC kopieren, entpacken und installieren – der Installer kennt nur `/opt/PixInsight`:
   ```
   mkdir -p ~/pi-setup && tar -xf PI-linux-x64-gpu-*.tar.xz -C ~/pi-setup && cd ~/pi-setup && sudo ./installer -y
   ```
   Die GPU-Ausgabe bringt CUDA 12.6 und cuDNN 9.10 mit; auf talos fehlte keine Bibliothek (Prüfung:
   `LD_LIBRARY_PATH=/opt/PixInsight/bin/lib ldd /opt/PixInsight/bin/PixInsight | grep "not found"`). Die Lizenz gilt
   für alle eigenen Rechner (EULA §5).
5. **Lizenz eintragen** – einmal mit Oberfläche, auch ohne Bildschirm am PC: PixInsight auf einem virtuellen
   Bildschirm, dazu ein VNC-Server nur auf dem PC selbst, ein SSH-Tunnel und die Bildschirmfreigabe des Macs:
   ```
   # PC
   x11vnc -storepasswd <Einmalpasswort> ~/.vnc-pass
   Xvfb :5 -screen 0 1600x1000x24 -nolisten tcp &
   DISPLAY=:5 /opt/PixInsight/bin/PixInsight.sh &
   x11vnc -display :5 -localhost -rfbport 5905 -rfbauth ~/.vnc-pass -forever &
   # Mac
   ssh -N -L 5905:localhost:5905 peter@<pc> &   open vnc://localhost:5905
   ```
   Lizenz eintragen, PixInsight beenden, VNC, Xvfb und Tunnel beenden. Die Lizenz liegt danach in
   `~/.PixInsight/PixInsightLicense` – der Dienst muss unter demselben Benutzer laufen.
6. **Test ohne Bildschirm:** ein Skript mit `-r=` starten (ohne `-r` beendet `--force-exit` PixInsight nicht):
   `xvfb-run -a /opt/PixInsight/bin/PixInsight.sh -n --automation-mode -r=<test.js> --force-exit` – auf talos nach 7 s
   wieder zu.
7. **Daten auf die schnelle SSD:** WBPP schreibt je Light der 6D etwa 650 MB Zwischenbilder. Auf talos liegt `/` auf
   einer Festplatte (7200 U/min), die NVMe-SSD ist als `/boot` eingehängt – deshalb die Daten in `/boot/6d-rechner`
   (`sudo mkdir /boot/6d-rechner && sudo chown peter: /boot/6d-rechner`).
8. **6D Studio auf den PC:** `git clone https://github.com/peterb42/6D-Studio.git ~/6D-Studio` – der Dienst braucht
   nur Python 3.9 oder neuer, keine weiteren Pakete.
9. **Dienst** (systemd-Dienst des Benutzers), Adresse im Heimnetz und Datenordner als Zusatzdateien:
   ```
   mkdir -p ~/.config/systemd/user/6d-rechner.service.d
   cp ~/6D-Studio/rechner/6d-rechner.service ~/.config/systemd/user/
   printf "[Service]\nEnvironment=RECHNER_ADRESSE=192.168.0.144\n" > ~/.config/systemd/user/6d-rechner.service.d/adresse.conf
   printf "[Service]\nExecStart=\nExecStart=/usr/bin/python3 %%h/6D-Studio/rechner/6d-rechner.py --daten /boot/6d-rechner\n" \
     > ~/.config/systemd/user/6d-rechner.service.d/daten.conf
   systemctl --user daemon-reload && systemctl --user enable --now 6d-rechner
   sudo loginctl enable-linger $USER
   sudo ufw allow from 192.168.0.0/24 to any port 8090 proto tcp
   journalctl --user -u 6d-rechner -f
   ```
   Der Schlüssel steht in `/boot/6d-rechner/token`.
10. **Unterwegs (später):** Tailscale auf PC und Mac
    ([tailscale.com/download/linux](https://tailscale.com/download/linux), `sudo tailscale up`, auf dem Mac die App) und
    `RECHNER_ADRESSE` auf die Tailscale-Adresse (100.x.y.z) – oder `auto`.

**Einstellungen des Dienstes** (Aufruf oder Umgebung):

| Angabe | Umgebung | Vorgabe |
|---|---|---|
| `--daten` | `RECHNER_DATEN` | `~/6d-rechner` (Sitzungen, Schlüssel) |
| `--port` | `RECHNER_PORT` | 8090 |
| `--adresse` | `RECHNER_ADRESSE` | `auto`: die Tailscale-Adresse (wartet beim Hochfahren bis 60 s darauf), sonst 127.0.0.1 |
| `--pixinsight` | `RECHNER_PIXINSIGHT` | Linux `/opt/PixInsight/bin/PixInsight.sh`, Mac aus `/Applications` |
| `--name` | – | Rechnername |
| Schlüssel | `RECHNER_TOKEN` | `<daten>/token`, beim ersten Start erzeugt |

Ein Mac (z. B. ein Mac mini mit PixInsight) geht genauso – dort ohne Xvfb.

## Einrichten – im Studio

**Bilder** → eine Sitzung → in der Zeile „Nachbearbeiten mit“ **+ Rechner**:
- **Adresse:** im Heimnetz `http://192.168.0.144:8090` (talos), über Tailscale `http://100.x.y.z:8090`;
- **Schlüssel:** Inhalt von `<daten>/token` auf dem PC (talos: `/boot/6d-rechner/token`);
- **Neue Rohbilder schon während der Serie hochladen** – wahlweise.

**Speichern und prüfen** zeigt, was der Rechner hat: PixInsight mit WBPP, Kerne, RAM, Grafikkarte, freien Platz.
Danach gibt es **Rechner** als drittes Programm neben PixInsight und Siril; die Zeile zeigt den Zustand
(„● pc-4090 · RTX 4090 · 1.2 TB frei · vorab oben: 34/36“). Die Einstellungen liegen in
`~/.config/6d-studio/rechner.json` (nur für den Besitzer lesbar). Der Schlüssel geht nie an die Oberfläche zurück.

## Datenmengen

Eine CR2 der 6D hat rund 20 MB (Beispielsitzung: 62 Rohbilder = 1,2 GB).

| Verbindung | etwa | 1,2 GB | ein Light |
|---|---|---|---|
| zu Hause, Kabel (1 Gbit/s) | 110 MB/s | 11 s | 0,2 s |
| zu Hause, WLAN | 20–40 MB/s | 30–60 s | 1 s |
| Mobilfunk-Upload (20 Mbit/s) | 2,5 MB/s | 8 min | 8 s |

Unterwegs lohnt das Vorab-Hochladen bei längeren Belichtungen (ein Light je Minute: 0,3 MB/s). Bei Serien mit
3-s-Bildern fällt mehr an, als Mobilfunk schafft – dann zu Hause stapeln. Ist der Mac per WLAN mit der Kamera
verbunden, braucht er fürs Internet einen zweiten Weg (iPhone per USB, Ethernet-Adapter).

## Die Grafikkarte

WBPP rechnet mit dem Prozessor – dort hilft der PC mit RAM und schneller SSD. Die RTX 4090 nutzt **MLDenoise**
(PixInsight 1.9.6, KI-Entrauschen mit ONNX Runtime und CUDA):

- **Modell:** Datei `MLDenoise_v42.xmlm` (596 MB) aus dem Softwareverteilungssystem von PixInsight – sie gehört nicht
  zu PixInsight selbst. Auf dem Rechner in `<daten>/modelle/` legen (talos: `/boot/6d-rechner/modelle/`); der Dienst
  nimmt das neueste (höchste Version im Namen) und meldet es in `/api/status` (`mldenoise`) und beim Start.
- **Im Ablauf:** „Auf dem Rechner entwickeln“ entrauscht das lineare Bild nach der Farbkalibrierung, vor dem
  Strecken. Auf diesem Mac nur, wenn ein Modell in `~/.config/6d-studio/modelle/` liegt (dort ~2 min).
- **Gemessen** an einem 50-Light-Master der 6D (5450×3547, RGB):

  | Wo | Zeit |
  |---|---|
  | RTX 4090 (CUDA) | 18,6 s (erster Lauf mit Laden des Modells 23 s), 2,5 GB Grafikspeicher |
  | M1, CoreML | 109 s |
  | Ryzen 5 5600X, nur CPU | 345 s |

  Rauschen im Himmelshintergrund (MAD, grün, linear): 1,4 → 0,4. RTX 4090 und M1 liefern dasselbe Ergebnis.
  „Auf dem Rechner entwickeln“ vom Mac aus, mit Hochladen des Masters: 41 s.
- **Grafikspeicher:** MLDenoise braucht etwa 2,5 GB. Läuft auf dem PC ein anderes Programm, das die Karte füllt
  (auf talos z. B. ein Sprachmodell-Server mit 22,5 GB), schlägt das Entrauschen fehl – das Bild wird dann ohne
  Entrauschen fertig, mit Warnung im Auftrag.
- **RC-Astro** (BlurXTerminator, NoiseXTerminator, StarXTerminator) ginge auf Linux nur in PixInsight mit
  TensorFlow und CUDA – siehe [RC-Astro FAQ](https://www.rc-astro.com/frequently-asked-questions/); nicht eingerichtet
  (keine Lizenz).

## Platz und Aufräumen

- Pro Sitzung bleiben auf dem PC die Rohbilder (für einen neuen Lauf), die Ergebnisse, die Masters des letzten Laufs
  (`wbpp/master/`) und die Protokolle (`wbpp/logs/`). Zwischenbilder löscht der Dienst nach jedem Lauf, auch nach
  einem Absturz; vor jedem Lauf leert er `wbpp/` bis auf die Protokolle (WBPP überschreibt keine Masters, sondern
  legt `_(1)`-Kopien daneben).
- Ganze Sitzungen löschen: `rm -r <daten>/sitzungen/<Ablage>_<Sitzung>` (talos: `/boot/6d-rechner/…`) oder
  `DELETE /api/sessions/<Sitzung>`.

## Sicherheit

- Der Dienst hört von sich aus nur auf der Tailscale-Adresse (sonst nur auf 127.0.0.1). Im Heimnetz (talos:
  `RECHNER_ADRESSE=192.168.0.144`) lässt die Firewall nur das eigene Netz an Port 8090 (`ufw allow from
  192.168.0.0/24 …`). Den Port nicht im Router freigeben.
- Jede Anfrage braucht den Schlüssel (`Authorization: Bearer …`, Vergleich in konstanter Zeit). Pfade bleiben im
  Sitzungsordner (`..`, versteckte und leere Teile werden abgelehnt).
- HTTP selbst ist unverschlüsselt – die Verschlüsselung macht Tailscale (WireGuard).

## Schnittstelle

| Anfrage | Zweck |
|---|---|
| `GET /api/status` | Name, PixInsight, WBPP, Kerne, RAM, Grafikkarte, freier Platz, laufender Auftrag |
| `GET /api/sessions/<S>/files` | Dateien der Sitzung mit Größe und Zeit |
| `PUT /api/sessions/<S>/files/<Pfad>` | Datei hochladen (`X-Mtime` setzt die Änderungszeit) |
| `GET /api/sessions/<S>/files/<Pfad>` | Datei laden |
| `DELETE /api/sessions/<S>/files/<Pfad>`, `DELETE /api/sessions/<S>` | Datei bzw. Sitzung löschen |
| `POST /api/jobs` `{"kind": "wbpp", "session": S}` | WBPP über `Rohbilder/` |
| `POST /api/jobs` `{"kind": "finish", "session": S, "rel": Pfad}` | fertig entwickeln |
| `GET /api/jobs/<id>` | Fortschritt: `steps`, `index`, `step`, `detail`, `warnings`, `result`, `error` |

## Geprüft und offen

- **Geprüft auf talos (Ubuntu 24.04, Ryzen 5 5600X, RTX 4090) vom Mac aus:**
  - WBPP über das Studio mit 12 Lights, 20 Darks, 30 Bias: 62 Rohbilder (1,2 GB) in 12 s hoch.
    | Daten von WBPP | Zeit |
    |---|---|
    | M1 MacBook (SSD) | 146 s |
    | talos, Festplatte | 172 s – Debayern 45 s, Ausrichten 40 s |
    | talos, im RAM (Obergrenze für die SSD) | 89 s – Debayern 7 s, Ausrichten 6 s |
  - Fertig entwickeln mit MLDenoise auf der RTX 4090 (siehe oben).
  - Dienst als systemd-Dienst des Benutzers mit `xvfb-run`, Firewall, Neustart.
- **Geprüft auf dem Mac (M1):** Dienst lokal mit dem echten PixInsight 1.9.6, Studio über die Oberfläche (Chrome ohne
  Fenster): einrichten, hochladen, WBPP, Ergebnis zurück; zweiter Lauf ohne neues Hochladen, nicht passende Bias auf
  dem Rechner gelöscht. Tests: `tests/test_rechner.py`, `tests/test_simulator.py` 9f.
- **Absturz von PixInsight:** Ein Lauf von WBPP stürzte auf dem Mac mitten in der lokalen Normalisierung ab
  (SIGABRT in `LocalNormalizationThread`, Modul ImageCalibration – ein Fehler in PixInsight); in `wbpp/master/`
  lagen da noch die Masters des Laufs davor. Derselbe Lauf mit geleertem `wbpp/` ging durch. Das Studio und der
  Dienst melden das jetzt mit dem Schritt, löschen die Zwischenbilder und versuchen es bei der lokalen
  Normalisierung einmal ohne sie – mit Warnung im Auftrag.
- **Hinweis:** PixInsight setzt `LC_ALL=en_US.utf8`; fehlt die Sprachumgebung, warnt es bei jedem Start (läuft
  trotzdem) – `sudo locale-gen en_US.UTF-8` beseitigt die Warnung.
- **Offen:**
  - WBPP mit den Daten auf der NVMe-SSD von talos messen (erwartet: nahe an den 89 s im RAM).
  - Kein Abbrechen eines laufenden Auftrags (weder im Studio noch auf dem Rechner).
  - Laufende Vorschauen (alle N Bilder ein schnelles Ergebnis vom Rechner neben dem Live-Stack) – geplant.
