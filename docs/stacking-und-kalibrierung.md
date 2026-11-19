# Serien für Stacking, Kalibrierbilder und Live-Stacking

Stand 19.11.2026. Teil 1 beschreibt die Serien. Teil 2 ist der empfohlene Ablauf für eine Nacht. Teil 3
vergleicht, wie andere Programme live stacken. Teil 4 beschreibt den Live-Stack von 6D Studio mit Bildanalyse,
Teil 5 die Kalibrier-Bibliothek (Bias, Darks, Flats, Dark-Flats) mit geführter Aufnahme.

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

**Bedienung:** Karte „Live-Stack“ → Bias/Dark/Flat wählen (Vorgabe: automatisch aus der Bibliothek) → **Starten**.
Danach wird jedes Light eingerechnet, das auf den Mac kommt (Serie oder einzelne Bulb-Aufnahme). Oben
„Anzeige: Live-Stack“ (Taste <kbd>V</kbd>) zeigt das Ergebnis, darunter Helligkeit und Schwarzpunkt (nur Anzeige).
**Beenden & speichern** schreibt `LIVESTACK_<Zeit>_<n>x<Zeit>_raw.fits` (linear, 32 Bit) und `.png` (gestreckt)
nach `Astro/<Nacht>_<Objekt>/livestack/`. **Nachstacken** rechnet vorhandene Lights einer Sitzung ein –
praktisch zum Ausprobieren ohne Kamera.

**Je Bild (RAW):**

| Schritt | Wie | Vorbild |
|---|---|---|
| 1. Bias abziehen | Master-Bias gleicher ISO, sonst Schwarzwert der Kamera (2047) | Siril „=2048“ |
| 2. Dark abziehen | Wärmesignal = Master-Dark − Bias, mal **k**. Dark-Optimierung: k ∈ [0, 4] per Goldener-Schnitt-Suche (Toleranz 0,001), kleinste Streuung im 512×512-Feld der Bildmitte, je Farbe des Bayer-Musters – **ohne die Pixel der Hotpixel-Karte** (Schritt 3 ersetzt sie ohnehin) | Siril `-opt` |
| 3. Flat teilen | durch den Master-Flat, **je Farbkanal des Bayer-Musters auf 1 normiert** (Median der Bildmitte) – gleicht Vignettierung und Staubschatten aus, ohne die Farben zu verschieben, auch wenn die Lichtquelle farbig war; Werte unter 0,05 (tote Ecken) werden nicht geteilt | Siril `-flat`, `-equalize_cfa` |
| 4. Hotpixel | aus dem Wärmesignal (Median + 5σ, höchstens 1 %), ersetzt durch die 4 gleichfarbigen Nachbarn; dazu ein Filter je Bild für Reste und kosmische Strahlung (Sterne bleiben, weil ihr Licht mehrere Pixel trifft) | Siril „cosmetic correction“ |
| 5. Debayer | Superpixel (halbe Auflösung, 6D: 2748 × 1835) | Siril „Superpixel“ |
| 6. Ausrichten | bis 100 Sterne (Blockhintergrund, 5σ, Schwerpunkt), Dreiecke aus je 5 Nachbarn, log. Seitenverhältnisse vergleichen → grobe Ähnlichkeit (Verschiebung + Drehung); dann **Homographie** über alle Sterne, Toleranz schrittweise 16 → 2 px | astroalign, Siril `-transf=homography` |
| 7. Mitteln | bilinear auf das erste Bild verzerren, laufender Mittelwert mit Abdeckung je Pixel; optional hellsten Wert je Pixel verwerfen | OpenLiveStacker „Remove Satellites“ |
| 8. Anzeige | **Hintergrund ebnen** (Ebene + radiale Vignettierung, an Block-Mediane angepasst, helle Bereiche ausgespart), Weißabgleich der Kamera, Midtones Transfer Function je Farbe (Schatten: Median − 2,8 · MAD, Hintergrund auf 25 %) | Siril/PixInsight Autostretch, Siril Hintergrundextraktion |
| 9. Bildanalyse | alle 10 Bilder, am Ende und per „Bild prüfen“ – siehe unten | – |

