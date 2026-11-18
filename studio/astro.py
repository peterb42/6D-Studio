"""Astro-Serien für Stacking: Bildtypen, Namenskonvention und Serienprotokoll.

Ordnerstruktur (kompatibel mit den Siril-Skripten, die biases/darks/flats/lights erwarten):

    <OUT>/Astro/<Nacht>_<Objekt>/
        lights/    LIGHT_<Objekt>_<JJJJMMTT-HHMMSS>_ISO3200_30s_f2.8_0001.CR2
        darks/     DARK_…
        biases/    BIAS_…_1-4000s_…
        flats/     FLAT_…
        darkflats/ DARKFLAT_…
        serie.csv  ein Eintrag pro Datei (auch für Bilder, die nur auf der Karte bleiben)

<Nacht> ist das Datum 12 Stunden vor der Aufnahme – eine Nacht bleibt so ein Ordner,
auch nach Mitternacht (wie $$DATEMINUS12$$ bei N.I.N.A.).
"""
import csv
import datetime
import os
import re
import threading
import unicodedata

FRAME_TYPES = {
    "light":    {"label": "Light",     "folder": "lights",    "prefix": "LIGHT"},
    "dark":     {"label": "Dark",      "folder": "darks",     "prefix": "DARK"},
    "bias":     {"label": "Bias",      "folder": "biases",    "prefix": "BIAS"},
    "flat":     {"label": "Flat",      "folder": "flats",     "prefix": "FLAT"},
    "darkflat": {"label": "Dark-Flat", "folder": "darkflats", "prefix": "DARKFLAT"},
}

CSV_FIELDS = ["typ", "nr", "datei", "kameradatei", "ordner", "start", "belichtung_geplant_s",
              "belichtung_ist_s", "iso", "blende", "zeit", "modus", "geladen"]

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def night_date(ts):
    """Datum der Beobachtungsnacht: Wechsel um 12 Uhr mittags statt um Mitternacht."""
    return (datetime.datetime.fromtimestamp(ts) - datetime.timedelta(hours=12)).strftime("%Y-%m-%d")


def safe_name(text, default="Ziel"):
    """Objektname für Datei- und Ordnernamen: nur Buchstaben, Ziffern und Bindestriche."""
    s = unicodedata.normalize("NFKD", (text or "").translate(_UMLAUTE)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s[:40] or default


def exposure_tag(seconds):
    """30 → „30s“, 2.5 → „2.5s“, 1/4000 → „1-4000s“, unbekannt → „auto“."""
    if not seconds:
        return "auto"
    if seconds >= 0.3:
        return "%gs" % round(seconds, 1)
    return "1-%ds" % round(1 / seconds)


def iso_tag(label):
    return "ISO" + (label if label and label not in ("–", "?") else "--")


def aperture_tag(label):
    return label.replace("/", "") if label and label.startswith("f/") else "f--"


def session_dir(base, target, ts):
    return os.path.join(base, "%s_%s" % (night_date(ts), safe_name(target)))


def frame_name(ftype, target, ts, iso_label, exposure_s, aperture_label, index):
    """Dateiname ohne Endung, z. B. LIGHT_Milchstrasse_20261118-231405_ISO3200_30s_f2.8_0001."""
    return "%s_%s_%s_%s_%s_%s_%04d" % (
        FRAME_TYPES[ftype]["prefix"], safe_name(target),
        datetime.datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S"),
        iso_tag(iso_label), exposure_tag(exposure_s), aperture_tag(aperture_label), index)


def next_index(folder, ftype):
    """Nächste freie Bildnummer im Ordner – eine zweite Serie überschreibt nichts."""
    prefix = FRAME_TYPES[ftype]["prefix"] + "_"
    highest = 0
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            m = re.match(re.escape(prefix) + r".*_(\d{4,})\.[A-Za-z0-9]+$", name)
            if m:
                highest = max(highest, int(m.group(1)))
    return highest + 1


_csv_lock = threading.Lock()


def log_file(session, row):
    """Eine Zeile an <Sitzung>/serie.csv anhängen (Kopfzeile beim ersten Mal)."""
    path = os.path.join(session, "serie.csv")
    with _csv_lock:
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)
