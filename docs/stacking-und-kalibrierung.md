# Serien für Stacking, Kalibrierbilder und Live-Stacking

Stand 18.11.2026. Teil 1 beschreibt, was 6D Studio jetzt kann. Teil 2 ist der empfohlene Ablauf für eine Nacht.
Teil 3 vergleicht, wie andere Programme live stacken, und Teil 4 schlägt vor, wie wir das einbauen.

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

## 4. Vorschlag: Live-Stacking in 6D Studio

**Bildquelle:** Die Kamera auf **RAW + kleines JPEG** (S2/S3) stellen. Das Studio lädt **nur das JPEG** für den
Live-Stack (wenige hundert KB statt 25 MB, passt zu ~1 MB/s über den Hotspot). Die RAWs bleiben auf der Karte
für die spätere Ausarbeitung; `serie.csv` verbindet beide über den Namen.

**Zwei Wege:**

1. **Siril als Rechenkern (empfohlen für den Anfang):** Das Studio startet `siril-cli` im Hintergrund mit
   `start_ls -dark=<Master-Dark> [-rotate]` und übergibt jedes neue Light mit `livestack <datei>`.
   Das Zwischenergebnis wird als JPEG gespeichert und in der Oberfläche angezeigt. Vorteile: RAW-Unterstützung,
   Debayer, Kalibrierung und Registrierung sind fertig und gut getestet. Nachteil: Siril muss installiert sein
   (`brew install --cask siril`).
2. **Eigene, schlanke Pipeline mit numpy** (optional in der venv) auf den kleinen JPEGs:
   Master-Dark aus den Dark-JPEGs mitteln und abziehen, helle Sterne finden, Verschiebung über die Sternpositionen
   (später Drehung) bestimmen, laufend mitteln und mit einer automatischen Streckung (asinh) anzeigen.
   Ohne externe Programme, aber mehr eigener Code.

**Ablauf in der Oberfläche:** In der Serien-Karte „Live-Stack“ einschalten. Bei Lights wird jedes Bild nach dem
Download sofort eingerechnet; daneben stehen Anzahl, verworfene Bilder (zu wenige Sterne) und die Gesamtzeit.
Master-Darks und -Bias werden aus den Ordnern `darks/` und `biases/` der Sitzung oder aus einer
**Dark-Bibliothek** (`Astro/_bibliothek/darks/ISO3200_30s/`) gebaut und nach ISO und Zeit ausgewählt, später auch
nach EXIF-Temperatur.

**Reihenfolge für die Umsetzung:**
1. Serien + Namenskonvention (✔︎ erledigt)
2. Download nur der JPEGs bei RAW+JPEG (Schalter in der Serie)
3. Live-Stack mit Siril (`siril-cli`) inklusive Anzeige
4. Dark-Bibliothek und automatische Auswahl
5. Bildfelddrehung bei langen Serien vom Stativ

## Quellen

- Siril: [Live stacking](https://siril.readthedocs.io/en/latest/Livestack.html),
  [Skripte und Ordnerstruktur](https://siril.org/tutorials/first-steps/), [FAQ zu DSLR-Darks](https://siril.org/faq/)
- SharpCap: [Live Stacking](https://docs.sharpcap.co.uk/2.9/13_LiveStacking.htm),
  [Forum: Kalibrierung live](https://forums.sharpcap.co.uk/viewtopic.php?p=26404)
- ASIAIR: [Review Live Stacking](https://www.zwoastro.com/2020/04/21/review-live-stacking-with-the-asiair-pro-by-david-parks/),
  [Eastwind Astro](https://eastwindastro.blogspot.com/2021/03/asiair-pro-live-stacking.html)
- OpenLiveStacker: [Handbuch](https://github.com/artyom-beilis/OpenLiveStacker/wiki/Open-Live-Stacker-Manual)
- ALS: [Projekt (Mirror)](https://github.xiaohuzi123231346.workers.dev/deufrai/als)
- N.I.N.A.: [Imaging-Optionen / Dateimuster](https://nighttime-imaging.eu/docs/master/site/tabs/options/imaging/)
- Dark-Temperatur: [Astro Pixel Processor Forum](https://www.astropixelprocessor.com/community/main-forum/exif-data-canon-sensor-temperature/),
  [Dark Master](https://sourceforge.net/p/darkmaster/wiki)