**Warum Homographie:** Auf dem Stativ dreht sich der Himmel um die Kamera. Ein (verzeichnungsfreies) Objektiv
bildet eine solche Drehung exakt als projektive Abbildung ab (Bild = K·R·K⁻¹), nicht als Drehen + Verschieben.
Bei Teleobjektiven ist der Unterschied winzig, bei Weitwinkel groß: gemessen an der 6D mit 24 mm nach 0,7°
Bildfelddrehung lagen Sterne am Rand mit Drehen + Verschieben **7 px** daneben (Doppelsterne zum Rand hin), mit
Homographie **0,2 px**.

**Warum Hintergrund ebnen:** Die automatische Streckung setzt den Schwarzpunkt knapp unter den Median des
Himmels. Was dunkler ist – Ecken mit Vignettierung, die Seite gegenüber der Lichtverschmutzung – wird schwarz
abgeschnitten, umso mehr, je rauschärmer der Stapel wird. Gemessen: Ecken 15 % dunkler bei f/4. Das Ebnen
wirkt nur auf die Anzeige und das PNG; im FITS bleibt alles linear, dort korrigieren Flats die Vignettierung.

JPEGs gehen ab Schritt 5 ohne Kalibrierung (JPEGs sind nicht linear). Bilder mit weniger als 6 zugeordneten
Sternen werden verworfen (Wolken, Verwackeln) und in der Liste mit Grund angezeigt.

**Dark-Optimierung verstehen:** k ist der Faktor mit dem kleinsten Gesamtrauschen – nicht das reine
Temperaturverhältnis. Sind die Darks gut (viele, lange Zeit), liegt k nahe am Verhältnis des Wärmesignals (z. B.
0,5 bei 6 °C kälteren Lights). Bei wenigen, kurzen Darks bringt der Master eigenes Rauschen mit, dann ist k
kleiner – das ist Absicht. k steht je Bild in der Liste. Die Hotpixel bleiben bei der Suche außen vor: An der
echten 6D (3 s, Darks 27 °C, Lights 35–40 °C) trieben sie k an den alten Anschlag 2, ohne sie ergab sich 0,55 –
bei gleichem Rauschen im Stapel, denn die Hotpixel ersetzt ohnehin die Hotpixel-Karte.

**Bildanalyse** (Karte „Live-Stack“ → „Bildanalyse“, `studio/analysis.py`): prüft das Stapelbild und die
Kalibrierung und sagt, was zu tun ist – ✓ in Ordnung, i Hinweis, ! Verbesserung möglich:

| Prüfung | Gemessen | Empfehlung, z. B. |
|---|---|---|
| Bias | Master vorhanden? Anzahl | 30–50 Bias bei der ISO der Lights |
| Dark – Temperatur | Darks vs. Lights (Sensortemperatur aus der CR2), ab 5 °C | Darks direkt nach den Lights – die 6D heizt sich im Livebild um 10 °C auf |
| Dark – Faktor | k am Anschlag 0 oder 4? | Darks passen nicht – neu aufnehmen |
| Dark – Wärmesignal | Wärmesignal im Light vs. Rauschen | kurze Belichtung: Dark wirkt v. a. als Hotpixel-Karte, Anzahl zählt |
| Dark – Rauschgrenze | Rauschen des Masters × k gegen Einzelbild: ab N ≈ (σ_Bild / (k·σ_Master))² Bildern begrenzt der Master | mehr Darks – die Grenze wächst mit ihrer Anzahl |
| Rauschgewinn | Stapel-Rauschen vs. Einzelbild/√N (je Bild nach dem Verzerren gemessen) | festes Muster → passende Kalibrierung, Dithering |
| Hintergrund | Vignettierung (Ecken vs. Mitte), Gradient, helle Bereiche je Neuntel, Überbelichtung | Flats; Lichtquelle/Wolke |
| Flat | mit Master-Flat: was an Vignettierung übrig bleibt (ab ±5 %), Helligkeit der Flats | Rest dunkel: Blende/Fokus/Brennweite anders als bei den Lights; Ecken heller (Überkorrektur): Offset fehlt → Bias oder Dark-Flats, Master-Flat neu; Flats unter 20 %: Belichtungskorrektur +1…+2 EV |
| Sterne | FWHM und Form in Mitte und Rand, Stapel vs. Einzelbild, Restfehler der Ausrichtung | Abblenden (Koma), Stativ/Wind |
| Bilder | verworfene, Wolken (Hintergrund +20 % oder −40 % Sterne), Temperaturverlauf; ab 3 verworfenen Bildern mit je 30+ Sternen: anderer Bildausschnitt | Bilder aussortieren; nach neuem Ausrichten „Neu beginnen“ |

