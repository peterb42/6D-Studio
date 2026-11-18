# Serien für Stacking, Kalibrierbilder und Live-Stacking

Stand 18.11.2026. Teil 1 beschreibt die Serien. Teil 2 ist der empfohlene Ablauf für eine Nacht. Teil 3
vergleicht, wie andere Programme live stacken. Teil 4 beschreibt den Live-Stack von 6D Studio, Teil 5 die
Dark-/Bias-Bibliothek mit geführter Aufnahme.

## 1. Serie für Stacking in 6D Studio

Karte **„Serie für Stacking“** in der Oberfläche (API: `POST /api/series/start`, `/api/series/stop`):

| Bildtyp | Moduswahlrad | Belichtung | Download-Vorgabe |
|---|---|---|---|
| **Light** | B (Zeit im Studio) oder M (Kamerazeit) | wie eingestellt | an |
| **Dark** | wie die Lights | wie die Lights, Deckel drauf | aus |
| **Bias** | M | Studio stellt die **kürzeste Zeit** (1/4000) ein | aus |
| **Flat** | Av | Kamera bestimmt die Zeit | aus |
| **Dark-Flat** | M | Zeit der Flats, Deckel drauf | aus |

- Zwischen den Bildern wartet die Serie, bis die Kamera das Bild gemeldet hat (`ObjectAddedEx`), plus eine
  einstellbare **Pause**. So werden RAW und JPEG sicher dem richtigen Bild zugeordnet.
- **Stopp** beim ersten Klick: nach dem laufenden Bild. Beim zweiten Klick: sofort (die laufende Bulb-Belichtung
  wird beendet und das Bild trotzdem gespeichert).
- Während einer Serie sind der Bulb-Knopf und „Auslösen“ gesperrt.
- **Auf den Mac:** alles, nur RAW, nur JPEG oder nichts. Bei RAW+JPEG an der Kamera spart „nur RAW“ für den
  Live-Stack die Hälfte der Wartezeit, „nur JPEG“ ist am schnellsten (dann ohne Kalibrierung).
- Downloads laufen jetzt **in Stücken** (`GetPartialObject`, 256 KB), und Bulb-Befehle haben **Vorrang**.
  Ein laufender Download verzögert das Ende einer Belichtung um höchstens ~0,25 s statt um bis zu 20 s.
  Kann die Kamera kein `GetPartialObject`, wird wie bisher am Stück geladen. **An der echten 6D noch prüfen.**

### Namenskonvention

```
~/Pictures/6D-Tethering/Astro/
  2026-11-18_Milchstrasse/                 ← <Nacht>_<Objekt>
    lights/     LIGHT_Milchstrasse_20261118-231405_ISO3200_30s_f2.8_0001.CR2
                LIGHT_Milchstrasse_20261118-231405_ISO3200_30s_f2.8_0001.JPG   (bei RAW+JPEG)
    darks/      DARK_Milchstrasse_20261119-004512_ISO3200_30s_f2.8_0001.CR2
    biases/     BIAS_Milchstrasse_…_ISO3200_1-4000s_f2.8_0001.CR2
    flats/      FLAT_Milchstrasse_…_ISO200_1-60s_f2.8_0001.CR2
    darkflats/  DARKFLAT_…
    serie.csv   eine Zeile pro Datei
```

- **Ordnernamen `lights/darks/biases/flats`** sind genau das, was die Siril-Skripte erwarten
  (z. B. `OSC_Preprocessing`). Siril im Sitzungsordner starten, Skript wählen, fertig.
- **Nacht** = Datum 12 Stunden vor der Aufnahme (wie `$$DATEMINUS12$$` bei N.I.N.A.). Eine Nacht bleibt so
  ein Ordner, auch wenn sie über Mitternacht geht.
- **Dateiname**: Typ, Objekt, Startzeit, ISO, Belichtung (`30s`, `2.5s`, `1-4000s`), Blende (`f2.8`,
  `f--` bei manuellen Objektiven), laufende Nummer. Die Nummer zählt im Ordner weiter, eine zweite Serie
  überschreibt also nichts. Programme sortieren die Dateien dadurch richtig, und die wichtigen Werte stehen
  auch ohne EXIF im Namen.
- Bleibt ein Bild nur auf der Karte (Download aus), steht es trotzdem in `serie.csv`, mit dem
  **Kamera-Dateinamen** (`IMG_1234.CR2`). So lassen sich die Dateien später von der Karte zuordnen.
