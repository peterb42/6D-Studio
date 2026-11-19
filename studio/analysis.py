"""Bildanalyse für den Live-Stack: prüft Stapelbild und Kalibrierung und gibt Empfehlungen.

Jeder Befund: {"level": ok|info|warn|bad, "topic", "title", "text", "advice"}. Gemessen wird, was die Pipeline
ohnehin kennt – je Bild Temperatur, Dark-Faktor k, Hintergrund, Rauschen, Sterne, Restfehler der Ausrichtung –
plus das Stapelbild selbst: Hintergrundmodell (Vignettierung, Gradient, helle Bereiche), Rauschen im Vergleich
zu √N, Sternbreite und -form in Mitte und Ecken.
"""
import time

import calib
import livestack
from imaging import np

BIAS_MIN_N = 20             # Master-Bias aus weniger Bildern → mehr empfehlen
DARK_MIN_N = 15
TEMP_DELTA = 5.0            # °C zwischen Darks und Lights, ab dem es zählt
VIGNETTE = (0.10, 0.25)     # Ecken dunkler als die Mitte: Hinweis / Warnung
GRADIENT = 0.10             # Hintergrund von Rand zu Rand
FLAT_RESIDUAL = 0.05        # mit Flat: Ecken noch so viel dunkler/heller → Flat passt nicht
BRIGHT_SECTOR = 0.15        # Bereich heller als das Hintergrundmodell
GAIN = (0.75, 0.5)          # Rauschgewinn gegenüber √N: Hinweis / Warnung
FWHM_RATIO = 1.4            # Sterne in den Ecken breiter als in der Mitte
ELONG = 1.5                 # Sterne in den Ecken länglich
RMS_PX = 0.6                # Restfehler der Ausrichtung
SECTORS = (("oben links", "oben", "oben rechts"), ("links", "Mitte", "rechts"),
           ("unten links", "unten", "unten rechts"))


def de(x, nd=1):
    """Zahl mit Dezimalkomma."""
    return ("%.*f" % (nd, x)).replace(".", ",")


def pct(x):
    return "%d %%" % round(100 * x)


def finding(level, topic, title, text="", advice=""):
    return {"level": level, "topic": topic, "title": title, "text": text, "advice": advice}


def median(values):
    v = [x for x in values if x is not None]
    return float(np.median(v)) if v else None


def analyze(snap):
    """snap: von LiveStack.snapshot(). → {"time", "frames", "items": [Befunde], "summary"}."""
    items = []
    stats = snap["stats"]
    if snap["kind"] == "raw":
        items += check_bias(snap)
        items += check_dark(snap, stats)
    else:
        items.append(finding("info", "Kalibrierung", "JPEG-Stapel ohne Kalibrierung",
                             "JPEGs sind nicht linear – Bias und Darks lassen sich nicht abziehen.",
                             "Für Kalibrierung und tiefere Stapel die Kamera auf RAW (oder RAW+JPEG) stellen."))
    items += check_noise(snap, stats)
    items += check_background(snap)
    items += check_stars(snap, stats)
    items += check_frames(snap, stats)
    order = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
    worst = min((order[i["level"]] for i in items), default=3)
    summary = {0: "Probleme gefunden", 1: "Verbesserungen möglich", 2: "Gut – mit Hinweisen", 3: "Alles in Ordnung"}
    return {"time": time.time(), "frames": snap["frames"], "summary": summary[worst],
            "items": sorted(items, key=lambda i: order[i["level"]])}


