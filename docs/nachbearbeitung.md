# Nachbearbeitung: Bildverwaltung, PixInsight, Siril und andere Programme

Stand 25.11.2026 (Siril 1.4.5 und PixInsight 1.9.6 mit WBPP 3.1.2 und MLDenoise v42 geprüft). Teil 1 fasst die
Recherche zu den üblichen Programmen zusammen, Teil 2 zeigt, was PixInsight (WBPP) und Siril tatsächlich erwarten und
wie das Studio sie ohne Oberfläche steuert, Teil 3 beschreibt die Bildverwaltung von 6D Studio. Was auf dem Mac und
was auf dem Rechner (talos) läuft: [rechner.md – Was wo läuft](rechner.md#was-wo-läuft).

## 1. Der übliche Ablauf und die Programme

Astroaufnahmen werden in zwei Stufen bearbeitet:

1. **Vorverarbeitung** – Kalibrieren (Bias, Darks, Flats), Debayern, Ausrichten, Stapeln (Integration). Ergebnis:
   ein lineares, rauscharmes Bild (FITS/XISF). Das macht der Live-Stack schon grob, für das Endbild aber besser
   Siril, PixInsight (WBPP) oder Astro Pixel Processor – mit Ausreißerverwerfung und Gewichtung.
2. **Nachbearbeitung** – Hintergrund/Gradienten entfernen, Farbkalibrierung, Entrauschen, Strecken (nichtlinear),
   Sterne verkleinern oder getrennt bearbeiten, Feinschliff.

| Programm | Mac | Kosten | Wofür | Anbindung in 6D Studio |
|---|---|---|---|---|
| **Siril** | ja | frei | Komplett: Kalibrieren, Stapeln, Hintergrund, Farbe, Strecken; per Skript ohne Oberfläche (`siril-cli`) | Arbeitsordner + Skript, auf Knopfdruck ausführen; „Öffnen mit“ |
| **PixInsight** | ja | Lizenz | Profi-Werkzeug; Vorverarbeitung mit dem Skript WBPP (Weighted Batch Preprocessing) | WBPP ohne Oberfläche auf Knopfdruck, Fertig entwickeln mit eigenem Skript (mit MLDenoise) – auf dem Mac oder dem Rechner; WBPP-Ordner mit Light/Dark/Flat/Bias + Listen; „Öffnen mit“ |
| **GraXpert** | ja | frei | Gradienten entfernen und KI-Entrauschen gestapelter Bilder; auch per Kommandozeile | „Öffnen mit“ (FITS, XISF, TIFF, PNG) |
| **Starry Landscape Stacker** | nur Mac | App Store | Milchstraße mit Vordergrund: Himmel ausrichten, Landschaft scharf lassen | „Öffnen mit“ für die JPEGs (laut älterer Quelle kein RAW) |
| **Astro Pixel Processor** | ja | Lizenz | Stapeln, Mosaike, Lichtverschmutzung | „Öffnen mit“ |
| **StarTools** | ja | Lizenz | Nachbearbeitung gestapelter Bilder | „Öffnen mit“ |
| **Affinity Photo / Photoshop** | ja | Lizenz | Bildbearbeitung nach dem Stapeln; Affinity mit eigener Astro-Stapel-Ansicht | „Öffnen mit“ |
| Sequator, DeepSkyStacker | nein (Windows) | frei | Stapeln (Sequator: Landschaft, DSS: Deep Sky) | – |

Für die Milchstraße über der Tenne (Weitwinkel, Vordergrund) passt Starry Landscape Stacker; für Deep-Sky-Felder
Siril oder PixInsight. Gradienten (Horizont, Lichtverschmutzung) entfernt GraXpert am gestapelten Bild.

## 2. Was PixInsight und Siril erwarten

### PixInsight – WBPP

Nachgelesen im Quelltext von WBPP, der mit PixInsight 1.9.6 ausgeliefert wird
(`/Applications/PixInsight/src/scripts/BatchPreprocessing/`):

- **Bildtyp:** zuerst der FITS-Schlüssel `IMAGETYP` (`bias`, `dark`, `darkflat`/`flatdark` → Dark, `flat`, `light`,
  mit „master“ davor als Master), `BPP-Helper.js`. Fehlt er – wie bei CR2 –, nimmt WBPP den Typ aus dem **Pfad**:
  `BIAS`, `DARK(S)`, `FLAT(S)`, `LIGHT(S)` zählen nur als eigenes Wort (`_BIAS_`, `/lights/`), und **das letzte
  im Pfad gewinnt** (`getImageTypeFromPath`). Ohne Treffer nimmt WBPP ein Light an.
  Folge für unsere Namen: `DARKFLAT_…` und `darkflats/` erkennt WBPP nicht, `biases/` auch nicht (nur `BIAS`), und
  ein Objekt namens „Flats“ würde den Typ überschreiben.
- **Belichtungszeit** liest WBPP aus dem Pfad nur, wenn die Metadaten keine haben (`BPP-StackEngine.js`).
- **Masters** nimmt WBPP nur als **XISF** – FITS-Masters werden abgelehnt („master calibration files must be in
  XISF format“). Unsere Bibliothek ist FITS; für WBPP gibt 6D Studio deshalb die **Rohbilder** weiter, WBPP baut
  die Masters selbst.
- **Dark-Flats** sind für WBPP Darks; es ordnet sie über die Belichtungszeit den Flats zu.

### PixInsight ohne Oberfläche (Automatikbetrieb)

Nachgelesen in `doc/docs/CommandLineInterface` und im WBPP-Quelltext (`BPP-Main.js`, `BPP-Automation.js`,
`BPP-OperationQueue.js`, `examples/event-script-example.js`), dann ausprobiert:

- **Aufruf:** `PixInsight -n --automation-mode --no-focus-stealing -r=<Skript>,<Arg>,<Arg> --force-exit`
  (macOS: `PixInsight.app/Contents/MacOS/PixInsight`, Linux: `/opt/PixInsight/bin/PixInsight.sh`). `-n` startet
  eine neue Instanz – eine offene PixInsight-Oberfläche stört nicht. Die Argumente nach dem Skript kommen am Komma
  getrennt in `jsArguments`; ein Pfad darf deshalb kein Komma enthalten.
- **WBPP:** `-r=…/BatchPreprocessing/WBPP.js,automationMode=true,dir=<Ordner>,outputDirectory=<Ordner>,…`.
  `dir=` liest rekursiv. Weitere Werte als `name=wert` (Liste: `BPP-Automation.js`, `printAutomationHelp`).
  Im Automatikbetrieb liest WBPP die Einstellungen seiner Oberfläche **nicht** – es gelten die Voreinstellungen
  (Gewichtung PSF Signal, lokale Normalisierung, Autocrop, Debayer-Muster aus der RAW).
- **Fortschritt:** Ein **Ereignisskript** (`usePipelineScript=true,pipelineScriptFile=…`) ruft WBPP vor und nach
  jedem Schritt auf. `cout(…)` schreibt auf die Standardausgabe von PixInsight (die Konsole in PixInsight sieht
  das nicht) – so liest das Studio den Fortschritt: `studio/pixinsight/6d-wbpp-ereignisse.js`. Ein Fehler im
  Ereignisskript hält WBPP nicht an.
- **Rückgabewert** ist auch nach einem Fehler im Skript 0. Ob es geklappt hat, sagen die Ereigniszeilen und die
  Ausgabedateien; bricht WBPP vor dem Lauf ab (Prüfung), steht der Grund in `<Ausgabe>/logs/*.log`
  („*** Error“).
- **Astrometrie** (`platesolve`) braucht die Gaia-Datenbank von PixInsight. Ohne sie schlägt der Schritt fehl
  (WBPP fragt dann einen Online-Katalog mit Koordinaten 0/0) – das Studio schaltet sie deshalb aus
  (`platesolve=false`). Ohne Astrometrie gibt es auch keine SPCC-Farbkalibrierung.
- **Ausgabe:** `master/` (Masters, `masterLight_…_RGB.xisf` mit Ausreißerkarten und `…_autocrop.xisf`
  beschnitten), `calibrated/`, `debayered/`, `registered/` (je Light der 6D zusammen etwa 650 MB), `logs/`
  (Protokoll, SmartReport). Autocrop braucht die Ausreißerkarten, ihr Ausschalten wird ignoriert.
- **XISF** der Masters: unkomprimiert, planar, Float32 0…1, die FITS-Schlüssel stehen im XML-Kopf (`IMAGETYP`,
  `EXPTIME`, `ISOSPEED`, `CCD-TEMP`, `DATE-OBS`, `BAYERPAT`, …); dahinter ein zweites Bild (Maske vom Beschneiden).
  Die Zahl der gestapelten Lights steht nicht im Kopf.
- **FITS:** PixInsight beachtet `ROWORDER` beim Lesen (unsere Live-Stacks stehen richtig) und schreibt selbst
  `ROWORDER = 'TOP-DOWN'`. Gleitkomma-FITS außerhalb 0…1 skaliert es beim Öffnen auf 0…1 (Minimum…Maximum).
- **Siril 1.4.5 liest XISF** (mit libXISF gebaut) – richtig herum, geprüft an den Plejaden und einem Staubkorn.
- **Prozesse für das Fertig-Entwickeln** (Standardwerte aus `toSource()`): GradientCorrection (ohne Proben, für
  lineare Bilder), BackgroundNeutralization, ColorCalibration (Weiß aus den Sternen des ganzen Bilds),
  MultiscaleAdaptiveStretch (neu in 1.9.6, Hintergrund 0,15, ohne Bereich = ganzes Bild), SCNR.
- **MLDenoise** (MachineLearning-Modul, ONNX Runtime) braucht eine Modelldatei, die nicht zu PixInsight gehört:
  `MLDenoise_v42.xmlm` (596 MB) aus dem Softwareverteilungssystem von PixInsight. `6d-fertig.js` nimmt sie als
  drittes Argument (`new MLDenoise; modelPath = …`) und entrauscht das lineare Bild nach ColorCalibration, vor dem
  Strecken. Rechnet auf dem M1 mit CoreML (109 s für ein ganzes 6D-Bild), unter Linux mit CUDA (RTX 4090: 19 s).
  Ist der Grafikspeicher belegt, rechnet es ohne Meldung auf dem Prozessor (Ryzen 5 5600X: ~6 min). Schlägt es
  ganz fehl, meldet das Skript eine Warnung und entwickelt ohne Entrauschen fertig.

### Siril

- Die mitgelieferten Skripte (`OSC_Preprocessing.ssf` für Farbkameras/DSLR) erwarten im Arbeitsordner `lights`,
  `darks`, `flats`, `biases` – genau die Ordner der Serien von 6D Studio. Ablauf: Bias stapeln → Flats mit Bias
  kalibrieren und mit `-norm=mul` stapeln → Darks stapeln → Lights kalibrieren (`-cc=dark -cfa -equalize_cfa
  -debayer`) → `register` → `stack … -norm=addscale -output_norm -rgb_equal`.
- `convert` nimmt **alle** Bilder eines Ordners – liegen RAW und JPEG zusammen (RAW+JPEG an der Kamera), mischt es
  sie. Deshalb ein eigener Arbeitsordner nur mit RAWs.
- Ohne Oberfläche: `siril-cli -d <Arbeitsordner> -s <Skript>` (Handbuchseite Siril 1.4.4: `-d` setzt den
  Arbeitsordner, `-s` führt ein Skript ohne Oberfläche aus).
- Siril liest unsere FITS-Masters direkt (32 Bit, Werte 0…1 = ADU/65535). Der Master-Dark enthält den Offset (wie
  Sirils eigener `dark_stacked`), der Master-Flat ist ohne Offset (wie `pp_flat_stacked`).

## 3. Bildverwaltung in 6D Studio

Oben **Anzeige: Bilder** (Taste <kbd>B</kbd>). Links die Quellen, rechts die Bilder als Vorschau.

**Ablagen:** Die Bildverwaltung zeigt immer die Kamera-Ablage (`~/Pictures/6D-Tethering`) **und** die
Simulator-Ablage (`…/Simulator`) – die des laufenden Studios zuerst. So sind die Live-Stacks der Kamera auch im
Simulator-Studio zu sehen.

**Quellen je Ablage:**
- **Ergebnisse** – alle Live-Stacks, Siril- und PixInsight-Ergebnisse (auch fertig entwickelte JPEG/TIFF) aller
  Sitzungen, neueste zuerst.
- **Sitzungen** `Astro/<Nacht>_<Objekt>/` mit Lights, Darks, Bias, Flats, Dark-Flats, Live-Stack,
  Siril-Ergebnissen, PixInsight-Ergebnissen und Aussortierten.
- **Einzelaufnahmen** (Auslösen, Bulb) aus dem Fotoordner, je Nacht.
- **Kalibrier-Bibliothek** (Masters als FITS).

RAW und JPEG gleichen Namens sind ein Eintrag, ebenso FITS und PNG eines Live-Stacks.

### Nachbearbeitung in drei Schritten

Ist eine Sitzung gewählt, steht oben ihr Ablauf. Ein Haken zeigt, was erledigt ist. Darüber: **Nachbearbeiten mit
PixInsight | Siril | Rechner** – das Programm für Stapeln und Fertig entwickeln (nur installierte; die Wahl merkt
sich der Browser, ohne Wahl PixInsight). **Rechner** ist PixInsight auf einem zweiten Rechner, z. B. einem Linux-PC
mit Grafikkarte: siehe [rechner.md](rechner.md).

1. **Prüfen**
   - Anzeige: wie viele Lights; welche Kalibrierbilder passen (Darks, Bias, Flats, Dark-Flats; nicht passende
     werden genannt); Hinweise auf Fehlendes (z. B. „Keine Flats – Vignettierung bleibt“).
   - **Lights prüfen** (etwa 0,5 s je Light) arbeitet wie der Live-Stack: Sterne finden, jedes Light einem
     Bildfeld zuordnen. Das größte Feld ist das Hauptfeld.
   - Gemeldet werden Lights mit **anderem Bildausschnitt**, mit wenigen Sternen (Wolken, Tau), mit unscharfen
     Sternen (Wind, Fokus) oder mit hellem Himmel.
   - **N aussortieren** verschiebt sie nach `verworfen/`, **zeigen** wählt sie im Raster aus.
   - An der Beispielsitzung: 50 im Hauptfeld, 19 ab Bild 0038 mit anderem Ausschnitt.
2. **Stapeln**
   - **Mit PixInsight stapeln** legt `pixinsight/Rohbilder/` an (wie „Ordner für WBPP“) und startet WBPP ohne
     Oberfläche: kalibrieren, debayern, bewerten und gewichten, ausrichten, lokal normalisieren, stapeln,
     Ränder beschneiden. Darüber der Fortschritt mit den Schritten von WBPP auf Deutsch (z. B. „Schritt 24/31:
     Lokal normalisieren · 12 Lights · 3.2 s“). Ergebnis: `pixinsight/wbpp_<Anzahl>x<Zeit>s.xisf` (das
     beschnittene Master-Light). Danach werden die Zwischenbilder gelöscht – bei 12 Lights 7,8 GB; die Masters
     (`wbpp/master/`) und Protokolle (`wbpp/logs/`) bleiben. Schritte, die WBPP als fehlgeschlagen meldet,
     stehen als Warnung im Auftrag.
   - **Mit Siril stapeln** legt den Arbeitsordner an und lässt Siril (`siril-cli`) im Hintergrund laufen.
     Darüber erscheint der Fortschritt mit Schritt (z. B. „Schritt 7/9: Lights ausrichten · 71 %“).
     Danach werden die Zwischenbilder (`process/`) gelöscht, bei 69 Lights der 6D rund 40 GB. Masters und
     Ergebnis bleiben.
   - Dazu **Ordner für WBPP** (nur die Ordner, zum Starten in PixInsight von Hand) und **Siril-Skript** (zum
     Ausführen in Siril selbst).
   - Dauer auf einem M1 (8 Kerne, 16 GB): WBPP etwa 10 s je Light plus 1 s je Kalibrierbild (12 Lights, 20 Darks,
     30 Bias: 2 min 30 s), Siril etwa 2,6 s je Light.
3. **Fertig entwickeln** – für jeden Stapel der Sitzung (PixInsight- oder Siril-Ergebnis, Live-Stack):
   - **PixInsight** (`studio/pixinsight/6d-fertig.js`, ohne Oberfläche): GradientCorrection,
     BackgroundNeutralization, ColorCalibration, MLDenoise (nur mit Modell: auf dem Mac in
     `~/.config/6d-studio/modelle/`, auf dem Rechner in `<daten>/modelle/`), MultiscaleAdaptiveStretch, SCNR;
     speichert JPEG (Qualität 92) und 16-Bit-TIFF als `<Stapel>_fertig-pi` daneben. Etwa 20 s für ein ganzes
     6D-Bild, 9 s für einen Live-Stack (halbe Größe); mit Entrauschen auf dem M1 gut 2 min, auf dem Rechner 41 s.
   - **Siril** entfernt Hintergrund und Gradient (`subsky`), den Grünstich (`rmgreen`), streckt automatisch
     (`autostretch`) und speichert JPEG und 16-Bit-TIFF als `<Stapel>_fertig` daneben. Etwa 4 s.
   - Beide gehen mit FITS und XISF. Beim Stapel steht, womit er fertig entwickelt ist („fertig ✓ PixInsight,
     Siril“) – nur, wenn das fertige Bild neuer ist als der Stapel.
   - Das fertige Bild öffnet sich gleich groß. **Öffnen …** gibt den Stapel an das gewählte Programm zur
     Feinarbeit.
   - Der Hintergrund von PixInsight ist dunkler (MultiscaleAdaptiveStretch: 0,15) als der von Siril
     (`autostretch`: 0,25).

In der Gruppe „Ergebnisse“ geht „Fertig entwickeln“ auch über die Leiste: ein FITS oder XISF auswählen.

**Ansehen:** Vorschaubilder entstehen beim ersten Mal und liegen in `.vorschau/thumbs/`:
- JPEG und PNG in etwa 0,1 s;
- RAW ohne JPEG über sips (macOS) in etwa 0,7 s;
- FITS über numpy in etwa 0,3 s;
- XISF (z. B. WBPP-Master) über einen eigenen Leser (`imaging.read_xisf`: unkomprimiert oder zlib, auch mit
  Byte-Shuffle) in etwa 0,2 s für ein ganzes 6D-Bild.

Doppelklick zeigt ein Bild groß. Darunter stehen Aufnahmedaten aus EXIF bzw. FITS-Kopf: Zeit, ISO, Belichtung,
Sensortemperatur, Brennweite, Blende, Objektiv, Dateien.

**Auswählen:** Klick wählt aus oder ab, Shift-Klick einen Bereich, „Alle“ alles.

**Aktionen der Leiste:**

| Aktion | Was passiert |
|---|---|
| **Mit PixInsight / Siril entwickeln** | bei gestapelten FITS oder XISF (Gruppe „Ergebnisse“, Live-Stack, Siril- und PixInsight-Ergebnisse) – wie Schritt 3, mit dem gewählten Programm |
| **Öffnen mit …** | in einem installierten Programm (gesucht in `/Applications`, auch eine Ebene tiefer wie bei PixInsight). Angeboten wird nur, was mit den Dateien umgehen kann; bei RAW+JPEG bekommt z. B. Starry Landscape Stacker das JPEG. |
| **Finder** | zeigt die Datei im Finder |
| **Aussortieren / Zurücklegen** | verschiebt Bilder (RAW und JPEG) nach `<Sitzung>/verworfen/<Ordner>/` und zurück. Export und Siril lassen sie weg. Nichts wird gelöscht. |
| **In Sitzung übernehmen …** | Einzelaufnahmen als Light (oder Kalibrierbild) einer Sitzung: verschieben, nach der Namenskonvention benennen (Zeit, ISO, Belichtung und Blende aus dem EXIF), in `serie.csv` eintragen (Spalte „geladen“: „übernommen“). Die Nummern laufen mit den Serien fort. |

### FITS von unten nach oben (wie Siril)

Siril liest RAWs Zeile für Zeile **von unten** ein (`image_formats_libraries.c`, `for row = height-1 … 0`) und
dreht FITS beim Laden nicht – `ROWORDER` ist für Siril nur eine Angabe. 6D Studio schrieb FITS bis 19.11.2026
von oben (`ROWORDER = TOP-DOWN`): In Siril standen Live-Stacks deshalb auf dem Kopf, und ein Master aus der
Bibliothek hätte gespiegelt über den Lights gelegen (Hotpixel und Staub an der falschen Stelle).

- **Jetzt:** Daten von unten, `ROWORDER = BOTTOM-UP`, `BAYERPAT` wie von oben gesehen – genau Sirils Standard.
  Geprüft mit Siril 1.4.5: Ein Dark, mit 6D Studio geschrieben, liegt Pixel für Pixel wie Sirils eigene
  Umwandlung desselben CR2.
- **Ältere Dateien** liest 6D Studio weiter richtig. Bevor Siril sie benutzt (Master im Skript, Live-Stack beim
  Fertig-Entwickeln), dreht das Studio sie an Ort und Stelle (`imaging.to_bottom_up`). Kopf und Werte bleiben
  dabei gleich.

### Für PixInsight: `<Sitzung>/pixinsight/`

```
pixinsight/
  Rohbilder/                Ziel für „+ Directory“ in WBPP
    Light/  TenneMilkyway_20261119-001437_ISO800_3s_f4_0001_LIGHT.CR2   …
    Dark/   …_DARK.CR2      (Darks und Dark-Flats)
    Flat/   …_FLAT.CR2
    Bias/   …_BIAS.CR2
    lights.txt darks.txt flats.txt biases.txt darkflats.txt   Originalpfade, einer je Zeile
    bilder.csv              Typ, Datei, Zeit, ISO, Belichtung, Temperatur, Blende, Brennweite, Objektiv, Herkunft
  wbpp/                     Arbeitsordner von WBPP, wenn das Studio stapelt
    master/                 Masters (Bias, Dark, Flat) als XISF
    logs/                   Protokoll, SmartReport
  wbpp_12x3.2s.xisf         Ergebnis von WBPP (beschnittenes Master-Light)
  wbpp_12x3.2s_fertig-pi.jpg / .tif   mit PixInsight fertig entwickelt (Siril: _fertig)
  LIESMICH.txt              Schritte in WBPP, Ordner, was enthalten ist
```

- Die Dateien sind **harte Verknüpfungen** – kein zusätzlicher Platz, Originale bleiben, wo sie sind.
- Der Typ steht als **letztes Wort** im Namen. So erkennt WBPP ihn sicher, auch bei „DARKFLAT“ oder einem Objekt
  „Flats“ (die Tests prüfen jeden Namen mit der WBPP-Regel, nach Python übertragen).
- **Nur passende Kalibrierbilder:**
  - Bias und Darks mit der ISO der Lights, Darks auch mit ihrer Zeit (±10 %);
  - Flats mit gleicher Optik;
  - Dark-Flats mit ISO und Zeit der Flats (±25 %).

  Der Rest wird weggelassen und in `LIESMICH.txt` genannt. In der Beispielsitzung bleiben so 10 Bias mit ISO 3200
  draußen, die sonst einen falschen Master-Bias ergäben.
- **Fehlt ein Kalibriertyp** in der Sitzung, nimmt der Export die Rohbilder, aus denen der passende Master der
  Bibliothek entstanden ist (über `SOURCE`/`FIRSTIMG` im FITS-Kopf). So nutzt auch WBPP die Dark-Bibliothek.
- Ein neuer Export ersetzt `Rohbilder/` und `LIESMICH.txt`; Ergebnisse und `wbpp/` bleiben. Einen Ordner
  gleichen Namens, der nicht von 6D Studio stammt, rührt er nicht an.

Von Hand in WBPP: **+ Directory** → `pixinsight/Rohbilder` wählen (nicht `pixinsight/` – dort liegen auch
Ergebnisse und Masters), bei den Lights „CFA images“ an, **Run**.

### Siril: `<Sitzung>/siril/`

- `lights/ darks/ flats/ biases/ darkflats/` mit den passenden RAWs (verknüpft, ohne JPEGs) und
  `vorverarbeitung.ssf`. Das Skript folgt `OSC_Preprocessing.ssf` aus Siril 1.4 (`requires 1.3.4`, `-32b`); vor
  jedem Schritt steht `# >> <Schritt>` für die Fortschrittsanzeige.
- **Fehlende Kalibrierbilder:**
  - Mit Dark-Flats werden die Flats mit ihnen statt mit dem Bias kalibriert.
  - Fehlen Rohbilder eines Typs, nimmt das Skript die Rohbilder des passenden Masters aus einer anderen Sitzung,
    sonst den Master selbst (FITS, Siril liest ihn direkt).
  - Fehlen Bias und Dark ganz, dient der Schwarzwert als Offset (`-bias="=2047"`).
- Frühere Ergebnisse (`ergebnis_*.fit`) bleiben beim Neuanlegen erhalten.

### Geprüft und offen

- **Geprüft mit Siril 1.4.5:**
  - Stapeln von 8 echten Lights mit Darks und Bias über das Studio: 21 s; alle 9 Schritte erkannt (Siril meldet
    auf Deutsch, die Schrittmarken im Skript sind sprachunabhängig); 5,1 GB Zwischenbilder gelöscht.
  - Fertig entwickeln eines Siril-Ergebnisses und eines älteren Live-Stacks (dieser wird dabei gedreht), je
    unter 2 s.
  - Die Meldung „error: no suitable data in src fits“ nach `subsky` ist harmlos (interne Kopie in Siril); das
    Ergebnis ist richtig.
- **Geprüft mit PixInsight 1.9.6 (WBPP 3.1.2) auf einem M1:**
  - WBPP über das Studio mit 12 echten Lights, 20 Darks und 30 Bias (10 Bias mit ISO 3200 blieben draußen):
    2 min 30 s, alle Schritte erkannt, Master-Light als `wbpp_12x3.2s.xisf`, 7,8 GB Zwischenbilder gelöscht.
    Typ-Erkennung (Light/Dark/Bias am Dateinamen) und Debayer-Muster (RGGB aus der CR2) stimmen.
  - Fertig entwickeln mit PixInsight: WBPP-Master 20 s, älterer Live-Stack (FITS, `TOP-DOWN`) 9 s, beide richtig
    herum. Mit Siril: dasselbe XISF in 4 s.
  - In der Oberfläche (Chrome ohne Fenster, über das DevTools-Protokoll geklickt): Programmwahl, Fortschritt,
    Ergebnis mit „Mit PixInsight entwickeln“, XISF-Vorschau, keine Fehler in der Konsole.
- **Offen:**
  - **SPCC** (Farbkalibrierung über Spektren der Gaia-Sterne) und **Astrometrie** brauchen die Gaia-Datenbank
    von PixInsight (Download über das PixInsight-Konto). Ist sie installiert, kann `6d-fertig.js` ImageSolver und
    SPCC statt ColorCalibration nehmen – noch nicht eingebaut, weil hier nicht prüfbar.
  - **RC-Astro** (BlurXTerminator, NoiseXTerminator) bräuchte eine Lizenz – nicht eingebaut; entrauscht wird mit
    MLDenoise.
  - Ein laufender Auftrag lässt sich nicht abbrechen; hängt PixInsight (z. B. an einer Lizenzmeldung – die zeigt
    es auch im Automatikbetrieb), hilft nur, PixInsight zu beenden.
  - Mehrere Light-Gruppen (z. B. zwei Belichtungszeiten) ergeben mehrere Master-Lights; das Studio nimmt das
    neueste beschnittene, die anderen bleiben in `wbpp/master/`.
  - „Öffnen mit“ startet die Programme mit `open -a`; ausprobiert in den Tests nur mit einem Ersatz.

## Quellen

- PixInsight 1.9.6, WBPP-Quelltext: `src/scripts/BatchPreprocessing/BPP-Helper.js` (`imageTypeFromKeyword`,
  `getImageTypeFromPath`, `getExposureTimeFromPath`), `BPP-StackEngine.js` (Reihenfolge Kopf → Pfad, XISF-Masters),
  `BPP-Main.js` und `BPP-Automation.js` (Automatikbetrieb), `BPP-OperationQueue.js` und
  `examples/event-script-example.js` (Ereignisskript)
- PixInsight 1.9.6, `doc/docs/CommandLineInterface` (Argumente des Programms, `-n`, `-r`, `--automation-mode`,
  `--force-exit`), `doc/tools/GradientCorrection`, `doc/tools/MultiscaleAdaptiveStretch`
- XISF 1.0: [Spezifikation](https://pixinsight.com/doc/docs/XISF-1.0-spec/XISF-1.0-spec.html) (auch in
  `doc/docs/XISF-1.0-spec`)
- PixInsight-Forum: [Frame-Typ aus dem Dateinamen](https://pixinsight.com/forum/index.php?topic=4405.0),
  [Bildtyp automatisch erkennen](https://pixinsight.com/forum/index.php?threads/batch-preprocessing-script-autodetect-image-type.11082/)
- Siril: [Skripte](https://siril.readthedocs.io/en/stable/scripts/Script-files.html),
  [Handbuchseite siril/siril-cli](https://www.mankier.com/1/siril),
  [OSC_Preprocessing.ssf](https://gitlab.com/free-astro/siril/-/raw/master/scripts/OSC_Preprocessing.ssf),
  [Bash-Skripte, Programmpfad auf dem Mac](https://siril.org/tutorials/bash-scripts/)
- GraXpert: [GitHub](https://github.com/Steffenhir/GraXpert), [PyPI (Kommandozeile)](https://pypi.org/project/graxpert/)
- Programmübersichten: [Night Sky Pix – Software](https://nightskypix.com/best-image-processing-software-for-astrophotography/),
  [AstroBackyard](https://astrobackyard.com/image-processing-software/),
  [Mac Observatory – Mac-Astro-Software](https://mac-observatory.squarespace.com/mac-astronomy-software)
- Milchstraße mit Vordergrund: [Starry Landscape Stacker (App Store)](https://apps.apple.com/gb/app/starry-landscape-stacker/id550326617?mt=12),
  [PetaPixel: Milky Way stacking](https://petapixel.com/2016/02/20/stack-photos-epic-milky-way-landscapes)
