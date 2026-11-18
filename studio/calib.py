"""Kalibrier-Bibliothek: Master-Bias und Master-Darks als FITS, passende Auswahl zu den Lights, Master erstellen.

    <OUT>/Astro/Bibliothek/
        bias/    MASTER-BIAS_ISO3200_n40_20261118-1530.fits
        darks/   MASTER-DARK_ISO3200_30s_14C_n20_20261118-2350.fits

Alle Angaben stehen im FITS-Kopf (IMAGETYP, ISOSPEED, EXPTIME, CCD-TEMP, NCOMBINE, INSTRUME, DATE-OBS …), Siril liest
die Dateien direkt. Ein Master entsteht aus RAWs in zwei Durchgängen: robuste Mitte und Streuung je Pixel (ohne
Minimum und Maximum), dann der Mittelwert ohne Ausreißer (κσ-Clipping, κ = 3) – kosmische Strahlen oder ein
Lichtblitz in einem Bild fallen heraus, ohne dass alle Bilder gleichzeitig im Speicher liegen müssen.

Auswahl zu einem Light (gleiche Kamera und Bildgröße vorausgesetzt):
  * Bias: gleiche ISO, das neueste.
  * Dark: gleiche ISO. Zuerst gleiche Belichtungszeit (±5 %), dann die nächste Temperatur, dann das neueste.
    Mit Dark-Optimierung auch andere Zeiten (der Faktor k gleicht aus, 0,5- bis 4-fach) – gleiche Zeit zuerst.
"""
import collections
import datetime
import os
import threading
import time

import astro
import imaging
from imaging import np

KINDS = {"bias": {"folder": "bias", "label": "Bias", "imagetyp": "Master Bias", "prefix": "MASTER-BIAS",
                  "source": "biases"},
         "dark": {"folder": "darks", "label": "Dark", "imagetyp": "Master Dark", "prefix": "MASTER-DARK",
                  "source": "darks"}}
KAPPA = 3.0
MIN_FRAMES = 3
LIBRARY_FOLDER = "Bibliothek"
DARK_MIN_EXPOSURE = 0.8      # kürzere „Darks“ sind Bias (kein Wärmesignal); unter 1 s, weil Bulb-Zeiten schwanken


def _exposure_class(dark_exp, light_exp, optimize):
    """0 = gleiche Zeit, 1/2 = andere Zeit (nur mit Optimierung), 3 = Zeit des Lights unbekannt, None = passt nicht."""
    if not light_exp:
        return 3
    if not dark_exp:
        return None
    r = dark_exp / light_exp
    if abs(r - 1) <= 0.05:
        return 0
    if optimize and 1 < r <= 4:
        return 1
    if optimize and 0.5 <= r < 1:
        return 2
    return None


