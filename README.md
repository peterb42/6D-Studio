# 6D Studio

Fernsteuerung für die **Canon EOS 6D** vom Mac aus – per **WLAN oder USB-Kabel**, umschaltbar, ohne EOS Utility –
gebaut für Nachtaufnahmen des Sternenhimmels:
- Livebild mit 5×/10×-Lupe der Kamera, Schärfe-Messung am Stern, Fokusantrieb
- Bulb Start/Stopp mit Timer, ISO/Blende/Zeit, automatischer Download
- Serien für Stacking (Lights, Darks, Bias, Flats, Dark-Flats) mit Siril-gerechter Ablage
- **Live-Stack** mit Dark-Optimierung, Ausrichtung auch bei Bildfelddrehung und **Bildanalyse** mit Empfehlungen
- **Kalibrier-Bibliothek** (Bias, Darks, Flats, Dark-Flats) mit geführter Aufnahme und automatischer Auswahl
  passender Masters
- **Bildverwaltung** für Sitzungen, Einzelaufnahmen und Live-Stacks: ansehen, aussortieren, übernehmen, in
  installierten Programmen öffnen; **stapeln und fertig entwickeln mit PixInsight (WBPP) oder Siril** auf Knopfdruck –
  auch auf einem **zweiten Rechner** (z. B. Linux-PC mit Grafikkarte, über Tailscale)

```
 Browser ──HTTP :8066──┐                          ┌─ PTP/IP :15740 (WLAN) ──────────┐
                       ├──  6d-studio (Python) ───┤                                 ├── Canon EOS 6D
 gphoto2 ─ptpip:127.0.0.1┘   hält EINE Verbindung    └─ PTP über USB (ptpusb.py) ──────┘
                             offen – WLAN oder Kabel
```

## Schnellstart

**Mit Kabel:** Wi-Fi an der Kamera aus (die 6D schaltet USB ab, solange Wi-Fi an ist), Kamera per USB-Kabel an den
Mac, Kamera an, `6d-fernsteuerung usb`, dann `6d-fernsteuerung studio`.

**Über WLAN:**
1. iPhone: Persönlicher Hotspot an, **„Maximale Kompatibilität“** an. Mac in den Hotspot.
2. Kamera: Wi-Fi-Funktion → **Fernsteuerung (EOS Utility)**. Geht EOS Utility auf dem Mac auf → beenden.
3. Terminal (beim ersten Mal `6d-fernsteuerung wlan`, das sucht die Kamera im Netz):

```bash
6d-fernsteuerung studio
```