Ergebnis an der echten 6D (50 × 3 s, ISO 800, f/4): Darks 9 °C kälter (bei 3 s unkritisch), Master-Dark begrenzt
ab ~56 Bildern (mit 50 statt 20 Darks ab ~140), Ecken 15 % dunkler, Rauschen sinkt wie √N, Sterne bis in die
Ecken rund.

**Gemessen** (MacBook M1): ein 6D-RAW braucht ~1,4 s (Laden 0,3 s, Hotpixel 0,14 s, Sterne 0,1 s, Verzerren
0,5 s, Anzeige 0,2 s), die Bildanalyse ~1 s alle 10 Bilder – wenig gegen ~25 s Download über den Hotspot. Der
Kamera-Thread wird dabei höchstens 7 ms aufgehalten (Bulb-Stopp bleibt pünktlich). Restfehler der Ausrichtung an
der echten 6D: 0,22 px im Median.

**Grenzen:** Mittelwert ohne Ausreißerverwerfung (außer „Satelliten“) – für das Endbild die RAWs in Siril
stapeln. Der Vordergrund verschmiert, weil auf die Sterne ausgerichtet wird.

## 5. Kalibrier-Bibliothek

```
~/Pictures/6D-Tethering/Astro/Bibliothek/
  bias/       MASTER-BIAS_ISO3200_21C_n40_20261118-1530.fits
  darks/      MASTER-DARK_ISO3200_30s_14C_n20_20261118-2350.fits
  flats/      MASTER-FLAT_24mm_f4_ISO400_n25_20261119-0130.fits
  darkflats/  MASTER-DARKFLAT_ISO400_1-50s_30C_n25_20261119-0140.fits
```

