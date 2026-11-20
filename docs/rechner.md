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

Getestet ist der Dienst auf dem Mac (mit dem echten PixInsight 1.9.6, siehe unten). Die Linux-Teile – `xvfb-run`,
`/opt/PixInsight`, systemd – folgen den Unterlagen von PixInsight und Ubuntu, sind aber auf Linux noch nicht
gelaufen.

1. **Ubuntu Server 24.04 LTS** (ohne Desktop), Benutzer anlegen, SSH an.
2. **NVIDIA-Treiber:** `sudo ubuntu-drivers install`, neu starten, `nvidia-smi` zeigt die Karte.
3. **PixInsight für Linux** aus dem Softwareverteilungssystem von PixInsight (eigenes Konto) laden, entpacken und
   mit dem mitgelieferten Installer nach `/opt/PixInsight` installieren. Die Lizenz gilt für alle eigenen Rechner
   (EULA §5: „You can install and use the Software on any computer that you own“). Einmal mit Oberfläche starten
   (Monitor oder NoMachine), um die Lizenz einzutragen und zu sehen, dass alles läuft. Fehlen Bibliotheken, nennt
   PixInsight sie beim Start – mit `apt` nachinstallieren.
4. **Xvfb** für PixInsight ohne Bildschirm: `sudo apt install xvfb`. Der Dienst startet PixInsight dann selbst in
   `xvfb-run`, solange `DISPLAY` nicht gesetzt ist (abschalten mit `--kein-xvfb`).
5. **Tailscale** auf PC und Mac: auf Linux nach [tailscale.com/download/linux](https://tailscale.com/download/linux),
   dann `sudo tailscale up`; auf dem Mac die Tailscale-App. `tailscale ip -4` zeigt die Adresse (100.x.y.z). Über
   Tailscale ist der PC auch unterwegs erreichbar (iPhone-Hotspot), ohne Portfreigabe im Router.
6. **6D Studio auf den PC:** `git clone <Repository> ~/6D-Studio` – der Dienst braucht nur Python 3.9 oder neuer
   (bei Ubuntu dabei), keine weiteren Pakete.
7. **Dienst starten** – zuerst von Hand:
   ```
   python3 ~/6D-Studio/rechner/6d-rechner.py --daten ~/6d-rechner
   ```
   Er meldet Adresse, PixInsight, Grafikkarte und freien Platz und legt beim ersten Start den Schlüssel in
   `~/6d-rechner/token` an. Dann dauerhaft als Dienst des Benutzers (Datei `rechner/6d-rechner.service`):
   ```
   mkdir -p ~/.config/systemd/user && cp ~/6D-Studio/rechner/6d-rechner.service ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user enable --now 6d-rechner
   sudo loginctl enable-linger $USER
   journalctl --user -u 6d-rechner -f
   ```

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
- **Adresse:** `http://100.x.y.z:8090` (Tailscale-Adresse des PCs) oder `http://<Name im Tailnet>:8090`;
- **Schlüssel:** Inhalt von `~/6d-rechner/token` auf dem PC;
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

WBPP und „Fertig entwickeln“ rechnen mit dem Prozessor – hier hilft der PC mit mehr Kernen, mehr RAM und schneller
NVMe-SSD (bei 12 Lights entstehen 7,8 GB Zwischenbilder). Die RTX 4090 nutzen die KI-Werkzeuge:

- **RC-Astro** BlurXTerminator, NoiseXTerminator, StarXTerminator (kostenpflichtig; auf Linux nur in PixInsight):
  brauchen TensorFlow mit CUDA – siehe [RC-Astro FAQ](https://www.rc-astro.com/frequently-asked-questions/); ein
  [Skript aus der Gemeinschaft](https://github.com/orangetw0/rcastro-gpuaccel) richtet es ein (vorher lesen).
- **MLDenoise** (PixInsight 1.9.6, MachineLearning-Modul): ONNX Runtime mit CUDA, braucht eine Modelldatei.

Noch nicht im Ablauf: `6d-fertig.js` nutzt bisher nur Prozesse ohne Grafikkarte. Sind BXT/NXT oder ein
MLDenoise-Modell auf dem PC eingerichtet, kommen sie als Schritte dazu (nur auf dem Rechner, wo die Karte ist).

## Platz und Aufräumen

- Pro Sitzung bleiben auf dem PC die Rohbilder (für einen neuen Lauf), die Ergebnisse, die Masters des letzten Laufs
  (`wbpp/master/`) und die Protokolle (`wbpp/logs/`). Zwischenbilder löscht der Dienst nach jedem Lauf, auch nach
  einem Absturz; vor jedem Lauf leert er `wbpp/` bis auf die Protokolle (WBPP überschreibt keine Masters, sondern
  legt `_(1)`-Kopien daneben).
- Ganze Sitzungen löschen: `rm -r ~/6d-rechner/sitzungen/<Ablage>_<Sitzung>` oder
  `DELETE /api/sessions/<Sitzung>`.

## Sicherheit

- Der Dienst hört von sich aus nur auf der Tailscale-Adresse (sonst nur auf 127.0.0.1) – nicht im Heimnetz, nicht
  im Internet. Den Port nicht im Router freigeben.
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

- **Geprüft auf dem Mac (M1):** Dienst mit dem echten PixInsight 1.9.6 auf `127.0.0.1:8090`, Studio über die
  Oberfläche (Chrome ohne Fenster, über das DevTools-Protokoll geklickt): einrichten, 62 Rohbilder (1,2 GB)
  hochladen, WBPP auf dem Rechner mit Fortschritt, Ergebnis zurück (2 min 36 s); zweiter Lauf: nichts mehr
  hochgeladen, 10 nicht passende Bias auf dem Rechner gelöscht; fertig entwickeln auf dem Rechner. Tests:
  `tests/test_rechner.py` (Dienst und Anbindung), `tests/test_simulator.py` 9f (Studio ↔ Dienst).
- **Absturz von PixInsight:** Ein Lauf von WBPP stürzte auf dem Mac mitten in der lokalen Normalisierung ab
  (SIGABRT in `LocalNormalizationThread`, Modul ImageCalibration – ein Fehler in PixInsight); in `wbpp/master/`
  lagen da noch die Masters des Laufs davor. Derselbe Lauf mit geleertem `wbpp/` ging durch. Das Studio und der
  Dienst melden das jetzt mit dem Schritt („PixInsight ist bei „Lokal normalisieren“ abgestürzt (Signal 6)“), löschen
  die Zwischenbilder und versuchen es bei der lokalen Normalisierung einmal ohne sie – mit Warnung im Auftrag.
- **Offen:**
  - Linux ist nicht ausprobiert (kein PC hier): Xvfb, `/opt/PixInsight/bin/PixInsight.sh`, systemd-Dienst.
  - Die Grafikkarte nutzt der Ablauf noch nicht (siehe oben).
  - Kein Abbrechen eines laufenden Auftrags (weder im Studio noch auf dem Rechner).
  - Laufende Vorschauen (alle N Bilder ein schnelles Ergebnis vom Rechner neben dem Live-Stack) – geplant.