# ---------------------------------------------------------------- Kalibrierung
def check_bias(snap):
    c, iso = snap["calib"], snap["iso"]
    if not c.get("bias"):
        return [finding("warn", "Bias", "Kein Master-Bias für ISO %s" % iso,
                        "Ersatzweise wird der Schwarzwert der Kamera (%s) abgezogen – das feste Streifenmuster des "
                        "Sensors bleibt im Bild." % c.get("offset"),
                        "In der Bibliothek „Geführt aufnehmen …“ → Bias: 30–50 Bilder bei ISO %s. Geht jederzeit, "
                        "auch zu Hause – Bias ändern sich kaum." % iso)]
    n = c.get("bias_n") or 0
    if n and n < BIAS_MIN_N:
        return [finding("info", "Bias", "Master-Bias aus nur %d Bildern" % n,
                        "Das Rauschen des Masters geht in jedes Bild ein.",
                        "30–50 Bias aufnehmen und den neuen Master verwenden.")]
    return [finding("ok", "Bias", "Master-Bias ISO %s · %d Bilder" % (c.get("bias_iso") or iso, n))]


def check_dark(snap, stats):
    c, out = snap["calib"], []
    exposure, iso = snap["exposure"], snap["iso"]
    temps = [s["temp"] for s in stats if s.get("temp") is not None]
    if not c.get("dark"):
        long_exp = (exposure or 0) >= 10
        out.append(finding("warn" if long_exp else "info", "Dark", "Kein passender Master-Dark",
                           "Gesucht: ISO %s, %s s%s. Hotpixel werden nur je Bild herausgefiltert."
                           % (iso, de(exposure or 0), " bei ~%d °C" % round(median(temps)) if temps else ""),
                           "Darks mit gleicher ISO und Zeit direkt nach den Lights aufnehmen, solange die Kamera "
                           "gleich warm ist: „Geführt aufnehmen …“ → Darks, 20–30 Bilder."))
        return out
    dark_t, n = c.get("dark_temp"), c.get("dark_n") or 0
    ks = [s["k"] for s in stats if s.get("k") is not None]
    k = median(ks)
    thermal, dexp = c.get("thermal"), c.get("dark_exposure")
    noise = median(s.get("noise") for s in stats)
    weak = False                                      # Wärmesignal im Light kleiner als 1/10 des Rauschens?
    if thermal is not None and dexp and exposure and noise and k is not None:
        signal = abs(thermal) * k * exposure / dexp
        weak = signal < 0.1 * noise
    if temps and dark_t is not None:
        lo, hi = min(temps), max(temps)
        delta = median(temps) - dark_t
        span = "%d °C" % round(lo) if round(lo) == round(hi) else "%d–%d °C" % (round(lo), round(hi))
        if abs(delta) >= TEMP_DELTA:
            colder = delta > 0
            text = "Darks bei %d °C, Lights bei %s." % (round(dark_t), span)
            if weak:
                text += (" Bei %s s fällt das Wärmesignal kaum ins Gewicht (Dark-Faktor k = %s), aber Pixel, die erst"
                         " bei %d °C heiß werden, fehlen in der Hotpixel-Karte." % (
                             de(exposure, 0 if exposure >= 10 else 1), de(k or 0, 2), round(median(temps))))
            else:
                text += (" Die Dark-Optimierung gleicht das teilweise aus (k = %s); Hotpixel wachsen aber "
                         "unterschiedlich stark mit der Temperatur." % de(k or 0, 2))
            out.append(finding(
                "info" if weak else "warn", "Dark",
                "Darks %d °C %s als die Lights" % (round(abs(delta)), "kälter" if colder else "wärmer"), text,
                "Die 6D heizt sich im Livebild und bei Serien um 10 °C und mehr auf. Darks direkt im Anschluss an "
                "die Lights aufnehmen – Deckel drauf, gleiche Einstellungen, ohne Pause."))
        else:
            out.append(finding("ok", "Dark", "Temperatur passt: Darks %d °C, Lights %s" % (round(dark_t), span)))
    if k is not None and (k >= livestack.K_MAX - 0.02 or k <= 0.02) and not weak:
        out.append(finding("warn", "Dark", "Dark-Faktor am Anschlag (k = %s)" % de(k, 2),
                           "Der Master-Dark passt nicht zu diesen Lights (Temperatur, Zeit oder ISO).",
                           "Neue Darks passend zu den Lights aufnehmen."))
    # Das Rauschen des Masters steckt in jedem Bild gleich – ab N ≈ (Einzelbild / (k·Master))² Bildern ist es so
    # groß wie das Zufallsrauschen im Stapel; danach bringen mehr Lights immer weniger.
    dark_noise = c.get("dark_noise")
    if dark_noise and noise and k and k > 0.05:
        limit = (noise / (k * dark_noise)) ** 2
        frames = snap["frames"]
        if limit < 3 * max(frames, 30):
            out.append(finding("warn" if limit <= frames else "info", "Dark",
                               "Master-Dark begrenzt den Stapel ab etwa %d Bildern" % round(limit),
                               "Sein Rauschen (× k = %s ADU) steckt in jedem Bild gleich und mittelt sich nicht heraus;"
                               " das Einzelbild rauscht mit %s ADU." % (de(k * dark_noise, 2), de(noise, 1)),
                               "Mehr Darks aufnehmen – das Rauschen des Masters sinkt mit √n: 50 Darks statt %d "
                               "verschieben die Grenze auf etwa %d Bilder." % (n, round(limit * 50 / max(n, 1)))))
    if weak:
        out.append(finding("info", "Dark", "Wärmesignal vernachlässigbar (%s ADU bei %s s)"
                           % (de(abs(thermal) * k * exposure / dexp, 1), de(exposure, 0 if exposure >= 10 else 1)),
                           "Bei so kurzer Belichtung wirkt der Master-Dark vor allem als Hotpixel-Karte (%d Pixel)."
                           % (c.get("hot") or 0),
                           "Darks bleiben sinnvoll – wichtiger als die genaue Temperatur ist dann ihre Anzahl."))
    if n and n < DARK_MIN_N:
        out.append(finding("info", "Dark", "Master-Dark aus nur %d Bildern" % n, "",
                           "20–30 Darks verringern das Rauschen, das der Master in jedes Bild trägt."))
    elif n and not any(i["level"] in ("warn", "bad") for i in out):
        out.append(finding("ok", "Dark", "Master-Dark %s s · %d Bilder · k = %s"
                           % (de(dexp or 0, 1), n, de(k or 0, 2))))
    return out