- `serie.csv`: `typ, nr, datei, kameradatei, ordner, start, belichtung_geplant_s, belichtung_ist_s, iso,
  blende, zeit, modus, geladen`. `belichtung_ist_s` ist die gemessene Bulb-Zeit.

## 2. Empfohlener Ablauf für eine Nacht (EOS 6D, Stativ, ungekühlt)

Grundsätze für Kalibrierbilder: **Darks** enthalten das Wärmerauschen und hängen von ISO, Zeit und
**Temperatur** ab. **Bias** enthalten das Ausleserauschen (ISO, kürzeste Zeit). **Flats** enthalten
Vignettierung und Staub; sie gehören zu Optik, Blende und Fokus. **Dark-Flats** sind Darks mit der Zeit der Flats.

| Wann | Was | Wie viele | Hinweise |
|---|---|---|---|
| Zu Hause, beliebig | **Bias** | 40–50 | Modus M, Studio stellt 1/4000, gleiche ISO wie später. Einmal machen, wiederverwenden (Bias ändern sich kaum). |
| Vor Ort | Scharfstellen | – | Livebild, heller Stern, Lupe 10×, Stern-Ø minimieren. Tipp: kurz Modus M, 30 s, hohe ISO → helleres Livebild. |
| Nacht | **Lights** | so viele wie möglich | Modus B oder M. Belichtungszeit nach NPF-/500er-Regel (ohne Nachführung), z. B. 24 mm ≈ 10–15 s. |
| Direkt nach den Lights | **Darks** | 15–30 | **Gleiche ISO und Zeit**, Deckel drauf, Sucher abdecken. Gleich anschließend, damit die Temperatur passt. |
| Vor dem Abbauen | **Flats** | 20–30 | Modus Av, **Fokus und Blende nicht anfassen**. Weißes T-Shirt + Tablet/Dämmerung, Histogramm etwa mittig. ISO 100–400. |
| Optional | **Dark-Flats** | 20–30 | Modus M, Zeit der Flats. Bei kurzen Flats reichen meist die Bias. |

**Temperatur bei DSLRs:** Das Wärmesignal verdoppelt sich grob alle ~6 °C. Die 6D schreibt eine
Gehäusetemperatur (`CameraTemperature`) in die EXIF-Daten. Für eine **Dark-Bibliothek** sortiert man Darks nach
ISO, Zeit und Temperatur (±2–3 °C) und nimmt passende Darks zu den Lights. Siril bietet für DSLRs außerdem
**Dark-Optimierung** an, die das Master-Dark an das Rauschen der Lights anpasst. Das hilft, wenn die Temperatur
nicht genau passt.

**Ausarbeitung:** Siril im Sitzungsordner, Skript `OSC_Preprocessing`. Bei langen Serien vom Stativ
Registrierung mit **Rotation** (Bildfelddrehung).

## 3. Wie andere Programme live stacken

| Programm | Kameras | Kalibrierung live | Ausrichtung | Bemerkung |
|---|---|---|---|---|
| **SharpCap** (Windows) | Astrokameras, eingeschränkt DSLR | Dark und Flat pro Bild vor dem Ausrichten, rohe Bilder werden unkalibriert gespeichert | Mehrstern-Ausrichtung, mind. 3 (besser 10+) Sterne; Phasenkorrelation für Planeten | Vorbild für den Ablauf: kalibrieren → ausrichten → aufaddieren |
| **ASIAIR** (ZWO) | ZWO, DSLRs | Master-Dark/Flat/Bias als FITS, empfohlen: **Dark-Bibliothek** vorab | Sterne | Bias für DSLRs ausdrücklich wichtig; bekannte Farbprobleme bei Dark-Abzug live |
| **Siril** `livestack` | FITS und **Kamera-RAW** | `start_ls -dark=… -flat=…`, Master vorher erstellen | nur Verschiebung, `-rotate` für Bildfelddrehung | Überwacht einen Ordner oder bekommt Dateien per Befehl; während der Session keine anderen Befehle |
| **ALS – Astro Live Stacker** | FITS und DSLR-RAW | Darks | Sterne | Python, überwacht einen Ordner, in den die Aufnahme-Software speichert |
| **OpenLiveStacker** | ZWO, ToupTek, UVC, **DSLR über libgphoto2** | Darks, Flats, Dark-Flats (Bibliothek mit Namen) | FFT-Verschiebung, Derotation für Alt-Az | Android/Linux, Web-Oberfläche. Am nächsten an unserem Fall |
| **Seestar** (ZWO) | eingebaut | Darks automatisch, keine Flats | – | Geschlossenes System |