class Library:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.cache = {}                 # Pfad → (Änderungszeit, Eintrag)
        self.seq = 0                    # zählt hoch, wenn sich etwas ändert – die Oberfläche lädt dann neu

    def folder(self, kind):
        return os.path.join(self.root, KINDS[kind]["folder"])

    def changed(self):
        with self.lock:
            self.seq += 1

    def list(self):
        out = []
        with self.lock:
            for kind in KINDS:
                d = self.folder(kind)
                if not os.path.isdir(d):
                    continue
                for name in os.listdir(d):
                    if not name.lower().endswith(imaging.FITS_EXT):
                        continue
                    path = os.path.join(d, name)
                    try:
                        mtime = os.path.getmtime(path)
                        hit = self.cache.get(path)
                        if not hit or hit[0] != mtime:
                            hit = (mtime, self._entry(kind, path))
                            self.cache[path] = hit
                        out.append(hit[1])
                    except (OSError, ValueError, KeyError):
                        continue                # halb geschriebene oder fremde Datei
        out.sort(key=lambda e: (e["kind"], e["iso"] or 0, e["exposure"] or 0, e["date"] or ""))
        return out

    def _entry(self, kind, path):
        h, _ = imaging.read_fits_header(path)
        return {"id": KINDS[kind]["folder"] + "/" + os.path.basename(path), "kind": kind,
                "label": KINDS[kind]["label"], "name": os.path.basename(path),
                "iso": h.get("ISOSPEED"), "exposure": h.get("EXPTIME"), "temp": h.get("CCD-TEMP"),
                "tmin": h.get("TEMPMIN"), "tmax": h.get("TEMPMAX"), "n": h.get("NCOMBINE"),
                "date": h.get("DATE-OBS"), "model": h.get("INSTRUME"), "w": h.get("NAXIS1"), "h": h.get("NAXIS2"),
                "median": h.get("MEDIAN"), "noise": h.get("NOISE"), "hot": h.get("HOTPIX"),
                "size": os.path.getsize(path)}

    def path(self, mid):
        folder, _, name = str(mid).partition("/")
        if folder not in [k["folder"] for k in KINDS.values()] or not name or os.path.basename(name) != name:
            raise ValueError("Unbekannter Master: %s" % mid)
        return os.path.join(self.root, folder, name)

    def get(self, mid):
        return next((e for e in self.list() if e["id"] == mid), None)

    def delete(self, mid):
        os.remove(self.path(mid))
        self.changed()

    def best(self, kind, iso, exposure=None, temp=None, shape=None, model=None, optimize=False):
        """Passender Master zu einem Light – Regeln siehe oben. shape = (Höhe, Breite) der CFA-Daten."""
        found = []
        for e in sorted(self.list(), key=lambda e: e["date"] or "", reverse=True):     # neueste zuerst
            if e["kind"] != kind or e["iso"] != iso:
                continue
            if shape and (e["h"], e["w"]) != tuple(shape):
                continue
            if model and e["model"] and e["model"] != model:
                continue
            cls = _exposure_class(e["exposure"], exposure, optimize) if kind == "dark" else 0
            if cls is None:
                continue
            dt = abs(e["temp"] - temp) if temp is not None and e["temp"] is not None else 99.0
            found.append(((cls, round(dt)), e))
        return min(found, key=lambda f: f[0])[1] if found else None


# ---------------------------------------------------------------- Quellen für Masters und Live-Stack
def sources(astro_dir):
    """Sitzungsordner mit RAW-Darks/-Bias (für Masters) und Lights (zum Nachstacken)."""
    out = []
    if not os.path.isdir(astro_dir):
        return out
    for session in sorted(os.listdir(astro_dir), reverse=True):
        base = os.path.join(astro_dir, session)
        if session == LIBRARY_FOLDER or not os.path.isdir(base):
            continue
        row = {"session": session}
        for key, folder in (("dark", "darks"), ("bias", "biases"), ("light", "lights")):
            d = os.path.join(base, folder)
            files = os.listdir(d) if os.path.isdir(d) else []
            row[key] = sum(1 for f in files if imaging.kind_of(f) == "raw")
            if key == "light":
                row["light_jpeg"] = sum(1 for f in files if imaging.kind_of(f) == "jpeg")
        if row["dark"] or row["bias"] or row["light"] or row["light_jpeg"]:
            out.append(row)
    return out


def source_files(astro_dir, session, folder):
    if os.path.basename(session) != session or session in ("", ".", ".."):
        raise ValueError("Ungültiger Sitzungsordner")
    d = os.path.join(astro_dir, session, folder)
    return sorted(os.path.join(d, f) for f in os.listdir(d)) if os.path.isdir(d) else []