# ---------------------------------------------------------------- Rauschen
def check_noise(snap, stats):
    n, stack_noise = len(stats), snap.get("stack_noise")
    single = [s["noise"] for s in stats if s.get("noise")]
    if n < 4 or not stack_noise or not single:
        return []
    expected = float(np.sqrt(np.mean(np.square(single)))) / n ** 0.5
    gain = expected / stack_noise
    text = "Einzelbild %s ADU → Stapel %s ADU aus %d Bildern (erwartet %s)." % (
        de(float(np.median(single)), 1), de(stack_noise, 2), n, de(expected, 2))
    if gain >= GAIN[0]:
        return [finding("ok", "Rauschen", "Rauschen sinkt wie erwartet (√%d)" % n, text)]
    return [finding("warn" if gain < GAIN[1] else "info", "Rauschen",
                    "Stapeln bringt weniger als erwartet (%s von √N)" % pct(gain),
                    text + " Ein festes Muster mittelt sich nicht heraus – es wandert mit dem Sensor und erscheint "
                    "als feine Striche („Walking Noise“).",
                    "Bias und Darks passend zu den Lights (Temperatur, Anzahl). Zwischen Serien den Bildausschnitt "
                    "leicht versetzen (Dithering).")]


# ---------------------------------------------------------------- Hintergrund
def check_background(snap):
    small, cov = livestack.shrink(snap["img"], snap["covered"])
    model, coef = livestack.sky_model(small, cov)
    if model is None:
        return []
    out = []
    h, w = small.shape[:2]
    half = np.hypot(w, h) / 2
    X, Y = (w / 2) / half, (h / 2) / half
    c = coef[1]                                       # Grün: am meisten Signal
    c0 = c[0]
    if c0 <= 0:
        return []
    vignette = -(c[3] + c[4]) / c0                    # radial, in den Ecken (r = 1)
    if snap["kind"] == "raw" and snap["calib"].get("flat"):
        out += check_flat(snap["calib"], vignette)
    elif vignette >= VIGNETTE[0]:
        out.append(finding("warn" if vignette >= VIGNETTE[1] else "info", "Hintergrund",
                           "Ecken %s dunkler als die Mitte (Vignettierung)" % pct(vignette),
                           "Kein Master-Flat für %s. Dunkle Ecken schneidet die Anzeige sonst schwarz ab; "
                           "„Hintergrund ebnen“ gleicht sie nur für die Anzeige aus."
                           % (snap["calib"].get("optics") or "diese Optik"),
                           "Flats korrigieren das im Ergebnis: Bibliothek → „Geführt aufnehmen …“ → Flats, mit "
                           "unverändertem Fokus und unveränderter Blende. Abblenden verringert die Vignettierung."))
    gx, gy = 2 * c[1] * X / c0, 2 * c[2] * Y / c0     # von Rand zu Rand
    if max(abs(gx), abs(gy)) >= GRADIENT:
        if abs(gy) >= abs(gx):
            side, g = ("unten" if gy > 0 else "oben"), abs(gy)
        else:
            side, g = ("rechts" if gx > 0 else "links"), abs(gx)
        out.append(finding("info", "Hintergrund", "Hintergrund %s %s heller" % (side, pct(g)),
                           "Gradient von Rand zu Rand – meist Horizont, Lichtverschmutzung oder Mond.",
                           "Die Anzeige ebnet ihn; im Ergebnis entfernt Sirils Hintergrundextraktion den Rest."))
    out += check_bright(small, cov, model, c0, snap["white"])
    if not out:
        out.append(finding("ok", "Hintergrund", "Hintergrund gleichmäßig",
                           "Vignettierung %s, Gradient unter %s." % (pct(max(vignette, 0)), pct(GRADIENT))))
    return out