Gemeinsamer Ablauf: **Dark (und Bias) abziehen → durch Flat teilen → Sterne finden → Verschiebung (und
Drehung) bestimmen → zum Stapel mitteln → Anzeige automatisch strecken (nur die Anzeige).** Live-Stacks
sind ein Vorschau- und Beobachtungswerkzeug; für das Endbild stapelt man die RAWs später sorgfältig.

## 4. Live-Stack in 6D Studio

Eigene Pipeline mit numpy und rawpy (`studio/livestack.py`), angelehnt an **OpenLiveStacker** (Ablauf,
„Satelliten entfernen“, Streckung nur für die Anzeige) und **Siril** (Dark-Optimierung, kosmetische Korrektur,
Ausrichtung mit Drehung). Siril selbst wird nicht gebraucht – aber alle Ergebnisse sind FITS, die Siril direkt liest.

**Bedienung:** Karte „Live-Stack“ → Bias/Dark wählen (Vorgabe: automatisch aus der Bibliothek) → **Starten**.
Danach wird jedes Light eingerechnet, das auf den Mac kommt (Serie oder einzelne Bulb-Aufnahme). Oben
„Anzeige: Live-Stack“ (Taste <kbd>V</kbd>) zeigt das Ergebnis, darunter Helligkeit und Schwarzpunkt (nur Anzeige).
**Beenden & speichern** schreibt `LIVESTACK_<Zeit>_<n>x<Zeit>_raw.fits` (linear, 32 Bit) und `.png` (gestreckt)
nach `Astro/<Nacht>_<Objekt>/livestack/`. **Nachstacken** rechnet vorhandene Lights einer Sitzung ein –
praktisch zum Ausprobieren ohne Kamera.

**Je Bild (RAW):**

| Schritt | Wie | Vorbild |
|---|---|---|
| 1. Bias abziehen | Master-Bias gleicher ISO, sonst Schwarzwert der Kamera (2047) | Siril „=2048“ |
| 2. Dark abziehen | Wärmesignal = Master-Dark − Bias, mal **k**. Dark-Optimierung: k ∈ [0, 2] per Goldener-Schnitt-Suche (Toleranz 0,001), kleinste Streuung im 512×512-Feld der Bildmitte, je Farbe des Bayer-Musters | Siril `-opt` |
| 3. Hotpixel | aus dem Wärmesignal (Median + 5σ, höchstens 1 %), ersetzt durch die 4 gleichfarbigen Nachbarn; dazu ein Filter je Bild für Reste und kosmische Strahlung (Sterne bleiben, weil ihr Licht mehrere Pixel trifft) | Siril „cosmetic correction“ |
| 4. Debayer | Superpixel (halbe Auflösung, 6D: 2748 × 1835) | Siril „Superpixel“ |
| 5. Ausrichten | bis 60 Sterne (Blockhintergrund, 5σ, Schwerpunkt), Dreiecke aus je 5 Nachbarn, log. Seitenverhältnisse vergleichen, Ähnlichkeitstransformation (Verschiebung + Drehung), mit allen Paaren verfeinert | astroalign, Siril `-rotate` |
| 6. Mitteln | bilinear auf das erste Bild verzerren, laufender Mittelwert mit Abdeckung je Pixel; optional hellsten Wert je Pixel verwerfen | OpenLiveStacker „Remove Satellites“ |
| 7. Anzeige | Weißabgleich der Kamera, je Farbe Hintergrund neutral, Midtones Transfer Function (Schatten: Median − 2,8 · MAD, Hintergrund auf 25 %) | Siril/PixInsight Autostretch |

JPEGs gehen ab Schritt 4 ohne Kalibrierung (JPEGs sind nicht linear). Bilder mit weniger als 6 zugeordneten
Sternen werden verworfen (Wolken, Verwackeln) und in der Liste mit Grund angezeigt.