Alle Angaben stehen im FITS-Kopf (`ISOSPEED`, `EXPTIME`, `CCD-TEMP`, `TEMPMIN/MAX`, `NCOMBINE`, `INSTRUME`,
`DATE-OBS`, `BAYERPAT`, `MEDIAN`, `NOISE`, `HOTPIX`; Optik `FNUMBER`, `FOCALLEN`, `LENS`; bei Flats `FLATLVL`
(Helligkeit), `VIGNET` (Lichtabfall zur Ecke), `FLATCAL` (was abgezogen wurde); `SOURCE`/`FIRSTIMG` (Sitzung und
erstes Bild); 32-Bit-Gleitkomma 0…1, Zeilen von unten (FITS-Standard, wie Siril RAWs einliest). Die Karte zeigt die Masters als Tabelle (Spalte „Zeit / Optik“:
bei Flats Brennweite und Blende); ✓ markiert, was zu den letzten Lights passt – bei Dark-Flats, was zu den letzten
Flats passt. Löschen per ✕.

**Optik** (Blende, Brennweite, Objektiv) kommt aus dem EXIF der CR2 (`FNumber`, `FocalLength`, `LensModel`).
Manuelle Objektive ohne Kontakte melden keine Blende – dann zählt nur, was bekannt ist.

**Sensortemperatur:** aus dem Canon-MakerNote der CR2 (CameraInfo, Byte 0x1B − 128 = °C). Für die 6D an einer
echten CR2 geprüft – ISO, Blende und Zeit an den Nachbarpositionen stimmen mit den EXIF-Werten überein.

**Automatische Auswahl** beim ersten RAW eines Live-Stacks (gleiche Kamera und Bildgröße):
- **Bias:** gleiche ISO, das neueste.
- **Dark:** gleiche ISO; zuerst gleiche Zeit (±5 %), dann die nächste Temperatur, dann das neueste. Mit
  Dark-Optimierung auch Darks mit 0,5- bis 4-facher Zeit (k gleicht aus), gleiche Zeit bleibt bevorzugt.
- **Flat:** gleiche Optik – Blende (±6 %), Brennweite (±3 %) und Objektiv müssen passen, soweit beide sie kennen;
  vollständig bekannte Optik zuerst, dann das neueste. ISO und Zeit spielen keine Rolle (der Flat wird normiert).
  Passt keiner, rechnet der Live-Stack ohne Flat und sagt das in der Kalibrierzeile.
- **Dark-Flat** (für den Master-Flat): gleiche ISO wie die Flats, Zeit ±25 % (Av schwankt um Drittelstufen),
  nächste Temperatur, das neueste.

**Master-Flat:** Von jedem Flat wird der Master-Dark-Flat abgezogen, sonst der Master-Bias gleicher ISO, sonst
der Schwarzwert. Dann wird jedes Flat auf die Helligkeit des ersten gebracht (Median des Grüns im mittleren
Drittel) – im Modus Av schwankt die Zeit, und eine Tablet-Lichtquelle flackert. Erst danach das κσ-Mittel wie
unten. Gespeichert wird in ADU ohne Offset; die Helligkeit (Anteil bis zur Sättigung, ideal 30–60 %) und der
Lichtabfall zur Ecke stehen im Kopf und in der Tabelle. Kommen Dark-Flats erst nach den Flats, baut das Studio
den Master-Flat dieser Sitzung danach automatisch neu – jetzt mit Dark-Flat – und ersetzt den alten.

**Master erstellen** (`calib.build_master`): zwei Durchgänge über die RAWs, ohne alle 20-MP-Bilder gleichzeitig
im Speicher. Durchgang 1: robuste Mitte und Streuung je Pixel ohne Minimum und Maximum (bei 3–4 Bildern = Median;
die Streuung wird für das Weglassen korrigiert und ist nie kleiner als das Rauschen eines Einzelbilds).
Durchgang 2: Mittelwert aller Werte innerhalb 3σ. Ein Treffer kosmischer Strahlung fällt so auch bei nur 4 Bildern
heraus; ohne Ausreißer ist das Ergebnis gleich dem einfachen Mittel (Abweichung 0,17 ADU bei 10 Bildern).

**Geführte Aufnahme** („Geführt aufnehmen …“):

1. **Was?** Bias, Darks, Flats oder Dark-Flats – mit Erklärung, wann und wie viele.
2. **Kamera vorbereiten.** Automatisch geprüft:
   - **Bias:** Moduswahlrad M, ISO wie die Lights; das Studio stellt 1/4000 ein.
   - **Darks:** Moduswahlrad B oder M, ISO und Zeit wie die Lights, Temperatur der Lights. Darks unter 1 s sperrt
     der Assistent (auch der Server und „Master erstellen“): Das wären nur Bias – typisch, wenn die Kamera nach den
     Bias noch auf 1/4000 steht. Gibt es noch keine Lights, erinnert er daran, die Zeit der Lights einzustellen.
   - **Flats:** Moduswahlrad Av (oder M), Blende wie die Lights (Warnung, wenn nicht), ISO bis 800. Nach dem
     ersten Flat misst das Studio die Helligkeit und meldet „zu dunkel: +1…+2 EV“ oder „gut“.
   - **Dark-Flats:** Moduswahlrad M; das Studio stellt ISO und Zeit der letzten Flats selbst ein. Bei Flats unter
     1 s weist es darauf hin, dass der Master-Bias meist reicht.

   Zum Abhaken je Typ, z. B. Objektivdeckel drauf, Sucher abgedeckt (Okularabdeckung am Gurt),
   **Rauschreduzierung bei Langzeitbelichtung aus** (sonst macht die 6D nach jedem Bild selbst ein Dark),
   Bildqualität RAW; bei Flats: gleichmäßige Lichtquelle, Fokus/Blende/Brennweite/Drehung unverändert,
   Belichtungskorrektur +1 EV.
3. **Aufnehmen.** Anzahl (Bias 40, Darks 20, Flats und Dark-Flats 25) und bei Darks im Modus B die Zeit (vorbelegt
   mit der Zeit der Lights); Anzeige der Dauer und der Download-Zeit (~25 s je RAW über den Hotspot). Die Serie lädt
   nur RAWs.
4. **Master erstellen** startet von selbst, sobald alle RAWs auf dem Mac sind, und trägt den Master in die
   Bibliothek ein. Der nächste Live-Stack nimmt ihn automatisch.

**Aus Ordner:** Liegen Kalibrierbilder schon in einem Sitzungsordner (z. B. von der Karte kopiert), baut „Master
erstellen“ daraus Masters – gruppiert nach ISO und Zeit (Flats nur nach ISO), je Gruppe ab 3 Bildern. Bei Flats
entsteht zuerst der Master-Dark-Flat, wenn die Sitzung Dark-Flats enthält. Wird derselbe Ordner noch einmal
gebaut, ersetzt der neue Master den alten.

**Empfehlung:** Bias je benutzter ISO einmal zu Hause (40–50), hält lange. Darks am Ende jeder Nacht (15–30) mit
der Zeit der Lights; über die Zeit wächst so eine Bibliothek über viele Temperaturen, aus der die Auswahl für
spätere Nächte das passende Dark nimmt – die Dark-Optimierung gleicht den Rest aus. **Flats in jeder Nacht** vor
dem Abbauen (20–30): Staub wandert, und schon ein anderer Fokus ändert die Vignettierung. Dark-Flats nur bei
Flat-Zeiten ab etwa 1 s.

## 6. Ohne Kamera ausprobieren

```bash
6d-fernsteuerung sim --raw --sky-speed 60 --focus 0   # RAWs mit Sensordaten, Himmel dreht 60× schneller
6d-fernsteuerung sim-cmd cap on                       # Deckel drauf → Darks/Bias
6d-fernsteuerung sim-cmd temp 20                      # wärmer → mehr Wärmesignal
6d-fernsteuerung sim-cmd mode Av                      # Zeitautomatik für Flats
6d-fernsteuerung sim-cmd panel on                     # Leuchtfläche vor dem Objektiv → Flats
```

Der Simulator erzeugt RAWs (DNG-Inhalt mit Endung .CR2, 1368 × 912, mit Blende, Brennweite und Objektiv im EXIF):
- Offset und Spaltenmuster, Ausleserauschen.
- Hotpixel: Das Wärmesignal verdoppelt sich alle 6 °C und wächst mit ISO und Zeit; die Kamera erwärmt sich beim
  Belichten.
- Ein Objektiv mit Vignettierung (Ecken −30 %) und dem ringförmigen Schatten eines Staubkorns.
- Lichtverschmutzung und Sterne, die sich um den Himmelspol drehen.
- Im Modus Av belichtet er die Leuchtfläche automatisch auf 40 %.

Die Tests (`tests/test_livestack.py`, `tests/test_simulator.py`) prüfen damit u. a.:
- k = 0,5 bei 6 °C kälteren Lights, Drehung 3,76° nach 15 min, Master mit kosmischem Treffer.
- Geführte Darks → Master → automatische Auswahl im Live-Stack.
- Master-Flat mit Dark-Flat: Normierung je Farbkanal, Staubring sichtbar, kein passender Flat bei anderer Blende.
- Geführte Flats in Av → Rückmeldung 40 % → Dark-Flats mit von selbst gestellter ISO und Zeit → Master-Flat neu mit
  Dark-Flat → Live-Stack wählt ihn, die Analyse meldet „Flat gleicht die Vignettierung aus“.

**Noch offen:** Abgleich der Anzeige zwischen RAW und JPEG; an der echten 6D: Download-Zeit der RAWs im
Live-Betrieb, die Temperaturwerte über eine Nacht und Flats mit echter Lichtquelle.

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
