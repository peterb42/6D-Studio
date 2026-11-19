# Nachbearbeitung: Bildverwaltung, PixInsight, Siril und andere Programme

Stand 19.11.2026 (Siril 1.4.5 geprüft). Teil 1 fasst die Recherche zu den üblichen Programmen zusammen, Teil 2 zeigt, was PixInsight
(WBPP) und Siril tatsächlich erwarten, Teil 3 beschreibt die Bildverwaltung von 6D Studio.

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
| **PixInsight** | ja | Lizenz | Profi-Werkzeug; Vorverarbeitung mit dem Skript WBPP (Weighted Batch Preprocessing) | WBPP-Ordner mit Light/Dark/Flat/Bias + Listen; „Öffnen mit“ |
| **GraXpert** | ja | frei | Gradienten entfernen und KI-Entrauschen gestapelter Bilder; auch per Kommandozeile | „Öffnen mit“ (FITS, TIFF, PNG) |
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
- **Ergebnisse** – alle Live-Stacks und Siril-Ergebnisse (auch fertig entwickelte JPEG/TIFF) aller Sitzungen,
  neueste zuerst.
- **Sitzungen** `Astro/<Nacht>_<Objekt>/` mit Lights, Darks, Bias, Flats, Dark-Flats, Live-Stack,
  Siril-Ergebnissen und Aussortierten.
- **Einzelaufnahmen** (Auslösen, Bulb) aus dem Fotoordner, je Nacht.
- **Kalibrier-Bibliothek** (Masters als FITS).

RAW und JPEG gleichen Namens sind ein Eintrag, ebenso FITS und PNG eines Live-Stacks.

### Nachbearbeitung in drei Schritten

Ist eine Sitzung gewählt, steht oben ihr Ablauf. Ein Haken zeigt, was erledigt ist.

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
   - **Mit Siril stapeln** legt den Arbeitsordner an und lässt Siril (`siril-cli`) im Hintergrund laufen.
     Darüber erscheint der Fortschritt mit Schritt (z. B. „Schritt 7/9: Lights ausrichten · 71 %“).
   - Danach werden die Zwischenbilder (`process/`) gelöscht, bei 69 Lights der 6D rund 40 GB. Masters und
     Ergebnis bleiben.
   - Alternativ **Für PixInsight** (WBPP-Ordner) oder **Nur Skript** (zum Ausführen in Siril selbst).
3. **Fertig entwickeln**
   - Für jeden Stapel der Sitzung (Siril-Ergebnis oder Live-Stack). Siril entfernt Hintergrund und Gradient
     (`subsky`), den Grünstich (`rmgreen`), streckt automatisch (`autostretch`) und speichert JPEG und
     16-Bit-TIFF als `<Stapel>_fertig` daneben.
   - Das fertige Bild öffnet sich gleich groß. **Öffnen …** gibt den Stapel an Siril oder PixInsight zur
     Feinarbeit.

In der Gruppe „Ergebnisse“ geht „Fertig entwickeln“ auch über die Leiste: ein FITS auswählen.

**Ansehen:** Vorschaubilder entstehen beim ersten Mal und liegen in `.vorschau/thumbs/`:
- JPEG und PNG in etwa 0,1 s;
- RAW ohne JPEG über sips (macOS) in etwa 0,7 s;
- FITS über numpy in etwa 0,3 s.

Doppelklick zeigt ein Bild groß. Darunter stehen Aufnahmedaten aus EXIF bzw. FITS-Kopf: Zeit, ISO, Belichtung,
Sensortemperatur, Brennweite, Blende, Objektiv, Dateien.

**Auswählen:** Klick wählt aus oder ab, Shift-Klick einen Bereich, „Alle“ alles.

**Aktionen der Leiste:**

| Aktion | Was passiert |
|---|---|
| **Fertig entwickeln** | bei gestapelten FITS (Gruppe „Ergebnisse“, Live-Stack, Siril-Ergebnisse) – wie Schritt 3 |
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
  Light/  TenneMilkyway_20261119-001437_ISO800_3s_f4_0001_LIGHT.CR2   …
  Dark/   …_DARK.CR2        (Darks und Dark-Flats)
  Flat/   …_FLAT.CR2
  Bias/   …_BIAS.CR2
  lights.txt darks.txt flats.txt biases.txt darkflats.txt   Originalpfade, einer je Zeile
  bilder.csv      Typ, Datei, Zeit, ISO, Belichtung, Temperatur, Blende, Brennweite, Objektiv, Herkunft
  LIESMICH.txt    Schritte in WBPP und was enthalten ist
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
- Ein neuer Export ersetzt den Ordner; einen Ordner gleichen Namens, der nicht von 6D Studio stammt, rührt er
  nicht an.

In WBPP: **+ Directory** → `pixinsight` wählen, bei den Lights „CFA images“ an, **Run**.

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
- **Offen:**
  - WBPP mit dem Export ist noch nicht in PixInsight gelaufen. Geprüft ist die Typ-Erkennung mit der Regel aus
    dem WBPP-Quelltext.
  - „Öffnen mit“ startet die Programme mit `open -a`; ausprobiert in den Tests nur mit einem Ersatz.

## Quellen

- PixInsight 1.9.6, WBPP-Quelltext: `src/scripts/BatchPreprocessing/BPP-Helper.js` (`imageTypeFromKeyword`,
  `getImageTypeFromPath`, `getExposureTimeFromPath`), `BPP-StackEngine.js` (Reihenfolge Kopf → Pfad, XISF-Masters)
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