**Dark-Optimierung verstehen:** k ist der Faktor mit dem kleinsten Gesamtrauschen – nicht das reine
Temperaturverhältnis. Sind die Darks gut (viele, lange Zeit), liegt k nahe am Verhältnis des Wärmesignals (z. B.
0,5 bei 6 °C kälteren Lights). Bei wenigen, kurzen Darks bringt der Master eigenes Rauschen mit, dann ist k
kleiner – das ist Absicht. k steht je Bild in der Liste.

**Gemessen** (MacBook M1): ein 6D-RAW braucht ~1,3 s (Laden 0,3 s, Hotpixel 0,14 s, Sterne 0,09 s, Verzerren
0,4 s, Anzeige 0,2 s) – wenig gegen ~25 s Download über den Hotspot. Der Kamera-Thread wird dabei höchstens 7 ms
aufgehalten (Bulb-Stopp bleibt pünktlich). Ausrichtung im Test: 0,001° und 0,02 px Fehler.

**Grenzen:** Mittelwert ohne Ausreißerverwerfung (außer „Satelliten“) – für das Endbild die RAWs in Siril
stapeln. Keine Flats im Live-Stack (noch). Der Vordergrund verschmiert, weil auf die Sterne ausgerichtet wird.

## 5. Dark- & Bias-Bibliothek

```
~/Pictures/6D-Tethering/Astro/Bibliothek/
  bias/   MASTER-BIAS_ISO3200_21C_n40_20261118-1530.fits
  darks/  MASTER-DARK_ISO3200_30s_14C_n20_20261118-2350.fits
```

Alle Angaben stehen im FITS-Kopf (`ISOSPEED`, `EXPTIME`, `CCD-TEMP`, `TEMPMIN/MAX`, `NCOMBINE`, `INSTRUME`,
`DATE-OBS`, `BAYERPAT`, `MEDIAN`, `NOISE`, `HOTPIX`); 32-Bit-Gleitkomma 0…1 wie bei Siril. Die Karte zeigt die
Masters als Tabelle; ✓ markiert, was zu den letzten Lights passt. Löschen per ✕.

**Sensortemperatur:** aus dem Canon-MakerNote der CR2 (CameraInfo, Byte 0x1B − 128 = °C). Für die 6D an einer
echten CR2 geprüft – ISO, Blende und Zeit an den Nachbarpositionen stimmen mit den EXIF-Werten überein.

**Automatische Auswahl** beim ersten RAW eines Live-Stacks (gleiche Kamera und Bildgröße):
- **Bias:** gleiche ISO, das neueste.
- **Dark:** gleiche ISO; zuerst gleiche Zeit (±5 %), dann die nächste Temperatur, dann das neueste. Mit
  Dark-Optimierung auch Darks mit 0,5- bis 4-facher Zeit (k gleicht aus), gleiche Zeit bleibt bevorzugt.

**Master erstellen** (`calib.build_master`): zwei Durchgänge über die RAWs, ohne alle 20-MP-Bilder gleichzeitig
im Speicher. Durchgang 1: robuste Mitte und Streuung je Pixel ohne Minimum und Maximum (bei 3–4 Bildern = Median;
die Streuung wird für das Weglassen korrigiert und ist nie kleiner als das Rauschen eines Einzelbilds).
Durchgang 2: Mittelwert aller Werte innerhalb 3σ. Ein Treffer kosmischer Strahlung fällt so auch bei nur 4 Bildern
heraus; ohne Ausreißer ist das Ergebnis gleich dem einfachen Mittel (Abweichung 0,17 ADU bei 10 Bildern).

**Geführte Aufnahme** („Geführt aufnehmen …“):

1. **Was?** Bias oder Darks – mit Erklärung, wann und wie viele.
2. **Kamera vorbereiten.** Automatisch geprüft: Moduswahlrad (Bias: M, Darks: B oder M), ISO wie die letzten
   Lights, Belichtungszeit wie die Lights, Temperatur der Lights. Darks unter 1 s sperrt der Assistent (auch der
   Server und „Master erstellen“): Das wären nur Bias – typisch, wenn die Kamera nach den Bias noch auf 1/4000
   steht. Gibt es noch keine Lights, erinnert er daran, die Zeit der Lights einzustellen. Zum Abhaken:
   Objektivdeckel drauf, Sucher abgedeckt (Okularabdeckung am Gurt), **Rauschreduzierung bei Langzeitbelichtung
   aus** (sonst macht die 6D nach jedem Bild selbst ein Dark), Bildqualität RAW.