# ---------------------------------------------------------------- Master erstellen
def group_frames(paths, by_exposure=True):
    """RAWs nach ISO und Belichtungszeit gruppieren → [(ISO, Zeit, [(Pfad, Metadaten), …]), …], größte zuerst.
    Zeiten ab 1 s auf ganze Sekunden (Bulb schwankt ein wenig). by_exposure=False: nur nach ISO."""
    groups = collections.OrderedDict()
    for p in paths:
        if imaging.kind_of(p) != "raw":
            continue
        m = imaging.read_meta(p)
        exp = m["exposure"]
        if exp and exp >= 1:
            exp = float(round(exp))
        key = (m["iso"], round(exp, 6) if by_exposure and exp else None)
        groups.setdefault(key, []).append((p, m))
    return sorted(((k[0], k[1], v) for k, v in groups.items()), key=lambda g: -len(g[2]))


def _robust(a):
    sample = a.ravel()[::97]
    med = float(np.median(sample))
    return med, 1.4826 * float(np.median(np.abs(sample - med)))


def _trim_correction(n):
    """Faktor für die Streuung ohne Minimum und Maximum, damit sie der Streuung aller n Werte entspricht
    (Monte-Carlo mit Normalverteilung; bei n = 4 ≈ 2,6, bei n = 20 ≈ 1,25)."""
    if n < 4:
        return 1.0
    x = np.sort(np.random.default_rng(0).normal(size=(20000, n)), axis=1)[:, 1:-1]
    return float(1 / np.sqrt(x.var(axis=1).mean()))