def check_flat(c, residual):
    """Mit Master-Flat: Was an Vignettierung übrig bleibt, zeigt, ob der Flat passt."""
    out = []
    what = "Master-Flat %s · %s Bilder" % (c.get("flat_optics") or "", c.get("flat_n") or "?")
    if c.get("flat_vignette") is not None:
        what += " · Ecken ohne Flat %s dunkler" % pct(c["flat_vignette"])
    if residual >= FLAT_RESIDUAL:
        out.append(finding("warn", "Flat", "Flat gleicht die Vignettierung nicht ganz aus (Ecken noch %s dunkler)"
                           % pct(residual), what + ".",
                           "Flats mit derselben Blende, Brennweite und demselben Fokus wie die Lights aufnehmen und "
                           "dazwischen nichts am Objektiv verändern (Taukappe, Filter, Drehung)."))
    elif residual <= -FLAT_RESIDUAL:
        cal = c.get("flat_cal") or ""
        out.append(finding("warn", "Flat", "Flat korrigiert zu stark (Ecken %s heller)" % pct(-residual),
                           what + (". Beim Master-Flat wurde nur der Schwarzwert abgezogen." if cal.startswith(
                               "Schwarzwert") else "."),
                           "Meist fehlt beim Master-Flat oder bei den Lights der Abzug des Offsets: Bias (oder "
                           "Dark-Flats) aufnehmen und den Master-Flat neu erstellen (Bibliothek → „Aus Ordner“)."))
    else:
        out.append(finding("ok", "Flat", "Flat gleicht die Vignettierung aus (Rest %s)" % pct(abs(residual)), what))
    level = c.get("flat_level")
    if level is not None and not calib.FLAT_LEVEL[0] <= level <= calib.FLAT_LEVEL[1]:
        dark = level < calib.FLAT_LEVEL[0]
        out.append(finding("info", "Flat", "Flats %s belichtet (%s)" % ("zu dunkel" if dark else "sehr hell",
                                                                       pct(level)),
                           "Ideal sind 30–60 % bis zur Sättigung – dann ist das Flat rauscharm und sicher linear.",
                           "Im Modus Av die Belichtungskorrektur auf +1 bis +2 EV stellen." if dark else
                           "Belichtungskorrektur −1 EV oder die Lichtquelle dunkler."))
    return out