3. **Aufnehmen.** Anzahl (Bias 40, Darks 20) und bei Darks im Modus B die Zeit (vorbelegt mit der Zeit der
   Lights); Anzeige der Dauer und der Download-Zeit (~25 s je RAW über den Hotspot). Die Serie lädt nur RAWs.
4. **Master erstellen** startet von selbst, sobald alle RAWs auf dem Mac sind, und trägt den Master in die
   Bibliothek ein. Der nächste Live-Stack nimmt ihn automatisch.

**Aus Ordner:** Liegen Darks/Bias schon in einem Sitzungsordner (z. B. von der Karte kopiert), baut „Master
erstellen“ daraus Masters – gruppiert nach ISO und Zeit, je Gruppe ab 3 Bildern.

**Empfehlung:** Bias je benutzter ISO einmal zu Hause (40–50), hält lange. Darks am Ende jeder Nacht (15–30) mit
der Zeit der Lights; über die Zeit wächst so eine Bibliothek über viele Temperaturen, aus der die Auswahl für
spätere Nächte das passende Dark nimmt – die Dark-Optimierung gleicht den Rest aus.

## 6. Ohne Kamera ausprobieren

```bash
6d-fernsteuerung sim --raw --sky-speed 60 --focus 0   # RAWs mit Sensordaten, Himmel dreht 60× schneller
6d-fernsteuerung sim-cmd cap on                       # Deckel drauf → Darks/Bias
6d-fernsteuerung sim-cmd temp 20                      # wärmer → mehr Wärmesignal
```

Der Simulator erzeugt RAWs (DNG-Inhalt mit Endung .CR2, 1368 × 912) mit Offset und Spaltenmuster,
Ausleserauschen, Hotpixeln (Wärmesignal verdoppelt sich alle 6 °C, wächst mit ISO und Zeit; die Kamera erwärmt
sich beim Belichten), Lichtverschmutzung und Sternen, die sich um den Himmelspol drehen. Die Tests
(`tests/test_livestack.py`, `tests/test_simulator.py`) prüfen damit u. a.: k = 0,5 bei 6 °C kälteren Lights,
Drehung 3,76° nach 15 min, Master mit kosmischem Treffer, geführte Darks → Master → automatische Auswahl im
Live-Stack.

**Noch offen:** Flats/Dark-Flats im Live-Stack und als Masters; Abgleich der Anzeige zwischen RAW und JPEG; an der
echten 6D: Download-Zeit der RAWs im Live-Betrieb und die Temperaturwerte über eine Nacht.

## Quellen

- Siril: [Live stacking](https://siril.readthedocs.io/en/latest/Livestack.html),
  [Kalibrierung und Dark-Optimierung](https://siril.readthedocs.io/en/latest/preprocessing/calibration.html),
  [Quelltext der Dark-Optimierung](https://gitlab.com/free-astro/siril/-/blob/master/src/core/preprocess.c),
  [Skripte und Ordnerstruktur](https://siril.org/tutorials/first-steps/), [FAQ zu DSLR-Darks](https://siril.org/faq/)
- Beispiel-RAW der 6D zum Prüfen von rawpy und Temperatur: [raw.pixls.us](https://raw.pixls.us/data/Canon/EOS%206D/)
- SharpCap: [Live Stacking](https://docs.sharpcap.co.uk/2.9/13_LiveStacking.htm),
  [Forum: Kalibrierung live](https://forums.sharpcap.co.uk/viewtopic.php?p=26404)
- ASIAIR: [Review Live Stacking](https://www.zwoastro.com/2020/04/21/review-live-stacking-with-the-asiair-pro-by-david-parks/),
  [Eastwind Astro](https://eastwindastro.blogspot.com/2021/03/asiair-pro-live-stacking.html)
- OpenLiveStacker: [Handbuch](https://github.com/artyom-beilis/OpenLiveStacker/wiki/Open-Live-Stacker-Manual)
- ALS: [Projekt (Mirror)](https://github.xiaohuzi123231346.workers.dev/deufrai/als)
- N.I.N.A.: [Imaging-Optionen / Dateimuster](https://nighttime-imaging.eu/docs/master/site/tabs/options/imaging/)
- Dark-Temperatur: [Astro Pixel Processor Forum](https://www.astropixelprocessor.com/community/main-forum/exif-data-canon-sensor-temperature/),
  [Dark Master](https://sourceforge.net/p/darkmaster/wiki)