def build_master(library, kind, frames, progress=None, log=print, exposure=None):
    """Master aus RAW-Dateien einer Gruppe (siehe group_frames). progress(phase, fertig, gesamt).
    → Eintrag der Bibliothek."""
    problem = imaging.missing()
    if problem:
        raise RuntimeError(problem)
    n = len(frames)
    if n < MIN_FRAMES:
        raise ValueError("Zu wenige RAW-Bilder (%d) – mindestens %d" % (n, MIN_FRAMES))
    metas = [m for _, m in frames]
    if exposure is None:
        exps = sorted(m["exposure"] for m in metas if m["exposure"])
        exposure = exps[len(exps) // 2] if exps else None
    if kind == "dark" and exposure and exposure < DARK_MIN_EXPOSURE:
        raise ValueError("Darks mit 1/%d s sind eigentlich Bias – als Master-Bias erstellen (Ordner biases) oder "
                         "Darks mit der Zeit der Lights aufnehmen" % round(1 / exposure))
    progress = progress or (lambda *a: None)
    info = KINDS[kind]
    t0 = time.time()

    # Durchgang 1: Summe, Quadratsumme, Minimum und Maximum je Pixel (relativ zum ersten Bild – bleibt in
    # float32 genau). Mitte und Streuung ohne Minimum und Maximum: ein Ausreißer bläht sonst die Streuung so
    # auf, dass er bei wenigen Bildern nie über κσ liegt (bei 3–4 Bildern ist die Mitte der Median).
    ref = s = ss = mx = mn = None
    first, noise = None, 0.0
    for i, (path, _) in enumerate(frames):
        f = imaging.load_raw(path)
        x = f.data
        if ref is None:
            first, ref = f, x.copy()
            s, ss = np.zeros_like(x), np.zeros_like(x)
            mx, mn = np.full_like(x, -np.inf), np.full_like(x, np.inf)
        elif x.shape != ref.shape:
            raise ValueError("Bildgröße passt nicht: %s" % os.path.basename(path))
        x -= ref
        if i == 1:
            noise = _robust(x)[1] / 2 ** 0.5             # Rauschen eines Einzelbilds (aus der Differenz)
        s += x
        np.maximum(mx, x, out=mx)
        np.minimum(mn, x, out=mn)
        x *= x
        ss += x
        progress("Durchgang 1 von 2", i + 1, 2 * n)
    s -= mx
    s -= mn
    s /= n - 2                                           # s = robuste Mitte
    mx *= mx
    mn *= mn
    ss -= mx
    ss -= mn
    del mx, mn
    ss /= n - 2
    ss -= s * s
    np.maximum(ss, 0, out=ss)
    np.sqrt(ss, out=ss)
    ss *= _trim_correction(n)                            # ss = Streuung
    np.maximum(ss, noise, out=ss)                        # nie kleiner als das Rauschen eines Bilds
    s += ref
    del ref
    ss *= KAPPA
    ss += 1.0                                            # mindestens 1 ADU Spielraum (ganzzahlige Rohwerte)

    # Durchgang 2: Mittelwert ohne Ausreißer
    acc = np.zeros_like(s)
    cnt = np.zeros(s.shape, np.uint16)
    for i, (path, _) in enumerate(frames):
        x = imaging.load_raw(path).data
        ok = np.abs(x - s) <= ss
        x[~ok] = 0
        acc += x
        cnt += ok
        progress("Durchgang 2 von 2", n + i + 1, 2 * n)
    master = np.where(cnt > 0, acc / np.maximum(cnt, 1), s).astype(np.float32)
    rejected = 1 - float(cnt.sum(dtype=np.float64)) / (n * cnt.size)
    del acc, cnt, s, ss

    med, noise = _robust(master)
    hot = int(np.count_nonzero(master > med + max(10 * noise, 20.0))) if kind == "dark" else None
    temps = [m["temp"] for m in metas if m["temp"] is not None]
    iso = metas[0]["iso"]
    when = metas[0]["time"] or time.strftime("%Y-%m-%d %H:%M:%S")
    temp = round(sum(temps) / len(temps), 1) if temps else None

    parts = [info["prefix"], "ISO%s" % (iso or "--")]
    if kind == "dark":
        parts.append(astro.exposure_tag(exposure))
    if temp is not None:
        parts.append("%dC" % round(temp))
    stamp = when.replace("-", "").replace(":", "").replace(" ", "-")[:13]
    parts += ["n%d" % n, stamp]
    os.makedirs(library.folder(kind), exist_ok=True)
    name = "_".join(parts)
    path = os.path.join(library.folder(kind), name + ".fits")
    k = 2
    while os.path.exists(path):
        path = os.path.join(library.folder(kind), "%s_%d.fits" % (name, k))
        k += 1
    cards = [
        ("IMAGETYP", info["imagetyp"], "Bildtyp"), ("INSTRUME", first.meta["model"] or "", "Kamera"),
        ("ISOSPEED", iso, "ISO"), ("EXPTIME", float(exposure) if exposure else None, "Belichtung [s]"),
        ("CCD-TEMP", temp, "mittlere Sensortemperatur [C]"),
        ("TEMPMIN", min(temps) if temps else None, "[C]"), ("TEMPMAX", max(temps) if temps else None, "[C]"),
        ("NCOMBINE", n, "Anzahl Einzelbilder"), ("DATE-OBS", when.replace(" ", "T"), "erstes Bild"),
        ("BAYERPAT", "RGGB", ""), ("XBAYROFF", 0, ""), ("YBAYROFF", 0, ""),
        ("STACKMTH", "kappa-sigma mean", "zwei Durchgaenge"), ("CLIPKAPP", KAPPA, ""),
        ("CLIPFRAC", round(rejected, 5), "Anteil verworfener Werte"),
        ("BLACKLVL", first.black, "Schwarzwert der Kamera [ADU]"), ("WHITELVL", first.white, "[ADU]"),
        ("MEDIAN", round(med, 2), "[ADU]"), ("NOISE", round(noise, 3), "robuste Streuung [ADU]"),
        ("HOTPIX", hot, "Pixel > Median + max(10 sigma, 20 ADU)"),
        ("CREATOR", "6D Studio", ""), ("DATE", datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "erstellt"),
    ]
    imaging.write_fits(path, master, cards)
    library.changed()
    log("✔︎ Master-%s: %s (%d Bilder, %.0f s)" % (info["label"], os.path.basename(path), n, time.time() - t0))
    return library.get(KINDS[kind]["folder"] + "/" + os.path.basename(path))