def check_bright(small, cov, model, c0, white):
    out = []
    h, w = small.shape[:2]
    g, m = small[..., 1], model[..., 1]
    excess, worst = [], (0.0, None)
    for r in range(3):
        for k in range(3):
            ys, xs = slice(r * h // 3, (r + 1) * h // 3), slice(k * w // 3, (k + 1) * w // 3)
            ok = cov[ys, xs]
            if ok.mean() < 0.5:
                continue
            e = float(np.median((g[ys, xs] - m[ys, xs])[ok])) / c0
            excess.append(e)
            if e > worst[0]:
                worst = (e, SECTORS[r][k])
    if worst[1] and worst[0] >= BRIGHT_SECTOR:
        out.append(finding("info", "Hintergrund", "Bereich %s %s heller als der übrige Himmel" % (worst[1],
                                                                                                pct(worst[0])),
                           "Wolke, Streulicht, Lampe oder die Milchstraße – der Hintergrundausgleich spart ihn aus.",
                           "Wolken: betroffene Bilder später aussortieren. Streulicht: Gegenlichtblende, "
                           "Lichtquelle aus dem Bild nehmen."))
    sat = small.max(2) >= 0.95 * white
    if sat.mean() >= 0.0005:
        rr, kk = np.unravel_index(np.argmax([[sat[r * h // 3:(r + 1) * h // 3, k * w // 3:(k + 1) * w // 3].sum()
                                              for k in range(3)] for r in range(3)]), (3, 3))
        out.append(finding("info", "Hintergrund", "Überbelichtet: %s %% des Bildes" % de(100 * sat.mean(), 1),
                           "Vor allem %s." % SECTORS[rr][kk],
                           "Helle Lichtquellen aus dem Bild nehmen oder kürzer belichten."))
    return out


# ---------------------------------------------------------------- Sterne und Bilder
def check_stars(snap, stats):
    out = []
    img = snap["img"]
    lum = img.sum(2)
    pts, fwhm, elong = livestack.find_stars(lum, max_stars=300, details=True)
    if len(pts) < 10:
        return [finding("warn", "Sterne", "Kaum Sterne im Stapel (%d)" % len(pts), "",
                        "Fokus prüfen (Livebild, Lupe 10×), Wolken?")]
    h, w = lum.shape
    half = np.hypot(w, h) / 2
    r = np.hypot(pts[:, 0] - w / 2, pts[:, 1] - h / 2) / half
    mid, edge = r < 0.35, r > 0.7
    single = median(s.get("fwhm") for s in stats)
    f_all = float(np.median(fwhm))
    rms = median(s.get("rms") for s in stats[1:])
    if mid.sum() >= 5 and edge.sum() >= 5:
        f_mid, f_edge = float(np.median(fwhm[mid])), float(np.median(fwhm[edge]))
        e_edge = float(np.median(elong[edge]))
        if f_edge > FWHM_RATIO * f_mid or e_edge > ELONG:
            out.append(finding("warn", "Sterne", "Sterne am Rand %s" % ("länglich" if e_edge > ELONG else "breiter"),
                               "Breite (FWHM) Mitte %s px, Rand %s px; Form am Rand %s : 1." % (
                                   de(f_mid), de(f_edge), de(e_edge)),
                               "Liegt es an der Optik, zeigen es auch die Einzelbilder: Abblenden hilft gegen Koma "
                               "und Bildfeldwölbung. Nur im Stapel → Ausrichtung prüfen (Restfehler unten)."))
        else:
            out.append(finding("ok", "Sterne", "Sterne bis in die Ecken rund",
                               "Breite (FWHM) Mitte %s px, Rand %s px." % (de(f_mid), de(f_edge))))
    if single and f_all > 1.25 * single:
        out.append(finding("warn", "Sterne", "Sterne im Stapel breiter als im Einzelbild",
                           "Stapel %s px, Einzelbild %s px – die Bilder liegen nicht genau aufeinander."
                           % (de(f_all), de(single)),
                           "Wackelt das Stativ (Wind, Spiegelschlag)? Auslöseverzögerung oder Spiegelvorauslösung "
                           "nutzen."))
    if rms is not None and rms > RMS_PX:
        out.append(finding("warn", "Sterne", "Ausrichtung ungenau (Restfehler %s px)" % de(rms, 2),
                           "Die Sternpositionen passen nach der Ausrichtung nicht genau.",
                           "Mehr Sterne im Bild (länger belichten, Fokus prüfen) oder weniger Bewegung zwischen "
                           "den Bildern."))
    return out


def check_frames(snap, stats):
    out = []
    rejected = snap["rejected"]
    if rejected:
        reasons = sorted({r[1] for r in rejected})
        # Viele Sterne gefunden, aber keine Zuordnung: Das ist kein Wolkenproblem – der Bildausschnitt ist ein anderer
        moved = [r for r in rejected if r[1].startswith("Ausrichtung") and len(r) > 2 and r[2] >= 30]
        if len(moved) >= 3:
            out.append(finding("warn", "Bilder", "%d Bilder passen nicht zum Stapel – anderer Bildausschnitt?"
                               % len(moved),
                               "Sie zeigen genug Sterne (%d und mehr), aber nicht den Himmel des ersten Bilds – die "
                               "Kamera wurde vermutlich neu ausgerichtet. Ab %s." % (
                                   min(r[2] for r in moved), moved[0][0].rsplit("_", 1)[-1].split(".")[0]),
                               "Für den neuen Ausschnitt im Live-Stack „Neu beginnen“ – das nächste Bild wird die "
                               "Referenz. Den bisherigen Stapel vorher speichern."))
        rest = len(rejected) - (len(moved) if len(moved) >= 3 else 0)
        if rest:
            out.append(finding("info", "Bilder", "%d Bild%s verworfen" % (rest, "" if rest == 1 else "er"),
                               "; ".join(reasons[:3]), "Wolken oder Bewegung? Verworfene Bilder fehlen nur im Stapel."))
    bgs = [s["bg"] for s in stats if s.get("bg") is not None]
    stars = [s["stars"] for s in stats if s.get("stars")]
    if len(bgs) >= 5:
        mb, ms = float(np.median(bgs)), float(np.median(stars)) if stars else 0
        odd = [s for s in stats if (s.get("bg") is not None and mb > 0 and s["bg"] > 1.2 * mb)
               or (ms and s.get("stars") is not None and s["stars"] < 0.6 * ms)]
        if odd:
            out.append(finding("warn", "Bilder", "%d Bild%s mit Wolken oder Streulicht" % (
                len(odd), "" if len(odd) == 1 else "er"),
                "Hintergrund heller oder deutlich weniger Sterne als üblich: %s." % ", ".join(
                    s.get("name", "?").rsplit("_", 1)[-1].split(".")[0] for s in odd[:8]),
                "Mit „Zurücksetzen“ neu beginnen oder diese Bilder später in Siril aussortieren."))
    temps = [s["temp"] for s in stats if s.get("temp") is not None]
    if len(temps) >= 3 and max(temps) - min(temps) >= 3:
        out.append(finding("info", "Bilder", "Sensortemperatur %d–%d °C" % (round(min(temps)), round(max(temps))),
                           "Die Temperatur ändert sich während der Serie – Darks am besten bei der mittleren "
                           "Temperatur (%d °C)." % round(median(temps))))
    return out