Die Oberfläche öffnet sich unter <http://127.0.0.1:8066> – immer mit dem **Kamerabild**, zum Einrichten. Weitere
Befehle: `6d-fernsteuerung` ohne Argument (siehe [Kommandozeile](#kommandozeile)). Erstes Mal? Siehe
[Erstes Koppeln](#erstes-koppeln) und [Entwickeln](#entwickeln) (venv für Live-Stack und Bibliothek).

**Typische Nacht:** Livebild an → Stern anklicken, Doppelklick (Lupe 10×), scharfstellen → Objektiv auf MF →
Live-Stack starten → Light-Serie starten → zwischendurch auf „Live-Stack“ schauen, Bildanalyse lesen → direkt
danach Darks über „Geführt aufnehmen …“ (gleiche Temperatur) → vor dem Abbauen Flats (Fokus und Blende nicht
anfassen) → Bias gern später zu Hause → am nächsten Tag in **Bilder** aussortieren und an PixInsight oder Siril
übergeben. Ausführlich: [docs/stacking-und-kalibrierung.md](docs/stacking-und-kalibrierung.md),
[docs/nachbearbeitung.md](docs/nachbearbeitung.md).

## Funktionen im Einzelnen

Die Oberfläche hat drei Bereiche: die **Kopfzeile**, links das **große Bild** (Kamera, Live-Stack oder Bilder) mit seinen
Werkzeugen, rechts die **Karten** (Belichtung, Bulb, Serie, Live-Stack, Bibliothek, Letztes Foto, Protokoll).
Einstellungen wie Serienfelder, Nachtmodus, Fadenkreuz und Live-Stack-Optionen merkt sich der Browser.

### Kopfzeile

| Element | Funktion |
|---|---|
| Verbindung | Grün mit Kameraname und „WLAN 172.20.10.3“ bzw. „USB-Kabel“, wenn die 6D angemeldet ist; sonst, was gerade passiert (Anmeldung über WLAN dauert ~4 s). |
| **WLAN \| Kabel** | Verbindungsart umschalten: Das Studio trennt und verbindet gleich neu (nicht während einer Belichtung oder Serie). Ein Klick auf „WLAN“, wenn WLAN schon gewählt ist, fragt nach einer anderen IP-Adresse. Die Wahl gilt auch für `6d-fernsteuerung` (`~/.gphoto/6d-verbindung`). Am Kabel: an der Kamera Wi-Fi aus. |
| Modus | Stellung des Moduswahlrads (M, B, Av …) – wichtig für Bulb, Serien und den Assistenten. |
| Fotos: … | Zielordner auf dem Mac. |
| **Nachtmodus** (<kbd>N</kbd>) | Alles in Rot – schont die Dunkelanpassung der Augen. |

### Großes Bild: Kamera, Live-Stack oder Bilder

**Anzeige: Kamera | Live-Stack | Bilder** schaltet, was das große Bild zeigt (<kbd>V</kbd> Kamera ↔ Live-Stack,
<kbd>B</kbd> Bilder). Beim Öffnen ist es immer die Kamera; zum Live-Stack wechselt die Anzeige **nur** per Klick
oder <kbd>V</kbd> – auch nicht beim Starten des Live-Stacks. Läuft ein Stapel, zeigt der Knopf die Bildanzahl
(„Live-Stack · 12“). **Bilder** ist die [Bildverwaltung](#bildverwaltung).

#### Livebild und Scharfstellen

| Element | Funktion |
|---|---|
| **Livebild** (<kbd>L</kbd>) | Livebild der Kamera ein/aus (mit Bildrate). Während einer Bulb-Belichtung pausiert es. |
| **Lupe 1× / 5× / 10×** (<kbd>1</kbd> <kbd>5</kbd> <kbd>0</kbd>) | Die Vergrößerung der Kamera selbst – echte Sensordetails, nicht hochgerechnet. |
| **Ausschnitt** ◀ ▲ ▼ ▶ (Pfeiltasten), **Mitte** | Verschiebt den vergrößerten Ausschnitt um ¼ seiner Breite; „Mitte“ zentriert und löscht das Ziel. |
| **Fadenkreuz** | Blendet ein Fadenkreuz ein – zum Ausrichten und Einrahmen. |
| Klick ins Bild (1×) | Setzt das **Ziel** (gelber gestrichelter Kreis), z. B. einen hellen Stern. |
| Doppelklick | Lupe 10× an dieser Stelle; in der Lupe zurück auf 1×. |
| **Grünes Quadrat** | Der Stern, an dem gemessen wird. Mit Ziel: der hellste Stern in dessen Nähe, und es bleibt bei ihm, solange er ähnlich hell ist. Ohne Ziel: der hellste Stern im ganzen Bild – springt bei ähnlich hellen Sternen. |
| **Stern-Ø** | Durchmesser des gemessenen Sterns bei halber Helligkeit in Pixeln – je kleiner, desto schärfer. Daneben der Bestwert („best“), „überbelichtet“ bei gesättigtem Stern, und ein Verlauf (tiefer = schärfer). ↺ setzt Verlauf und Bestwert zurück. |
| **Fokus** ⟪ ‹‹ ‹ · › ›› ⟫ (<kbd>,</kbd> <kbd>.</kbd> fein) | Fokusmotor des Objektivs, nah/fern in drei Schrittgrößen. Nur mit Livebild und **Objektivschalter auf AF**; bei MF lehnt die Kamera ab. Das Studio stellt nicht selbst scharf. |
| **Anzeige hell**, **Kontrast** | Nur für die Anzeige (dunkle Sterne sichtbar machen), ändert nichts am Foto. |

**So wird scharfgestellt:** Livebild an → hellen Stern anklicken → Doppelklick (10×) → mit ‹ › oder am Fokusring
drehen, bis Stern-Ø am kleinsten ist → **vor der Serie Objektiv auf MF** (verstellt den Fokus nicht; auf AF
versucht die 6D bei jeder Auslösung scharfzustellen und löst nachts oft nicht aus).

#### Live-Stack-Anzeige

| Element | Funktion |
|---|---|
| **Helligkeit**, **Schwarzpunkt**, **Auto** | Automatische Streckung wie Siril (Hintergrund auf 25 %, Schwarzpunkt Median − 2,8 · MAD); Auto stellt die Vorgabe wieder her. |
| **Hintergrund ebnen** (Vorgabe: an) | Gleicht Gradient (Horizont, Lichtverschmutzung) und Vignettierung für die Anzeige aus – sonst schneidet die Streckung dunkle Ecken schwarz ab. Helle Bereiche (Milchstraße, Lampen) bleiben erhalten. |
| **Speichern**, **Öffnen** | Speichert den Stapel jetzt (FITS + PNG) bzw. öffnet das zuletzt gespeicherte PNG. |

Alle Regler wirken nur auf Anzeige und PNG – das FITS bleibt linear.

### Belichtung

**ISO**, **Blende**, **Zeit**: Auswahl genau der Werte, die die Kamera gerade anbietet; Änderungen gehen sofort an
die Kamera, Änderungen an der Kamera erscheinen hier. Im Modus B bestimmt Start/Stopp die Zeit. Meldet das Objektiv
keine Blende (manuelles Objektiv), wird sie am Objektiv eingestellt.

### Bulb-Aufnahme und Auslösen

- **Start/Stopp** (Moduswahlrad auf **B**): Langzeitbelichtung mit laufender Uhr und Fortschrittsbalken.
- **Auto-Stopp nach** min/s oder per Vorwahl (manuell, 15 s … 8 min): Das Studio beendet die Belichtung selbst;
  „manuell“ belichtet bis Stopp.
- **Auslösen**: ein Bild mit der Zeit der Kamera (nicht im Modus B).
- Während einer Serie sind beide gesperrt.

### Serie für Stacking

Nimmt eine Folge gleichartiger Bilder auf und legt sie Siril-gerecht ab.

| Feld | Funktion |
|---|---|
| **Bildtyp** | Light, Dark, Bias, Flat, Dark-Flat – mit Hinweis, wie die Kamera stehen muss (Tabelle unten). |
| **Objekt** | Name für Ordner und Dateien, z. B. „Milchstraße“. |
| **Anzahl** | 1–999 Bilder. |
| **Belichtung** | Nur im Modus B (Lights, Darks); im Modus M gilt die Zeit der Kamera, sie wird angezeigt. |
| **Pause** | Wartezeit zwischen den Bildern (0–600 s). |
| **Auf den Mac** | alles · nur RAW · nur JPEG (schnell) · nichts (bleibt auf der Karte). Bei RAW+JPEG spart „nur RAW“ für den Live-Stack die halbe Wartezeit. |
| **Serie starten** / **Nach diesem Bild stoppen** | Zweiter Klick auf Stopp: sofort (eine laufende Bulb-Belichtung wird beendet und gespeichert). |

| Bildtyp | Moduswahlrad | Zeit |
|---|---|---|
| Light | B (Zeit im Studio) oder M | wie eingestellt |
| Dark | wie die Lights, Deckel drauf, Sucher abdecken | wie die Lights |
| Bias | M | Studio stellt die kürzeste Zeit (1/4000) ein |
| Flat | Av | Kamera bestimmt die Zeit; Fokus und Blende wie bei den Lights. Das Studio misst die Helligkeit jedes Flats und meldet beim ersten „gut“ oder „zu dunkel: +1…+2 EV“ (ideal 30–60 %). |
| Dark-Flat | M | Studio stellt ISO und Zeit der letzten Flats ein; Deckel drauf |

**Ablage:** `~/Pictures/6D-Tethering/Astro/<Nacht>_<Objekt>/{lights,darks,biases,flats,darkflats}/` – die
„Nacht“ wechselt mittags, Bilder nach Mitternacht landen also im Ordner des Abends. Dateinamen:
`LIGHT_Milchstrasse_20261118-231405_ISO3200_30s_f2.8_0001.CR2`. Jede Datei – auch die, die auf der Karte bleibt –
steht in `serie.csv` (Typ, Nummer, Kameradatei, Zeiten, ISO, Blende, geladen ja/nein). Die Nummern laufen fort,
auch wenn eine neue Serie startet, während noch Bilder der vorigen geladen werden.

Lights merkt sich das Studio (ISO, Zeit, Sensortemperatur) – für den Dark-Assistenten und die Bibliothek. Läuft
ein Live-Stack, kommt jedes geladene Light hinein.

### Live-Stack

Stapelt jedes Light, das auf den Mac kommt (Serie oder einzelne Aufnahme), sofort zu einem rauschärmeren Bild.
Angelehnt an **OpenLiveStacker** und **Siril**. Braucht numpy und rawpy (siehe [Entwickeln](#entwickeln)).

| Element | Funktion |
|---|---|
| **Bias**, **Dark** | Automatisch (passend zu den Lights aus der Bibliothek), Keins, oder ein bestimmter Master. |
| **Flat** | Automatisch (gleiche Blende, Brennweite und Objektiv wie die Lights), Keins, oder ein bestimmter Master-Flat. Gleicht Vignettierung und Staubschatten aus. |
| **Dark-Optimierung** | Skaliert das Wärmesignal des Darks mit dem Faktor k (0–4), der das Rauschen am kleinsten macht – gleicht Temperatur- und Zeitunterschiede aus (Siril `-opt`). |
| **Hotpixel-Filter je Bild** | Entfernt einzelne helle Pixel, die die Hotpixel-Karte nicht kennt, und Treffer kosmischer Strahlung. |
| **Satelliten und Flugzeuge entfernen** | Verwirft je Pixel den hellsten Wert (ab 3 Bildern). |
| **Live-Stack starten** / **Beenden & speichern** / **Neu beginnen** | Neu beginnen leert den Stapel; das nächste Bild wird die neue Referenz. |
| Statuszeile | Bilder, Gesamtbelichtung, verworfene Bilder, RAW/JPEG; was gerade gerechnet wird. |
| Kalibrierzeile | Verwendeter Bias, Dark und Flat (oder „keiner für 24 mm · f/4“), Anzahl Hotpixel, aktuelles k, Temperatur der Lights. |
| Tabelle | Die letzten 8 Bilder: Sterne (zugeordnet/gefunden), k, FWHM, Drehung, Restfehler der Ausrichtung (±px); verworfene mit Grund. |
| **Nachstacken** | Stapelt die Lights einer vorhandenen Sitzung – auch ohne Kamera, z. B. mit neuen Darks. |

**Je RAW:**
1. Bias abziehen; ohne Master-Bias den Schwarzwert der Kamera.
2. Dark mal k abziehen.
3. Durch den Master-Flat teilen. Er ist je Farbkanal auf 1 normiert, damit die Farben bleiben, auch bei farbiger
   Lichtquelle.
4. Hotpixel aus der Hotpixel-Karte des Darks durch gleichfarbige Nachbarn ersetzen.
5. Superpixel-Debayer (halbe Auflösung, 2748 × 1835).
6. Bis zu 100 Sterne finden und per Dreiecksvergleich zuordnen.
7. **Homographie** über alle Sterne berechnen: Ein Weitwinkel bildet die Bildfelddrehung vom Stativ projektiv ab.
   An der 6D mit 24 mm liegen die Sterne damit am Rand 0,2 px genau statt 7 px daneben.
8. Verzerren und mitteln.

JPEGs werden nur ausgerichtet und gemittelt (nicht linear, keine Kalibrierung). Bilder mit weniger als 6
zugeordneten Sternen werden verworfen (Wolken, Verwackeln).

**Ergebnis:** `Astro/<Nacht>_<Objekt>/livestack/LIVESTACK_<Zeit>_<n>x<Zeit>_raw.fits` (linear, 32 Bit, liest Siril
direkt) und `.png` (wie angezeigt). Gespeichert wird bei „Beenden & speichern“ und „Speichern“.

#### Bildanalyse

Prüft den Stapel alle 10 Bilder, am Ende und per **Bild prüfen**. Jeder Befund nennt Messwert und Empfehlung:
✓ in Ordnung · i Hinweis · ! Verbesserung möglich.

| Prüfung | Gemessen | Empfehlung, z. B. |
|---|---|---|
| Bias | Master vorhanden? Anzahl Bilder | 30–50 Bias bei der ISO der Lights |
| Dark – Temperatur | Darks gegen Lights (Sensortemperatur aus der CR2), ab 5 °C Unterschied | Darks direkt nach den Lights – die 6D heizt sich im Livebild um 10 °C auf |
| Dark – Faktor | k am Anschlag (0 oder 4)? | Darks passen nicht, neu aufnehmen |
| Dark – Wärmesignal | Wärmesignal im Light gegen das Rauschen | bei kurzer Belichtung wirkt der Dark v. a. als Hotpixel-Karte, die Anzahl zählt |
| Dark – Rauschgrenze | ab wie vielen Bildern das Rauschen des Masters den Stapel begrenzt | mehr Darks – die Grenze wächst mit ihrer Anzahl |
| Rauschgewinn | Rauschen im Stapel gegen Einzelbild/√N | festes Muster („Walking Noise“): passende Kalibrierung, Dithering |
| Hintergrund | Vignettierung, Gradient, helle Bereiche, Überbelichtung | Flats; Lichtquelle oder Wolke |
| Flat | was mit Master-Flat an Vignettierung übrig bleibt; Helligkeit der Flats | Ecken noch dunkler: Blende/Fokus anders als bei den Lights; Ecken heller: Offset fehlt → Bias/Dark-Flats, Master-Flat neu; Flats zu dunkel: +1…+2 EV |
| Sterne | Breite und Form in Mitte und Rand, Stapel gegen Einzelbild, Restfehler der Ausrichtung | Abblenden (Koma), Stativ, Wind |
| Bilder | verworfene, Bilder mit Wolken/Streulicht, Temperaturverlauf; viele verworfene Bilder mit genug Sternen = anderer Bildausschnitt | Bilder aussortieren; nach neuem Ausrichten „Neu beginnen“ |

### Kalibrier-Bibliothek

Masters (gemittelte Kalibrierbilder) als FITS in `Astro/Bibliothek/{bias,darks,flats,darkflats}/`, z. B.
`MASTER-DARK_ISO3200_30s_18C_n20_20261118-2314.fits` oder `MASTER-FLAT_24mm_f4_ISO400_n25_20261119-0130.fits`.
Die Tabelle zeigt Typ, ISO, Zeit (bei Flats Brennweite und Blende), Temperatur, Anzahl und Datum; der Tooltip
zeigt bei Flats Helligkeit, Lichtabfall zur Ecke und was abgezogen wurde. **✓** markiert, was zu den letzten Lights
passt (Dark-Flats: zu den letzten Flats), **✕** löscht einen Master.

**Automatische Auswahl** (Live-Stack „Automatisch“), Kamera und Bildgröße müssen passen:
- **Bias:** gleiche ISO, der neueste.
- **Dark:** gleiche ISO; zuerst gleiche Zeit (±5 %), dann nächste Temperatur, dann der neueste. Mit
  Dark-Optimierung auch Darks mit der 0,5- bis 4-fachen Zeit.
- **Flat:** gleiche Optik – Blende, Brennweite und Objektiv aus dem EXIF müssen passen, soweit bekannt (manuelle
  Objektive melden keine Blende); dann der neueste. ISO und Zeit sind egal.
- **Dark-Flat** (für den Master-Flat): ISO der Flats, Zeit ±25 %.

**Master-Flat:** Von jedem Flat zieht das Studio den Master-Dark-Flat ab (sonst den Master-Bias gleicher ISO,
sonst den Schwarzwert). Dann bringt es jedes Flat auf die Helligkeit des ersten, denn in Av schwankt die Zeit.
Danach folgt dasselbe κσ-Mittel wie bei den anderen Masters. Kommen Dark-Flats erst nach den Flats, baut es den
Master-Flat dieser Sitzung danach von selbst neu und ersetzt den alten.

**Geführt aufnehmen …**

1. **Was?** Bias, Darks, Flats oder Dark-Flats – mit Erklärung, wann und wie viele.
2. **Kamera vorbereiten.** Automatisch geprüft:
   - **Bias:** Moduswahlrad M und ISO wie die Lights.
   - **Darks:** Moduswahlrad B oder M, ISO und Zeit wie die Lights. **Darks unter 1 s sperrt der Assistent** –
     das wären nur Bias, typisch nach einer Bias-Serie mit 1/4000.
   - **Flats:** Moduswahlrad Av (oder M), Blende wie die Lights, ISO bis 800, Helligkeit des ersten Flats.
   - **Dark-Flats:** Moduswahlrad M; ISO und Zeit der Flats stellt das Studio selbst ein. Bei Flats unter 1 s
     reicht meist der Master-Bias.

   Zum Abhaken je Typ, z. B. Objektivdeckel drauf, Sucher abgedeckt, Rauschreduzierung bei Langzeitbelichtung
   aus, RAW. Bei Flats: gleichmäßige Lichtquelle (weißes T-Shirt + Tablet oder Dämmerungshimmel),
   Fokus/Blende/Brennweite unverändert, Belichtungskorrektur +1 EV.
3. **Aufnehmen:** Anzahl (Bias 40, Darks 20, Flats und Dark-Flats 25) und im Modus B die Zeit (vorbelegt mit der
   Zeit der Lights), mit Dauer und Download-Zeit (~25 s je RAW über den Hotspot).
4. **Master erstellen** startet von selbst, sobald alle RAWs auf dem Mac sind. Das Verfahren: κσ-Clipping in zwei
   Durchgängen, verwirft Ausreißer wie kosmische Treffer auch bei wenigen Bildern (ab 3).

**Aus Ordner:** Baut Masters aus Kalibrierbildern, die schon in einem Sitzungsordner liegen (z. B. von der Karte
kopiert). Gruppiert wird nach ISO und Zeit (Flats nur nach ISO), je Gruppe ab 3 Bildern. Für Flats entsteht
vorher der Master-Dark-Flat, wenn die Sitzung Dark-Flats enthält. Derselbe Ordner noch einmal ersetzt den alten
Master.

### Bildverwaltung

**Anzeige: Bilder** (<kbd>B</kbd>). Links die Quellen, und zwar immer für die Kamera- **und** die
Simulator-Ablage (die des laufenden Studios zuerst):
- **Ergebnisse** – alle Live-Stacks, Siril- und PixInsight-Ergebnisse, auch fertig entwickelte, neueste zuerst;
- **Sitzungen** mit Lights, Darks, Bias, Flats, Dark-Flats, Live-Stack, Siril- und PixInsight-Ergebnissen und
  Aussortierten;
- **Einzelaufnahmen** aus dem normalen Kamerabetrieb (Auslösen, Bulb), je Nacht;
- die **Kalibrier-Bibliothek**.

Rechts die Bilder als Vorschau – auch FITS und XISF (z. B. die Masters von WBPP). RAW und JPEG gleichen Namens
sind ein Eintrag, ebenso FITS und PNG eines Live-Stacks. Klick wählt aus (Shift: Bereich, „Alle“), Doppelklick zeigt groß. Darunter stehen die Aufnahmedaten:
Zeit, ISO, Belichtung, Sensortemperatur, Brennweite, Blende, Objektiv, Dateien.

**Nachbearbeitung einer Sitzung** – oben, in drei Schritten (✓ zeigt, was erledigt ist). Darüber
**Nachbearbeiten mit PixInsight | Siril**: das Programm für Stapeln und Fertig entwickeln (nur installierte; die
Wahl merkt sich der Browser).

1. **Prüfen**
   - Zeigt, wie viele Lights es gibt, welche Kalibrierbilder passen und was fehlt.
   - **Lights prüfen** (~0,5 s je Light) findet Lights mit anderem Bildausschnitt, Wolken, unscharfen Sternen oder
     hellem Himmel. **N aussortieren** legt sie nach `verworfen/`.
2. **Stapeln** – läuft im Hintergrund, mit Schritt und Fortschritt; danach löscht das Studio die Zwischenbilder.
   - **Mit PixInsight stapeln:** WBPP ohne Oberfläche – kalibrieren, debayern, bewerten und gewichten,
     ausrichten, lokal normalisieren, stapeln, Ränder beschneiden. Ergebnis `pixinsight/wbpp_<n>x<Zeit>s.xisf`.
     Etwa 10 s je Light auf einem M1; bei 12 Lights 7,8 GB Zwischenbilder.
   - **Mit Siril stapeln:** kalibrieren, debayern, ausrichten, stapeln; etwa 2,6 s je Light, bei 69 Lights rund
     40 GB Zwischenbilder.
   - Oder nur **Ordner für WBPP** bzw. **Siril-Skript**, zum Starten von Hand.
3. **Fertig entwickeln** – für jeden Stapel der Sitzung (PixInsight, Siril oder Live-Stack, FITS oder XISF):
   - **PixInsight:** GradientCorrection, Hintergrund neutralisieren, Farbkalibrierung, MultiscaleAdaptiveStretch,
     Grünstich weg → `<Stapel>_fertig-pi.jpg` und `.tif` (16 Bit), etwa 20 s;
   - **Siril:** Hintergrund und Gradient, Grünstich, strecken → `<Stapel>_fertig.jpg` und `.tif`, etwa 4 s.

   Das Bild öffnet sich gleich groß; beim Stapel steht, womit er fertig ist. **Öffnen …** gibt den Stapel an das
   gewählte Programm.

| Aktion der Leiste | Funktion |
|---|---|
| **Mit PixInsight / Siril entwickeln** | ein gestapeltes FITS oder XISF auswählen (z. B. in „Ergebnisse“) – wie Schritt 3 |
| **Öffnen mit …** | in einem installierten Programm – PixInsight, Siril, GraXpert, Starry Landscape Stacker, Astro Pixel Processor, StarTools, Affinity Photo, Photoshop, Lightroom, darktable, Vorschau. Angeboten wird nur, was die Dateien lesen kann; bei RAW+JPEG bekommt z. B. Starry Landscape Stacker das JPEG. |
| **Finder** | Datei im Finder zeigen |
| **Aussortieren / Zurücklegen** | nach `<Sitzung>/verworfen/<Ordner>/` und zurück (RAW und JPEG zusammen) – Export und Siril lassen sie weg, gelöscht wird nichts |
| **In Sitzung übernehmen …** | Einzelaufnahmen als Lights (oder Kalibrierbilder) in eine Sitzung verschieben. Sie werden nach der Namenskonvention benannt (Zeit, ISO, Belichtung, Blende aus dem EXIF) und in `serie.csv` eingetragen; die Nummern laufen mit den Serien fort. |

**Für PixInsight (WBPP):** `<Sitzung>/pixinsight/` mit
- `Rohbilder/` – `Light/`, `Dark/`, `Flat/`, `Bias/`: harte Verknüpfungen auf die Rohbilder, also kein
  zusätzlicher Platz; dazu `lights.txt` usw. mit den Originalpfaden und `bilder.csv` mit allen Aufnahmedaten;
- `wbpp/` – Arbeitsordner, wenn das Studio WBPP startet: `master/` (Masters als XISF), `logs/` (Protokolle);
- `wbpp_*.xisf` – Ergebnisse, `*_fertig-pi.*` mit PixInsight fertig entwickelt;
- `LIESMICH.txt`.

Von Hand in WBPP: **+ Directory** → `pixinsight/Rohbilder`. Den Bildtyp erkennt WBPP am Dateinamen; der Typ steht
als letztes Wort, denn so liest WBPP ihn (nachgelesen im WBPP-Quelltext von PixInsight 1.9.6). Dark-Flats liegen
bei den Darks. Das Studio startet WBPP selbst im Automatikbetrieb (`PixInsight -n --automation-mode -r=WBPP.js,…`);
den Fortschritt meldet ein Ereignisskript (`studio/pixinsight/`). Die Einstellungen der WBPP-Oberfläche gelten
dabei nicht, sondern die Voreinstellungen von WBPP; die Astrometrie ist aus (sie bräuchte die Gaia-Datenbank).

Mit gehen nur passende Kalibrierbilder, der Rest wird in `LIESMICH.txt` genannt:
- Bias und Darks mit der ISO der Lights, Darks auch mit ihrer Zeit;
- Flats mit gleicher Optik;
- Dark-Flats passend zu den Flats.

Fehlt ein Typ, nimmt der Export die Rohbilder des passenden Masters aus der Bibliothek, auch aus einer anderen
Sitzung. Masters selbst nimmt WBPP nur als XISF, unsere sind FITS.

**Für Siril:** `<Sitzung>/siril/` mit `lights/ darks/ flats/ biases/ darkflats/` (nur die passenden RAWs,
verknüpft) und `vorverarbeitung.ssf`, nach Sirils `OSC_Preprocessing` aus Siril 1.4.

- Fehlt ein Kalibriertyp, nimmt das Skript die Rohbilder oder den Master aus der Bibliothek; Siril liest unsere
  FITS direkt.
- Ganz ohne Bias und Dark dient der Schwarzwert als Offset.
- Aufruf ohne Studio: `siril-cli -d <Sitzung>/siril -s vorverarbeitung.ssf`.

Geprüft mit Siril 1.4.5 und PixInsight 1.9.6 (WBPP 3.1.2) mit echten Lights. FITS schreibt das Studio wie Siril
von unten nach oben, ältere dreht es vor der Benutzung um; PixInsight beachtet die Angabe im FITS-Kopf, und
Siril 1.4 liest auch XISF. Siehe [docs/nachbearbeitung.md](docs/nachbearbeitung.md).

**Rechner (PixInsight auf einem zweiten Rechner):** Auf dem PC läuft `rechner/6d-rechner.py` – ein Dienst ohne
Oberfläche, nur Python, erreichbar über Tailscale. Im Studio: **+ Rechner** (Adresse und Schlüssel), danach gibt es
**Rechner** als drittes Programm neben PixInsight und Siril.
- **Auf dem Rechner stapeln:** Export wie für WBPP, Abgleich mit dem Rechner (fehlende Rohbilder hoch, nicht mehr
  passende dort weg), WBPP dort mit Fortschritt im Studio, Master-Light zurück nach `pixinsight/`.
- **Auf dem Rechner entwickeln:** Stapel hoch (wenn er dort fehlt), `6d-fertig.js` dort, JPEG und TIFF zurück.
- **Vorab hochladen** (wahlweise): neue Rohbilder der laufenden Sitzungen alle 20 s – nur, was der Export auch nähme.

Einrichtung (Ubuntu, NVIDIA, PixInsight, Xvfb, Tailscale, systemd), Datenmengen und Schnittstelle:
[docs/rechner.md](docs/rechner.md).

Unter der Leiste steht, welche Programme gefunden wurden und welche freien fehlen. Die Recherche zu den Programmen
und Formaten steht in [docs/nachbearbeitung.md](docs/nachbearbeitung.md).

### Letztes Foto

Vorschau des zuletzt geladenen Fotos (Klick: groß), Größe und Zeit, darunter die vorigen 8 und der Fortschritt
laufender Downloads. **Fotos nach der Aufnahme automatisch auf den Mac laden** gilt für Einzelbilder und Bulb;
Serien haben ihre eigene Einstellung.

### Protokoll

Alle Ereignisse (Verbindung, Aufnahmen, Downloads, Live-Stack, Masters, Fehler) – auch in
`~/.gphoto/6d-proxy.log`.

### Tastenkürzel

| Taste | Funktion |
|---|---|
| <kbd>L</kbd> | Livebild ein/aus |
| <kbd>1</kbd> <kbd>5</kbd> <kbd>0</kbd> | Lupe 1×, 5×, 10× |
| Pfeiltasten | Ausschnitt der Lupe verschieben |
| <kbd>,</kbd> <kbd>.</kbd> | Fokus fein nah / fern |
| <kbd>V</kbd> | Anzeige Kamera ↔ Live-Stack |
| <kbd>B</kbd> | Anzeige Bilder (Bildverwaltung) ↔ Kamera; <kbd>Esc</kbd> schließt die große Ansicht |
| <kbd>N</kbd> | Nachtmodus |

### Ordner und Dateien

| Pfad | Inhalt |
|---|---|
| `~/Pictures/6D-Tethering/` | Einzelbilder (`IMG_xxxx.CR2/.JPG`), Vorschauen in `.vorschau/` (Bildverwaltung: `.vorschau/thumbs/`) |
| `…/Astro/<Nacht>_<Objekt>/` | Serien (`lights/`, `darks/`, `biases/`, `flats/`, `darkflats/`), `serie.csv`, `livestack/` |
| `…/Astro/<Nacht>_<Objekt>/verworfen/` | aussortierte Bilder, je Ordner |
| `…/Astro/<Nacht>_<Objekt>/pixinsight/` | `Rohbilder/` für WBPP (Verknüpfungen, Listen, `bilder.csv`), `wbpp/` (Masters, Protokolle), Ergebnisse `wbpp_*.xisf` |
| `…/Astro/<Nacht>_<Objekt>/siril/` | Siril-Arbeitsordner mit Skript und Ergebnissen |
| `…/Astro/Bibliothek/` | Masters: Bias, Darks, Flats, Dark-Flats (FITS) |
| `…/Simulator/` | Fotos des Simulators |
| `~/.gphoto/6d-proxy.log` | Protokoll des Studios |
| `~/.gphoto/6d-verbindung`, `~/.gphoto/6d-wlan-ip` | Verbindungsart (`usb` oder `wlan`) und Kamera-IP – Studio und `6d-fernsteuerung` |
| `~/.config/6d-studio/rechner.json` | Adresse und Schlüssel des Rechners, Vorab-Hochladen (nur für den Besitzer lesbar) |
| PC: `~/6d-rechner/` | 6D Rechner: `token` (Schlüssel), `sitzungen/<Ablage>_<Sitzung>/` wie `pixinsight/` |

## Kommandozeile

`6d-fernsteuerung <befehl>` – läuft das Studio (immer bei WLAN), gehen die gphoto2-Befehle durch das Studio
(ptpip:127.0.0.1), auch am Kabel; ohne Studio spricht gphoto2 am Kabel direkt mit der Kamera.

| Befehl | Funktion |
|---|---|
| `studio` | Studio starten und die Oberfläche öffnen – über WLAN oder am Kabel, je nach Verbindungsart |
| `stop` | Studio beenden (danach an der Kamera die Fernsteuerung neu starten) |
| `wlan [ip]` | Auf WLAN umschalten: Kamera im Netz suchen (oder die IP nehmen), Studio starten bzw. umschalten |
| `usb` | Auf das USB-Kabel umschalten; ein laufendes Studio wechselt mit |
| `status` | Verbindungsart, Studio, Anmeldung der Kamera |
| `check`, `settings` | Kamera-Infos bzw. ISO, Blende, Zeit, Weißabgleich, Bildqualität, Akku |
| `options <name>`, `set <name> <wert>` | Mögliche Werte einer Einstellung bzw. ändern, z. B. `set iso 800` |
| `shoot`, `tether` | Ein Foto auslösen und laden bzw. jedes an der Kamera ausgelöste Foto laden |
| `liveview`, `preview` | Livebild in einem Fenster (ffplay) bzw. ein einzelnes Livebild |
| `shell` | Interaktive gphoto2-Shell |
| `sim [optionen]`, `sim-cmd <befehl>`, `sim-stop` | Simulierte 6D – siehe [Simulator](#ohne-kamera-der-simulator) |

## Änderungen

**23.11.2026**
- **Rechner talos eingerichtet** (Ubuntu 24.04, RTX 4090): PixInsight 1.9.6 (GPU-Ausgabe), Dienst als
  systemd-Dienst, Daten auf der NVMe-SSD – Schritte in `docs/rechner.md`.
- **KI-Entrauschen mit MLDenoise** beim Fertig-Entwickeln: auf dem Rechner, wenn dort ein Modell (`MLDenoise_v42.xmlm`
  von pixinsight.com/dist) in `<daten>/modelle/` liegt – 19 s auf der RTX 4090 statt 109 s auf dem M1; auf dem Mac,
  wenn eines in `~/.config/6d-studio/modelle/` liegt. Schlägt es fehl (z. B. Grafikspeicher voll), wird ohne
  Entrauschen fertig entwickelt, mit Warnung.
- Tests lesen und schreiben die echten Rechner-Einstellungen nicht mehr (der Pfad wurde beim ersten Import
  festgelegt).

**22.11.2026**
- **USB-Kabel:** Das Studio geht jetzt auch am Kabel – Livebild, Bulb, Serien, Downloads, Live-Stack und der
  gphoto2-Zugang wie über WLAN. Umschalten in der Kopfzeile (**WLAN | Kabel**) oder mit `6d-fernsteuerung usb` /
  `wlan`. Technik: `studio/ptpusb.py` – libusb über ctypes (keine Python-Pakete nötig) und eine Brücke, die für das
  Studio aussieht wie die PTP/IP-Verbindung.
- `6d-fernsteuerung usb` beendet das Studio nicht mehr und vergisst die WLAN-IP nicht; die Verbindungsart steht in
  `~/.gphoto/6d-verbindung`.
- Geprüft an der echten 6D am Kabel: Anmeldung in unter 1 s, Livebild 15 Bilder/s, ISO ändern, Lupe,
  Bulb-Testbild mit Download (RAW 19,4 MB, ~10 MB/s), gphoto2 durch das Studio, Umschalten Kabel → WLAN → Kabel.
  Einzelheiten: [docs/canon-6d-ptpip.md](docs/canon-6d-ptpip.md#usb-kabel-ptp-über-usb).

**20.11.2026**
- **Rechner:** PixInsight auf einem zweiten Rechner (`rechner/6d-rechner.py`, Anbindung `studio/rechner.py`).
  Auf dem Rechner stapeln und entwickeln, Abgleich der Rohbilder, wahlweise Vorab-Hochladen während der Serie;
  Einrichtung in `docs/rechner.md`.
- **WBPP robuster:** `wbpp/` wird vor jedem Lauf geleert (keine `_(1)`-Kopien der Masters mehr); stürzt PixInsight
  ab, nennt der Auftrag den Schritt, die Zwischenbilder werden gelöscht, und bei der lokalen Normalisierung läuft
  WBPP einmal ohne sie (gesehen: PixInsight 1.9.6, SIGABRT in `LocalNormalizationThread`).
- **PixInsight** in der Bildverwaltung: Programmwahl „Nachbearbeiten mit PixInsight | Siril“.
  - **Mit PixInsight stapeln:** WBPP ohne Oberfläche, Fortschritt mit den Schritten von WBPP, Master-Light als
    `pixinsight/wbpp_<n>x<Zeit>s.xisf`, Zwischenbilder werden gelöscht.
  - **Fertig entwickeln mit PixInsight:** GradientCorrection, BackgroundNeutralization, ColorCalibration,
    MultiscaleAdaptiveStretch, SCNR → JPEG und 16-Bit-TIFF (`studio/pixinsight/6d-fertig.js`).
  - Siril entwickelt jetzt auch XISF fertig.
- **XISF** in der Bildverwaltung: Vorschau und Aufnahmedaten (eigener Leser, `imaging.read_xisf`).
- Der Ordner für WBPP heißt jetzt `pixinsight/Rohbilder/`; ein neuer Export lässt Ergebnisse und `wbpp/` stehen.
- „Fertig“ zählt nur, was der Lauf eben geschrieben hat – ein älteres fertiges Bild gleichen Namens täuscht keinen
  Erfolg mehr vor.

**19.11.2026**
- **Nachbearbeitung** in der Bildverwaltung als Ablauf: Prüfen (Lights mit anderem Ausschnitt, Wolken, Unschärfe
  finden und aussortieren) → mit Siril stapeln (Fortschritt, Zwischenbilder werden gelöscht) → fertig entwickeln
  (Hintergrund, Grünstich, Strecken → JPEG/TIFF). Geprüft mit Siril 1.4.5.
- Bildverwaltung zeigt immer Kamera- und Simulator-Ablage, dazu „Ergebnisse“ mit allen Live-Stacks und
  Siril-Ergebnissen.
- **FITS** jetzt von unten nach oben wie Siril – vorher standen Live-Stacks in Siril auf dem Kopf, und ein Master
  hätte gespiegelt über den Lights gelegen. Ältere Dateien werden vor der Benutzung in Siril gedreht.
- **Bildverwaltung** (Anzeige „Bilder“, `studio/gallery.py`):
  - Sitzungen, Einzelaufnahmen und Bibliothek mit Vorschaubildern und Aufnahmedaten;
  - aussortieren, Einzelaufnahmen in Sitzungen übernehmen;
  - „Öffnen mit“ installierten Programmen;
  - Ordner für PixInsight WBPP mit Listen;
  - Siril-Arbeitsordner mit Vorverarbeitungs-Skript, mit installiertem Siril direkt ausführbar.
  - Recherche dazu in `docs/nachbearbeitung.md`.
- **Flats und Dark-Flats** in Live-Stack und Bibliothek:
  - Master-Flat mit Abzug von Dark-Flat oder Bias und Helligkeitsausgleich;
  - automatische Auswahl nach Optik (Blende, Brennweite, Objektiv aus dem EXIF);
  - Division je Farbkanal normiert;
  - geführte Aufnahme mit Rückmeldung zur Flat-Helligkeit;
  - Dark-Flats mit von selbst gestellter ISO und Zeit, danach Neubau des Master-Flats;
  - Bildanalyse prüft, ob der Flat passt.
- Gleiche Bilder noch einmal als Master gebaut ersetzen den alten Master.
- **Bildanalyse** im Live-Stack mit Empfehlungen für Darks, Bias und Flats (`studio/analysis.py`).
- Ausrichtung per **Homographie** – keine Doppelsterne mehr am Rand bei Weitwinkel.
- **Hintergrund ebnen** in der Stack-Anzeige – keine schwarz abgeschnittenen Ecken mehr.
- Dark-Optimierung rechnet ohne die Hotpixel der Hotpixel-Karte, k bis 4. Vorher trieben die Hotpixel k an den
  Anschlag 2.
- Das Studio startet immer mit dem Kamerabild; zum Live-Stack nur per Klick oder <kbd>V</kbd>.
- Das grüne Quadrat (Stern-Messung) bleibt beim angeklickten Stern statt zu springen.
- Fortlaufende Bildnummern, auch wenn eine Serie startet, während die vorige noch lädt.

**18.11.2026**
- Assistent und Master-Erstellung lehnen Darks unter 1 s ab (das wären Bias).
- **Live-Stack** mit Dark-Optimierung wie Siril; **Dark-/Bias-Bibliothek** mit geführter Aufnahme.
- **Serien für Stacking** mit Namenskonvention und `serie.csv`; Ablauf für Kalibrierbilder.
- Entwicklungsumgebung mit venv.

**17.11.2026**
- 6D Studio: WLAN-Fernsteuerung mit Livebild, Lupe, Fokus, Bulb, Download.
- Kamera-Simulator für die Entwicklung ohne Kamera.

## Grenzen und offene Punkte

- **Fokusantrieb:** an der echten 6D noch nicht ausprobiert – Richtung und Schrittgrößen sind angenommen.
- **Flats an der echten 6D:** Master-Flat, Auswahl nach Optik und Korrektur sind mit dem Simulator getestet,
  noch nicht mit einer echten Lichtquelle. Ob die 6D im Modus Av bei Fernauslösung die Belichtungskorrektur
  übernimmt, ist offen.
- **Live-Stack:** Der Live-Stack mittelt nur (außer „Satelliten entfernen“) – für das Endbild die RAWs in Siril
  stapeln. Der Vordergrund verschmiert, weil auf die Sterne ausgerichtet wird.
- **Uhrzeit:** Datum und Uhrzeit der Masters kommen aus der Kamera – ihre Uhr sollte stimmen.
- **Bildverwaltung:** PixInsight (1.9.6) und Siril (1.4.5) sind mit echten Lights geprüft. SPCC und Astrometrie
  brauchen die Gaia-Datenbank von PixInsight, MLDenoise eine Modelldatei – beides ist noch nicht im Ablauf. Ein
  laufender Auftrag lässt sich nicht abbrechen. GraXpert ist nur über „Öffnen mit“ angebunden.
- **Rechner:** auf dem Mac mit dem echten PixInsight geprüft (Dienst lokal); auf Linux noch nicht gelaufen (Xvfb,
  `/opt/PixInsight`, systemd). Die Grafikkarte nutzt der Ablauf noch nicht (BXT/NXT, MLDenoise wären die Schritte
  dafür).

## Projekt

| Pfad | Inhalt |
|---|---|
| `studio/6d-studio.py` | Server: PTP/IP zur Kamera, Vermittler für gphoto2, Web-API |
| `studio/6d-studio.html` | Bedienoberfläche (HTML/CSS/JS, keine Abhängigkeiten) |
| `studio/astro.py` | Serien für Stacking: Bildtypen, Namenskonvention, `serie.csv` |
| `studio/livestack.py` | Live-Stack: Kalibrierung mit Dark-Optimierung, Sterne finden, Ausrichten (Dreiecke + Homographie), Mitteln, Anzeige |
| `studio/analysis.py` | Bildanalyse des Stapels: Kalibrierung, Rauschgewinn, Hintergrund, Sterne – mit Empfehlungen |
| `studio/gallery.py` | Bildverwaltung: Übersicht, Vorschaubilder, Aussortieren, Übernehmen, Ordner und Aufträge für PixInsight (WBPP) und Siril, Programme finden und öffnen |
| `studio/pixinsight/` | Skripte für PixInsight ohne Oberfläche: Ereignisskript für WBPP (Fortschritt), Fertig entwickeln |
| `studio/rechner.py` | Anbindung an 6D Rechner: Einstellungen, Dateien abgleichen, Aufträge, Vorab-Hochladen |
| `studio/ptpusb.py` | USB-Kabel: libusb über ctypes, PTP-Container auf den Bulk-Endpunkten, Brücke zu PTP/IP |
| `rechner/6d-rechner.py`, `rechner/6d-rechner.service` | 6D Rechner: Dienst für PixInsight auf einem zweiten Rechner, systemd-Datei |
| `studio/calib.py` | Kalibrier-Bibliothek: Master-Bias, -Darks, -Flats, -Dark-Flats als FITS, automatische Auswahl (auch nach Optik), Master aus RAWs (κσ-Clipping) |
| `studio/imaging.py` | RAW (rawpy), JPEG, FITS, XISF (lesen), PNG; EXIF inkl. Sensortemperatur der 6D |
| `bin/6d-fernsteuerung` | Kommandozeilen-Werkzeug (zsh) – startet/stoppt das Studio, gphoto2-Befehle |
| `sim/sim6d.py` | **Simulierte 6D** – PTP/IP wie die echte Kamera, Sternenhimmel-Livebild, Bulb, Download, RAWs |
| `tools/ptpip-handshake.py` | Anmeldung/Kopplung direkt an der Kamera testen (90 s Wartezeit) |
| `tools/ptp-caps.py` | Unterstützte Befehle und Eigenschaften auslesen (über das laufende Studio) |
| `tools/ptp-probe.py` | Einzelne PTP-Befehle über das Studio schicken |
| `tools/legacy/` | Erster Vermittler, ersetzt durch `6d-studio` |
| `tests/` | Tests ohne Kamera, auch Studio ↔ Simulator (`.venv/bin/python -m unittest discover -s tests`, ~2 min) |
| `docs/canon-6d-ptpip.md` | **Alles, was wir über das Protokoll der 6D herausgefunden haben** |
| `docs/stacking-und-kalibrierung.md` | Serien, Namenskonvention, Ablauf einer Nacht, Live-Stack, Bildanalyse, Bibliothek |
| `docs/rechner.md` | Rechner: Aufbau, Einrichtung (Ubuntu, NVIDIA, PixInsight, Xvfb, Tailscale, systemd), Datenmengen, Schnittstelle |
| `docs/nachbearbeitung.md` | Recherche: Programme zur Nachbearbeitung, was PixInsight (WBPP) und Siril erwarten; die Bildverwaltung |

Läuft mit Python 3.9+. Fernsteuerung, Livebild, Bulb und Serien brauchen nur die Standardbibliothek (auch das
System-Python `/usr/bin/python3`); Live-Stack, Bildanalyse und Bibliothek brauchen zusätzlich numpy und rawpy
(`requirements.txt`); die Bildverwaltung läuft ohne, nur Vorschaubilder von FITS brauchen numpy.

## Entwickeln

Einmalig eine venv anlegen – mit numpy und rawpy für den Live-Stack (`requirements.txt`) und ruff zum Prüfen:

```bash
/opt/homebrew/bin/python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

`6d-fernsteuerung` und VS Code nehmen automatisch `.venv/bin/python`; ohne venv das System-Python (dann ohne
Live-Stack). ruff prüft auf Python 3.9 (`pyproject.toml`), damit der Code mit beiden läuft.

```bash
./install.sh
```

legt Symlinks in `~/.local/bin` auf dieses Projekt an – Änderungen wirken sofort:

- **Oberfläche** (`6d-studio.html`): Browser neu laden (Cmd+R).
- **Server und Module** (`*.py`): `6d-fernsteuerung stop` und `6d-fernsteuerung studio`.
  Danach muss an der Kamera die Fernsteuerung neu gestartet werden (die 6D beendet sie beim Trennen). Ein
  laufender Live-Stack geht dabei verloren – die Lights liegen aber auf dem Mac: „Nachstacken“.

In VS Code (Cmd+Shift+P → „Tasks: Run Task“): Studio starten/stoppen, Log verfolgen, Tests, Installieren.
Debuggen: Startkonfiguration „6D Studio (Debug)“ – stoppt vorher das Hintergrund-Studio.

### Ohne Kamera: der Simulator

```bash
6d-fernsteuerung sim
```

startet eine simulierte 6D und ein zweites Studio unter <http://127.0.0.1:8067> – parallel zum echten Studio
(Ports 15741/15742/8067, Fotos nach `~/Pictures/6D-Tethering/Simulator`). Beenden: `6d-fernsteuerung sim-stop`.

Der Simulator verhält sich wie die echte 6D: Anmeldung dauert 4 s, nach CloseSession wird alles abgelehnt,
ohne Abfragen trennt sie nach 30 s, Download mit 1 MB/s. Das Livebild zeigt einen Sternenhimmel, der auf
Lupe und Fokusantrieb reagiert – ideal zum Testen der Schärfe-Anzeige. Optionen: `sim/sim6d.py --help`
(z. B. `--mode M`, `--apo 60` für die Abschaltautomatik, `--reject` für abgelehnte Kopplung).

Für Live-Stack und Bibliothek: `6d-fernsteuerung sim --raw --sky-speed 60 --focus 0`. Dann gibt es je Aufnahme
ein RAW mit echten Sensordaten:
- Offset und Rauschen,
- Hotpixel abhängig von Temperatur und ISO,
- Vignettierung (Ecken −30 %) und der Schatten eines Staubkorns,
- Sterne, die sich 60× schneller um den Pol drehen,
- Blende, Brennweite und Objektiv im EXIF.

Für Darks und Bias: `6d-fernsteuerung sim-cmd cap on` (Objektivdeckel), `sim-cmd temp 20` (Umgebungstemperatur),
`sim-cmd mode M`. Für Flats: `sim-cmd mode Av` und `sim-cmd panel on` (Leuchtfläche vor dem Objektiv; Av belichtet
sie auf 40 %).

In VS Code mit Haltepunkten in beiden: Startkonfiguration **„Simulator + Studio (Debug)“**. Im Terminal des
Simulators lässt er sich steuern: `mode M`, `drop` (WLAN-Abbruch), `freeze`/`wake`, `focus 0`, `status`.

## Erstes Koppeln

Nur nötig, wenn `~/.gphoto/settings` keine `ptp2_ip=guid=`-Zeile hat oder die Kamera zurückgesetzt wurde:

1. GUID erzeugen und eintragen (gphoto2-Format, 16 Bytes mit Doppelpunkten).
2. Kamera: Fernsteuerung (EOS Utility) → „Kopplung starten“.
3. `tools/ptpip-handshake.py 172.20.10.3` – wartet bis 90 s; fragt die Kamera nach, mit OK bestätigen.
