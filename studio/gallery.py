"""Bildverwaltung: Sitzungen, Einzelaufnahmen, Live-Stacks und Masters durchsuchen, ansehen und weitergeben.

Quellen (alles unter OUT, z. B. ~/Pictures/6D-Tethering):
    Astro/<Nacht>_<Objekt>/{lights,darks,biases,flats,darkflats}/   Serien (siehe astro.py)
    Astro/<Nacht>_<Objekt>/livestack/                               Live-Stack-Ergebnisse (FITS + PNG)
    Astro/<Nacht>_<Objekt>/siril/*.fit                              Ergebnisse der Siril-Vorverarbeitung
    Astro/<Nacht>_<Objekt>/pixinsight/*.xisf                        Ergebnisse von WBPP (PixInsight)
    Astro/<Nacht>_<Objekt>/verworfen/<Ordner>/                      aussortierte Bilder
    Astro/Bibliothek/{bias,darks,flats,darkflats}/                  Masters (calib.py)
    *.CR2 / *.JPG direkt in OUT                                     Einzelaufnahmen (Auslösen, Bulb)

Weitergeben:
  * PixInsight (WBPP): <Sitzung>/pixinsight/ mit Light/ Dark/ Flat/ Bias/ – harte Verknüpfungen, Namen so, dass
    WBPP den Bildtyp sicher aus dem Pfad erkennt (siehe WBPP_TYPE), dazu Listen und bilder.csv. Das Studio kann
    WBPP auch selbst starten (ohne Oberfläche, PixInsight -n --automation-mode); Arbeitsordner pixinsight/wbpp/.
  * Siril: <Sitzung>/siril/ mit lights/ darks/ flats/ biases/ darkflats/ (nur RAWs, verknüpft) und einem Skript,
    das fehlende Kalibrierbilder durch passende Masters aus der Bibliothek ersetzt.
  * Fertig entwickeln: mit Siril (Skript) oder PixInsight (pixinsight/6d-fertig.js) → JPEG und 16-Bit-TIFF.
  * „Öffnen mit“ einem installierten Programm (TOOLS), „Im Finder zeigen“.
"""
import csv
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time

import astro
import calib
import imaging

GROUPS = (("lights", "Lights", "light"), ("darks", "Darks", "dark"), ("biases", "Bias", "bias"),
          ("flats", "Flats", "flat"), ("darkflats", "Dark-Flats", "darkflat"),
          ("livestack", "Live-Stack", "stack"), ("siril", "Siril-Ergebnisse", "result"),
          ("pixinsight", "PixInsight-Ergebnisse", "result"), ("verworfen", "Aussortiert", "rejected"))
FRAME_FOLDERS = {g[0]: g[2] for g in GROUPS[:5]}          # Ordner → Bildtyp (astro.FRAME_TYPES)
PRIMARY = ("raw", "fits", "xisf", "tiff", "jpeg", "png")   # welche Datei eines Bilds die „eigentliche“ ist
DATA_KINDS = ("fits", "xisf")                              # lineare Bilddaten (gestapelt: weiterentwickeln)
THUMB, LARGE = 360, 1600
EXPORT_MARK = ".6d-studio-export"                          # nur Ordner mit dieser Marke werden neu angelegt

# WBPP (PixInsight 1.9.6, BPP-Helper.js getImageTypeFromPath): Bildtyp aus dem Pfad, wenn der Dateikopf keinen
# IMAGETYP hat (CR2). Ein Schlüsselwort zählt nur als eigenes Wort; das letzte im Pfad gewinnt. „DARKFLAT“ oder
# „biases“ erkennt WBPP nicht – deshalb hängt der Export den Typ als letztes Wort an den Dateinamen.
WBPP_TYPE = re.compile(r"(?<![a-z0-9])(BIAS|DARK|DARKS|FLAT|FLATS|LIGHT|LIGHTS)(?![a-z0-9])", re.I)
WBPP_FOLDERS = {"light": "Light", "dark": "Dark", "darkflat": "Dark", "flat": "Flat", "bias": "Bias"}

# Programme zur Nachbearbeitung (macOS). kinds: womit das Programm umgehen kann.
TOOLS = (
    {"id": "pixinsight", "name": "PixInsight", "app": r"^PixInsight\.app$",
     "kinds": {"raw", "fits", "xisf", "tiff", "jpeg", "png"}, "hint": "Profi-Werkzeug; Vorverarbeitung mit WBPP"},
    {"id": "siril", "name": "Siril", "app": r"^Siril\.app$", "kinds": {"raw", "fits", "xisf", "tiff", "jpeg", "png"},
     "hint": "kostenlos: Kalibrieren, Stapeln, Hintergrund, Farbe, Strecken"},
    {"id": "graxpert", "name": "GraXpert", "app": r"^GraXpert.*\.app$",
     "kinds": {"fits", "xisf", "tiff", "png", "jpeg"}, "hint": "kostenlos: Gradienten entfernen, KI-Entrauschen "
                                                                "(gestapelte Bilder)"},
    {"id": "sls", "name": "Starry Landscape Stacker", "app": r"^Starry Landscape Stacker.*\.app$",
     "kinds": {"jpeg", "tiff"}, "hint": "Milchstraße mit Vordergrund stapeln (Mac)"},
    {"id": "app", "name": "Astro Pixel Processor", "app": r"^Astro ?Pixel ?Processor.*\.app$",
     "kinds": {"raw", "fits", "tiff"}, "hint": "Stapeln, Mosaike, Lichtverschmutzung"},
    {"id": "startools", "name": "StarTools", "app": r"^StarTools.*\.app$", "kinds": {"fits", "tiff"},
     "hint": "Nachbearbeitung gestapelter Bilder"},
    {"id": "affinity", "name": "Affinity Photo", "app": r"^Affinity Photo.*\.app$",
     "kinds": {"raw", "fits", "tiff", "jpeg", "png"}, "hint": "Astrofotografie-Stapel, Bildbearbeitung"},
    {"id": "photoshop", "name": "Photoshop", "app": r"^Adobe Photoshop.*\.app$",
     "kinds": {"raw", "tiff", "jpeg", "png"}, "hint": "Bildbearbeitung (RAW über Camera Raw)"},
    {"id": "lightroom", "name": "Lightroom Classic", "app": r"^Adobe Lightroom Classic\.app$",
     "kinds": {"raw", "jpeg", "tiff"}, "hint": "RAW-Entwicklung, Verwaltung"},
    {"id": "darktable", "name": "darktable", "app": r"^darktable\.app$", "kinds": {"raw", "jpeg", "tiff"},
     "hint": "kostenlose RAW-Entwicklung"},
    {"id": "preview", "name": "Vorschau", "app": r"^Preview\.app$", "kinds": {"raw", "jpeg", "png", "tiff"},
     "hint": "schnell ansehen"},
)
APP_DIRS = ("/Applications", "~/Applications", "/System/Applications")

_meta_cache, _meta_lock = {}, threading.Lock()
_tools_cache = {"t": 0.0, "found": {}}


# ---------------------------------------------------------------- Pfade
def kind_of(name):
    k = imaging.kind_of(name)
    if k:
        return k
    ext = os.path.splitext(name)[1].lower()
    return {".png": "png", ".tif": "tiff", ".tiff": "tiff", ".xisf": "xisf"}.get(ext)


def safe_path(out, rel):
    """Relativer Pfad → absoluter Pfad innerhalb von OUT (sonst ValueError)."""
    root = os.path.realpath(out)
    p = os.path.realpath(os.path.join(root, str(rel or "")))
    if p != root and not p.startswith(root + os.sep):
        raise ValueError("Pfad außerhalb des Fotoordners")
    return p


def rel_of(out, path):
    return os.path.relpath(os.path.realpath(path), os.path.realpath(out))


def _astro(out):
    return os.path.join(out, "Astro")             # Pfade wie übergeben – wie die Serien (Schlüssel der Nummern)


def session_path(out, session):
    if not session or os.path.basename(session) != session or session in (".", "..", calib.LIBRARY_FOLDER):
        raise ValueError("Ungültige Sitzung")
    p = os.path.join(_astro(out), session)
    if not os.path.isdir(p):
        raise ValueError("Sitzung nicht gefunden: %s" % session)
    return p


# ---------------------------------------------------------------- Übersicht
def _files(folder, recursive=False):
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder)):
        p = os.path.join(folder, name)
        if name.startswith("."):
            continue
        if os.path.isdir(p):
            if recursive:
                out += _files(p)
        elif kind_of(name):
            out.append(p)
    return out


def _single_files(out):
    """Einzelaufnahmen direkt im Fotoordner – ohne die Vorschaudatei von „6d-fernsteuerung preview“ (thumb_*)."""
    root = os.path.realpath(out)
    return [p for p in _files(root) if kind_of(p) in ("raw", "jpeg") and not os.path.basename(p).startswith("thumb_")]


def _group_counts(paths):
    stems = {os.path.join(os.path.dirname(p), os.path.splitext(os.path.basename(p))[0]) for p in paths}
    return {"count": len(stems), "files": len(paths), "size": sum(os.path.getsize(p) for p in paths)}


def index(out):
    """Übersicht: Sitzungen mit Gruppen, Einzelaufnahmen je Nacht, Bibliothek."""
    sessions, astro_dir = [], _astro(out)
    if os.path.isdir(astro_dir):
        for name in sorted(os.listdir(astro_dir), reverse=True):
            base = os.path.join(astro_dir, name)
            if name == calib.LIBRARY_FOLDER or name.startswith(".") or not os.path.isdir(base):
                continue
            groups = []
            for folder, label, _ in GROUPS:
                paths = _files(os.path.join(base, folder), recursive=folder == "verworfen")
                if folder in RESULT_TOOLS:
                    paths = [p for p in paths if kind_of(p) in RESULT_KINDS]
                if paths:
                    groups.append(dict(_group_counts(paths), key=folder, label=label))
            if groups:
                m = re.match(r"(\d{4}-\d{2}-\d{2})_(.*)", name)
                sessions.append({"id": "s:" + name, "name": name, "night": m.group(1) if m else None,
                                 "target": m.group(2) if m else name, "groups": groups,
                                 "export": os.path.isdir(os.path.join(base, "pixinsight")),
                                 "siril": os.path.isfile(os.path.join(base, "siril", SIRIL_SCRIPT))})
    nights = {}
    for p in _single_files(out):
        nights.setdefault(astro.night_date(os.path.getmtime(p)), []).append(p)
    singles = [{"id": "n:" + night, "name": "Einzelaufnahmen", "night": night,
                "groups": [dict(_group_counts(paths), key="all", label="Einzelaufnahmen")]}
               for night, paths in sorted(nights.items(), reverse=True)]
    lib_groups = []
    for kind, info in calib.KINDS.items():
        paths = _files(os.path.join(astro_dir, calib.LIBRARY_FOLDER, info["folder"]))
        if paths:
            lib_groups.append(dict(_group_counts(paths), key=kind, label=info["label"]))
    library = [{"id": "lib", "name": "Kalibrier-Bibliothek", "night": None, "groups": lib_groups}] if lib_groups else []
    res = _result_files(out)
    results = [{"id": "r:", "name": "Ergebnisse", "night": None,
                "groups": [dict(_group_counts(res), key="all", label="Live-Stacks, Siril, PixInsight")]}] if res else []
    return {"results": results, "sessions": sessions, "singles": singles, "library": library}


RESULT_TOOLS = ("siril", "pixinsight")                     # Ordner der Programme – nur die Ergebnisse darin zählen
RESULT_FOLDERS = ("livestack",) + RESULT_TOOLS
RESULT_KINDS = ("fits", "xisf", "png", "jpeg", "tiff")


def _result_files(out):
    """Alle Ergebnisse: Live-Stacks, Siril- und PixInsight-Ergebnisse (FITS/XISF, fertig entwickelte JPEG/TIFF)
    aller Sitzungen – je Ordner nur die oberste Ebene (nicht Light/, wbpp/ …)."""
    paths, astro_dir = [], _astro(out)
    if os.path.isdir(astro_dir):
        for name in os.listdir(astro_dir):
            base = os.path.join(astro_dir, name)
            if name == calib.LIBRARY_FOLDER or name.startswith(".") or not os.path.isdir(base):
                continue
            for folder in RESULT_FOLDERS:
                paths += [p for p in _files(os.path.join(base, folder)) if kind_of(p) in RESULT_KINDS]
    return paths


def _source_files(out, source, group):
    """Dateien einer Gruppe: (Liste absoluter Pfade, Basisordner für Unterordner-Angaben)."""
    if source.startswith("s:"):
        base = session_path(out, source[2:])
        if group not in [g[0] for g in GROUPS]:
            raise ValueError("Unbekannte Gruppe")
        folder = os.path.join(base, group)
        paths = _files(folder, recursive=group == "verworfen")
        if group in RESULT_TOOLS:
            paths = [p for p in paths if kind_of(p) in RESULT_KINDS]
        return paths, folder
    if source == "r:":
        return _result_files(out), None
    if source.startswith("n:"):
        return [p for p in _single_files(out) if astro.night_date(os.path.getmtime(p)) == source[2:]], None
    if source == "lib":
        if group not in calib.KINDS:
            raise ValueError("Unbekannte Gruppe")
        return _files(os.path.join(_astro(out), calib.LIBRARY_FOLDER, calib.KINDS[group]["folder"])), None
    raise ValueError("Unbekannte Quelle")


def meta_of(path):
    """Aufnahmedaten (gecacht nach Änderungszeit): EXIF für RAW/JPEG, Kopf für FITS."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    with _meta_lock:
        hit = _meta_cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
    k = kind_of(path)
    meta = {}
    if k in ("raw", "jpeg"):
        meta = imaging.read_meta(path)
    elif k in DATA_KINDS:
        try:
            h, _ = imaging.image_header(path)
            iso = h.get("ISOSPEED")
            meta = {"iso": int(iso) if isinstance(iso, float) and iso.is_integer() else iso,
                    "exposure": h.get("EXPTIME"), "temp": h.get("CCD-TEMP"), "n": h.get("NCOMBINE"),
                    "type": h.get("IMAGETYP"), "fnumber": h.get("FNUMBER"), "focal": h.get("FOCALLEN"),
                    "time": (h.get("DATE-OBS") or "").replace("T", " ")[:19] or None, "model": h.get("INSTRUME")}
            m = WBPP_RESULT.match(os.path.basename(path))
            if m and not meta["n"]:                  # WBPP schreibt die Zahl der Lights nicht in den Kopf
                meta["n"] = int(m.group(1))
        except (OSError, ValueError, KeyError):
            meta = {}
    with _meta_lock:
        _meta_cache[path] = (mtime, meta)
    return meta


def items(out, source, group):
    """Bilder einer Gruppe; RAW+JPEG bzw. FITS+PNG mit gleichem Namen sind ein Eintrag."""
    paths, base = _source_files(out, source, group)
    by_stem = {}
    for p in paths:
        stem = os.path.join(os.path.dirname(p), os.path.splitext(os.path.basename(p))[0])
        by_stem.setdefault(stem, []).append(p)
    result = []
    for stem, files in by_stem.items():
        files.sort(key=lambda p: PRIMARY.index(kind_of(p)) if kind_of(p) in PRIMARY else 9)
        primary = files[0]
        preview = next((p for p in files if kind_of(p) in ("jpeg", "png")), primary)
        st = os.stat(primary)
        entry = {"id": rel_of(out, primary), "name": os.path.basename(stem), "kind": kind_of(primary),
                 "files": [{"rel": rel_of(out, p), "kind": kind_of(p), "size": os.path.getsize(p)} for p in files],
                 "preview": rel_of(out, preview), "mtime": st.st_mtime, "meta": meta_of(primary)}
        if base and os.path.dirname(primary) != base:
            entry["from"] = os.path.basename(os.path.dirname(primary))       # verworfen/<Ordner>
        parts = entry["id"].split("/")
        if len(parts) >= 3 and parts[0] == "Astro" and parts[1] != calib.LIBRARY_FOLDER:
            entry["session"] = parts[1]
        done = FINISHED.match(os.path.basename(stem))
        entry["finished"] = bool(done)
        if done:
            entry["finished_by"] = "pixinsight" if done.group(2) else "siril"
        result.append(entry)
    if source == "r:" or group in RESULT_FOLDERS:
        result.sort(key=lambda e: -e["mtime"])                  # Ergebnisse: neueste zuerst
    else:
        result.sort(key=lambda e: (e["meta"].get("time") or "", e["name"]))
    return result


# ---------------------------------------------------------------- Vorschaubilder
def thumbnail(out, rel, size=THUMB):
    """→ (Bytes, Inhaltstyp). JPEG/PNG/RAW/TIFF über sips (macOS), FITS über numpy (gestreckt)."""
    path = safe_path(out, rel)
    if not os.path.isfile(path):
        raise ValueError("Datei nicht gefunden")
    k = kind_of(path)
    cache_dir = os.path.join(os.path.realpath(out), ".vorschau", "thumbs")
    key = hashlib.sha1(("%s|%s|%d" % (path, os.path.getmtime(path), size)).encode()).hexdigest()[:20]
    cached = os.path.join(cache_dir, key + (".png" if k in DATA_KINDS else ".jpg"))
    if os.path.isfile(cached):
        with open(cached, "rb") as f:
            return f.read(), "image/png" if cached.endswith(".png") else "image/jpeg"
    os.makedirs(cache_dir, exist_ok=True)
    if k in DATA_KINDS:
        data = _fits_preview(path, size)
        with open(cached, "wb") as f:
            f.write(data)
        return data, "image/png"
    r = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80", "-Z", str(size), path,
                        "--out", cached], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    if r.returncode != 0 or not os.path.isfile(cached):
        raise ValueError("Keine Vorschau möglich")
    with open(cached, "rb") as f:
        return f.read(), "image/jpeg"


def _fits_preview(path, size):
    problem = imaging.missing()
    if problem:
        raise ValueError(problem)
    import livestack
    np = imaging.np
    data, header = imaging.read_image(path)
    if data.ndim == 2:                               # Masters: CFA → Superpixel, Helligkeit gleich in allen Kanälen
        h, w = data.shape[0] // 2 * 2, data.shape[1] // 2 * 2
        g = data[:h, :w].reshape(h // 2, 2, w // 2, 2).mean((1, 3))
        rgb = np.repeat(g[..., None], 3, 2)
    else:
        rgb = data
    f = max(1, -(-rgb.shape[1] // size))
    hh, ww = rgb.shape[0] // f * f, rgb.shape[1] // f * f
    small = rgb[:hh, :ww].reshape(hh // f, f, ww // f, f, 3).mean((1, 3)).astype(np.float32)
    cov = np.ones(small.shape[:2], bool)
    white = float(np.percentile(small, 99.99)) or 1.0
    return imaging.png_bytes(livestack.stretch(small, cov, (1.0, 1.0, 1.0), white))


# ---------------------------------------------------------------- Programme
def find_tools(dirs=APP_DIRS, max_age=60.0):
    """Installierte Programme aus TOOLS → {id: Pfad zur .app}. Sucht zwei Ebenen tief (PixInsight liegt in
    /Applications/PixInsight/PixInsight.app)."""
    if time.time() - _tools_cache["t"] < max_age and dirs == APP_DIRS:
        return dict(_tools_cache["found"])
    apps = []
    for d in dirs:
        d = os.path.expanduser(d)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if name.endswith(".app"):
                apps.append((name, p))
            elif os.path.isdir(p) and not name.startswith("."):
                try:
                    apps += [(n, os.path.join(p, n)) for n in os.listdir(p) if n.endswith(".app")]
                except OSError:
                    pass
    found = {}
    for t in TOOLS:
        rx = re.compile(t["app"], re.I)
        hit = next((p for n, p in sorted(apps) if rx.match(n)), None)
        if hit:
            found[t["id"]] = hit
    if dirs == APP_DIRS:
        _tools_cache.update(t=time.time(), found=dict(found))
    return found


def tools_info(found=None):
    found = find_tools() if found is None else found
    return [{"id": t["id"], "name": t["name"], "kinds": sorted(t["kinds"]), "hint": t["hint"]}
            for t in TOOLS if t["id"] in found]


def open_with(out, rels, tool=None, reveal=False, found=None, run=subprocess.Popen):
    paths = [safe_path(out, r) for r in rels]
    if not paths:
        raise ValueError("Nichts ausgewählt")
    if reveal:
        return run(["open", "-R", paths[0]])
    if tool:
        found = find_tools() if found is None else found
        if tool not in found:
            raise ValueError("Programm nicht installiert")
        return run(["open", "-a", found[tool]] + paths)
    return run(["open"] + paths)


# ---------------------------------------------------------------- Einzelaufnahmen übernehmen, aussortieren
def _exposure_label(seconds):
    if not seconds:
        return ""
    return '%g"' % round(seconds, 1) if seconds >= 0.3 else "1/%d" % round(1 / seconds)


def _epoch(meta, path):
    t = meta.get("time")
    if t:
        try:
            return time.mktime(datetime.datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S").timetuple())
        except ValueError:
            pass
    return os.path.getmtime(path)


def adopt(out, rels, ftype, target, used=None):
    """Einzelaufnahmen in eine Sitzung übernehmen: verschieben, nach der Namenskonvention benennen, in serie.csv
    eintragen. RAW und JPEG mit gleichem Namen bleiben zusammen. → Liste der neuen relativen Pfade."""
    if ftype not in astro.FRAME_TYPES:
        raise ValueError("Unbekannter Bildtyp")
    root = os.path.realpath(out)
    names = os.listdir(root)
    stems = {}
    for r in rels:
        p = safe_path(out, r)
        if os.path.dirname(p) != root or kind_of(p) not in ("raw", "jpeg"):
            raise ValueError("Nur Einzelaufnahmen lassen sich übernehmen: %s" % os.path.basename(p))
        stem = os.path.splitext(os.path.basename(p))[0]
        for n in names:                                  # echte Namen – .CR2/.cr2 sind auf dem Mac dieselbe Datei
            if os.path.splitext(n)[0] == stem and kind_of(n) in ("raw", "jpeg"):
                stems.setdefault(stem, set()).add(os.path.join(root, n))
    if not stems:
        raise ValueError("Nichts ausgewählt")
    target = astro.safe_name(target)
    groups = []
    for files in stems.values():
        files = sorted(files, key=lambda p: PRIMARY.index(kind_of(p)))
        meta = meta_of(files[0])
        groups.append((_epoch(meta, files[0]), files, meta))
    groups.sort(key=lambda g: g[0])
    session = astro.session_dir(_astro(out), target, groups[0][0])
    info = astro.FRAME_TYPES[ftype]
    folder = os.path.join(session, info["folder"])
    os.makedirs(folder, exist_ok=True)
    used = used if used is not None else {}
    new = []
    for ts, files, meta in groups:
        index = max(astro.next_index(folder, ftype), used.get((folder, ftype), 0) + 1)
        used[(folder, ftype)] = index
        aperture = "f/%g" % meta["fnumber"] if meta.get("fnumber") else "–"
        base = astro.frame_name(ftype, target, ts, str(meta.get("iso") or "–"), meta.get("exposure"), aperture,
                                index)
        for p in files:
            dest = os.path.join(folder, base + os.path.splitext(p)[1])
            if os.path.exists(dest):
                raise ValueError("Gibt es schon: %s" % os.path.basename(dest))
            os.rename(p, dest)
            new.append(rel_of(out, dest))
            astro.log_file(session, {
                "typ": info["label"], "nr": index, "datei": os.path.basename(dest), "kameradatei": os.path.basename(p),
                "ordner": info["folder"], "start": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
                "belichtung_geplant_s": "", "belichtung_ist_s": meta.get("exposure") or "",
                "iso": meta.get("iso") or "", "blende": aperture, "zeit": _exposure_label(meta.get("exposure")),
                "modus": "", "geladen": "übernommen"})
    return new


def cull(out, rels, restore=False):
    """Aussortieren: Bilder einer Sitzung nach <Sitzung>/verworfen/<Ordner>/ verschieben (RAW und JPEG zusammen) –
    Siril- und PixInsight-Export lassen sie dann weg. restore=True legt sie zurück. → Anzahl verschobener Dateien."""
    astro_dir = os.path.realpath(_astro(out))
    moved = 0
    for r in rels:
        p = safe_path(out, r)
        parts = os.path.relpath(p, astro_dir).split(os.sep)
        if len(parts) < 3 or parts[0] == calib.LIBRARY_FOLDER or parts[0].startswith(".."):
            raise ValueError("Nur Bilder einer Sitzung lassen sich aussortieren")
        session = os.path.join(astro_dir, parts[0])
        if restore:
            if parts[1] != "verworfen" or len(parts) != 4 or parts[2] not in FRAME_FOLDERS:
                raise ValueError("Nicht aussortiert: %s" % os.path.basename(p))
            dest_dir = os.path.join(session, parts[2])
        else:
            if len(parts) != 3 or parts[1] not in FRAME_FOLDERS:
                raise ValueError("Nur Lights, Darks, Bias, Flats und Dark-Flats lassen sich aussortieren")
            dest_dir = os.path.join(session, "verworfen", parts[1])
        os.makedirs(dest_dir, exist_ok=True)
        stem = os.path.splitext(p)[0]
        for sib in [f for f in os.listdir(os.path.dirname(p)) if os.path.splitext(f)[0] == os.path.basename(stem)]:
            src = os.path.join(os.path.dirname(p), sib)
            if os.path.exists(os.path.join(dest_dir, sib)):
                raise ValueError("Gibt es schon: %s" % sib)
            os.rename(src, os.path.join(dest_dir, sib))
            moved += 1
    return moved


# ---------------------------------------------------------------- Nachbearbeitung einer Sitzung
CHECK_FILE = ".pruefung.json"
COUNT_STARS = 300               # Sterne zählen bis hier (zum Ausrichten reichen die hellsten 100)


def session_summary(out, session, library):
    """Was die Oberfläche für den Ablauf Prüfen → Stapeln → Fertig entwickeln braucht."""
    base = session_path(out, session)
    sets = frame_sets(out, session, library)
    lights = sets["light"]["files"]
    cal = {t: {"n": len(sets[t]["files"]), "skipped": sets[t]["skipped"], "from": sets[t]["from"],
               "master": sets[t]["master"]["name"] if sets[t]["master"] else None}
           for t in ("bias", "dark", "flat", "darkflat")}
    hints = []
    if not cal["dark"]["n"] and not cal["dark"]["master"]:
        hints.append("Keine passenden Darks – Hotpixel und Wärmerauschen bleiben. Darks mit ISO und Zeit der "
                     "Lights: Bibliothek → „Geführt aufnehmen …“.")
    if not cal["flat"]["n"] and not cal["flat"]["master"]:
        hints.append("Keine Flats – Vignettierung und Staub bleiben (die Ecken werden später dunkler). "
                     "„Fertig entwickeln“ gleicht den Hintergrund aus, Flats wären besser.")
    if not cal["bias"]["n"] and not cal["bias"]["master"] and not cal["dark"]["n"]:
        hints.append("Weder Bias noch Darks – als Offset dient der Schwarzwert der Kamera.")
    check = None
    try:
        with open(os.path.join(base, CHECK_FILE), encoding="utf-8") as f:
            check = json.load(f)
        names = sorted(os.path.basename(p) for p in lights)
        check["stale"] = sorted(fr["name"] for fr in check["frames"]) != names
    except (OSError, ValueError, KeyError):
        check = None
    results = []
    for folder in RESULT_FOLDERS:
        if os.path.isdir(os.path.join(base, folder)):
            results += [it for it in items(out, "s:" + session, folder) if it["kind"] in DATA_KINDS]
    results.sort(key=lambda e: -e["mtime"])
    finished = {}                                    # Name des Stapels → [(JPEG, Zeit, Programm)]
    for folder in RESULT_FOLDERS:
        for p in _files(os.path.join(base, folder)):
            done = FINISHED.match(os.path.splitext(os.path.basename(p))[0])
            if done and kind_of(p) == "jpeg":
                finished.setdefault(done.group(1), []).append(
                    (rel_of(out, p), os.path.getmtime(p), "pixinsight" if done.group(2) else "siril"))
    for r in results:                                # nur, wenn nach dem Stapel entwickelt (gleicher Name möglich)
        hits = sorted((h for h in finished.get(r["name"], []) if h[1] >= r["mtime"] - 1), key=lambda h: -h[1])
        r["finished_jpg"] = hits[0][0] if hits else None
        r["finished_by"] = sorted({h[2] for h in hits})
    return {"session": session, "lights": len(lights), "lights_raw": sum(kind_of(p) == "raw" for p in lights),
            "calib": cal, "hints": hints, "check": check, "results": results[:8],
            "pixinsight": os.path.isdir(os.path.join(base, "pixinsight"))}


def check_lights(out, session, progress=None):
    """Lights prüfen wie der Live-Stack: Sterne finden, jedem Bild ein Bildfeld zuordnen (Sterne passen zusammen),
    Ausreißer im Hauptfeld markieren – andere Bildausschnitte, wenige Sterne (Wolken, Tau), unscharfe oder
    verwackelte Sterne, heller Himmel. Ergebnis auch in <Sitzung>/.pruefung.json."""
    problem = imaging.missing()
    if problem:
        raise ValueError(problem)
    import livestack
    np = imaging.np
    base = session_path(out, session)
    lights = [p for p in _files(os.path.join(base, "lights")) if kind_of(p) == "raw"] or \
        [p for p in _files(os.path.join(base, "lights")) if kind_of(p) == "jpeg"]
    if not lights:
        raise ValueError("Keine Lights in %s" % session)
    progress = progress or (lambda *a: None)
    frames, fields = [], []                          # fields: [(Referenz-Sterne, [Indizes])]
    for i, p in enumerate(lights):
        f = imaging.load(p)
        if f.cfa:
            x = f.data - np.float32(f.black)
            livestack.hot_filter(x)
            rgb = imaging.superpixel(x)
        else:
            rgb = f.data
        found, fwhm = livestack.find_stars(rgb.sum(2), max_stars=COUNT_STARS)   # zählen: Wolken = weniger Sterne
        stars = found[:livestack.MAX_STARS]                          # ausrichten mit den hellsten
        info = {"name": os.path.basename(p), "rel": rel_of(out, p), "stars": len(found),
                "fwhm": round(fwhm, 2) if fwhm else None, "bg": round(float(np.median(rgb[::8, ::8, 1])), 1),
                "field": None, "why": None}
        if len(stars) >= livestack.MIN_STARS:
            for k, (ref, members) in enumerate(fields):
                if livestack.register(stars, ref) is not None:
                    members.append(i)
                    info["field"] = k
                    break
            else:
                fields.append((stars, [i]))
                info["field"] = len(fields) - 1
        frames.append(info)
        progress(i + 1, len(lights), info["name"])
    main = max(range(len(fields)), key=lambda k: len(fields[k][1])) if fields else None
    inside = [f for f in frames if f["field"] == main]
    med = lambda key: float(np.median([f[key] for f in inside if f[key]])) if inside else 0.0  # noqa: E731
    m_stars, m_fwhm, m_bg = med("stars"), med("fwhm"), med("bg")
    for f in frames:
        if f["field"] is None:
            f["why"] = "kaum Sterne (%d)" % f["stars"]
        elif f["field"] != main:
            f["why"] = "anderer Bildausschnitt"
        elif m_stars and f["stars"] < 0.5 * m_stars:
            f["why"] = "wenige Sterne (%d statt ~%d) – Wolken, Tau?" % (f["stars"], m_stars)
        elif m_fwhm and f["fwhm"] and f["fwhm"] > 1.5 * m_fwhm:
            f["why"] = "Sterne unscharf (FWHM %.1f statt ~%.1f px) – Wind, Fokus?" % (f["fwhm"], m_fwhm)
        elif m_bg and f["bg"] > 1.3 * m_bg + 5:
            f["why"] = "heller Himmel – Wolken, Licht, Dämmerung?"
    odd = [f for f in frames if f["why"]]
    other = [f for f in odd if f["why"] == "anderer Bildausschnitt"]
    result = {"time": time.strftime("%Y-%m-%d %H:%M"), "n": len(frames), "main": len(inside),
              "fields": len(fields), "frames": frames, "suggest": [f["rel"] for f in odd],
              "other_field": len(other), "first_other": other[0]["name"] if other else None}
    with open(os.path.join(base, CHECK_FILE), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


# ---------------------------------------------------------------- Kalibrierbilder für Export und Skript
def _raws(folder):
    return [p for p in _files(folder) if kind_of(p) == "raw"]


def frame_sets(out, session, library):
    """Rohbilder je Typ für eine Sitzung. Fehlt ein Kalibriertyp in der Sitzung, kommen die Rohbilder des
    passenden Masters aus der Bibliothek (über SOURCE/FIRSTIMG im FITS-Kopf) – für PixInsight, das nur XISF-Masters
    annimmt. → {Typ: {"files": [...], "from": Sitzung oder None, "master": Bibliothekseintrag oder None}}"""
    base = session_path(out, session)
    sets = {t: {"files": _raws(os.path.join(base, f)), "from": None, "master": None, "skipped": 0}
            for f, t in FRAME_FOLDERS.items()}
    lights = sets["light"]["files"]
    if not lights:
        lights = [p for p in _files(os.path.join(base, "lights")) if kind_of(p) == "jpeg"]
        sets["light"]["files"] = lights
    if not lights:
        return sets
    m = meta_of(lights[len(lights) // 2])
    flats = [p for p in sets["flat"]["files"] if _matches(meta_of(p), "flat", m)]
    fm = meta_of(flats[len(flats) // 2]) if flats else {}
    for kind in ("bias", "dark", "flat", "darkflat"):          # nur Kalibrierbilder, die zu den Lights passen
        ref = fm if kind == "darkflat" else m
        keep = [p for p in sets[kind]["files"] if _matches(meta_of(p), kind, ref)]
        sets[kind]["skipped"] = len(sets[kind]["files"]) - len(keep)
        sets[kind]["files"] = keep
    if library is None:
        return sets
    want = {"bias": dict(iso=m.get("iso")), "dark": dict(iso=m.get("iso"), exposure=m.get("exposure"),
                                                          temp=m.get("temp")),
            "flat": dict(optics=calib.optics_of(m))}
    for kind, args in want.items():
        if sets[kind]["files"]:
            continue
        e = library.best(kind, model=m.get("model"), **args)
        if not e:
            continue
        sets[kind]["master"] = e
        src = e.get("source")
        folder = os.path.join(_astro(out), src, calib.KINDS[kind]["source"]) if src else None
        if folder and os.path.isdir(folder) and src != session:
            groups = calib.group_frames(_raws(folder), by_exposure=kind != "flat")
            hit = next((g for g in groups if e.get("first") in [os.path.basename(p) for p, _ in g[2]]), None)
            if hit:
                sets[kind]["files"] = [p for p, _ in hit[2]]
                sets[kind]["from"] = src
    return sets


def _matches(meta, kind, ref):
    """Passt ein Kalibrierbild zu den Lights (bzw. ein Dark-Flat zu den Flats)? Unbekannte Werte zählen als passend."""
    def same(a, b, tol):
        return a is None or b is None or (abs(a - b) <= tol * max(abs(a), abs(b)) if tol else a == b)
    if kind == "flat":
        return not calib.optics_mismatch(calib.optics_of(ref), calib.optics_of(meta))[0]
    if not same(meta.get("iso"), ref.get("iso"), 0):
        return False
    if kind == "dark":
        return same(meta.get("exposure"), ref.get("exposure"), 0.1)
    if kind == "darkflat":
        return same(meta.get("exposure"), ref.get("exposure"), calib.DARKFLAT_TOL)
    return True


def _link(src, dest):
    try:
        os.link(src, dest)
    except OSError:
        os.symlink(src, dest)


def _fresh_dir(path):
    """Ausgabeordner neu anlegen – einen vorhandenen nur löschen, wenn er von uns stammt."""
    if os.path.isdir(path):
        if not os.path.isfile(os.path.join(path, EXPORT_MARK)):
            raise ValueError("%s gibt es schon und stammt nicht von 6D Studio – bitte umbenennen" % path)
        shutil.rmtree(path)
    os.makedirs(path)
    with open(os.path.join(path, EXPORT_MARK), "w") as f:
        f.write("Von 6D Studio erzeugt – wird beim nächsten Export ersetzt.\n")


def wbpp_name(path, ftype):
    """Dateiname für WBPP: Typ-Präfix weg, Typ als letztes Wort – „DARKFLAT_…_0001.CR2“ → „…_0001_DARK.CR2“."""
    stem, ext = os.path.splitext(os.path.basename(path))
    prefix = astro.FRAME_TYPES[ftype]["prefix"] + "_"
    if stem.upper().startswith(prefix):
        stem = stem[len(prefix):]
    return "%s_%s%s" % (stem, WBPP_FOLDERS[ftype].upper(), ext)


def wbpp_type(path):
    """Wie WBPP den Typ aus dem Pfad liest (letztes eigenständiges Schlüsselwort) – für die Tests."""
    hits = WBPP_TYPE.findall(path)
    word = hits[-1].upper() if hits else None
    return {"DARKS": "DARK", "FLATS": "FLAT", "LIGHTS": "LIGHT"}.get(word, word)


EXPORT_INPUT = "Rohbilder"                      # in pixinsight/: das Ziel für „+ Directory“ in WBPP


def _export_files():
    return ["bilder.csv"] + [t["folder"] + ".txt" for t in astro.FRAME_TYPES.values()]


def _refresh_export(dest):
    """pixinsight/ für einen neuen Export: Rohbilder/ (Verknüpfungen, Listen) und LIESMICH.txt des letzten Exports
    weg – Ergebnisse (wbpp_*.xisf, fertig entwickelte Bilder) und der WBPP-Arbeitsordner wbpp/ bleiben. Räumt auch
    die Ordner früherer Versionen auf (Light/ Dark/ Flat/ Bias/ direkt in pixinsight/)."""
    if os.path.isdir(dest):
        if not os.path.isfile(os.path.join(dest, EXPORT_MARK)):
            raise ValueError("%s gibt es schon und stammt nicht von 6D Studio – bitte umbenennen" % dest)
        for name in [EXPORT_INPUT] + sorted(set(WBPP_FOLDERS.values())):
            if os.path.isdir(os.path.join(dest, name)):
                shutil.rmtree(os.path.join(dest, name))            # nur Verknüpfungen – die Rohbilder bleiben
        for name in _export_files() + ["LIESMICH.txt"]:
            if os.path.isfile(os.path.join(dest, name)):
                os.remove(os.path.join(dest, name))
    else:
        os.makedirs(dest)
    with open(os.path.join(dest, EXPORT_MARK), "w") as f:
        f.write("Von 6D Studio erzeugt – Rohbilder/ und LIESMICH.txt werden beim nächsten Export ersetzt, "
                "Ergebnisse und wbpp/ bleiben.\n")


def export_pixinsight(out, session, library):
    """<Sitzung>/pixinsight/Rohbilder/: Light/ Dark/ Flat/ Bias/ (harte Verknüpfungen, kein zusätzlicher Platz),
    Listen der Originalpfade je Typ und bilder.csv; LIESMICH.txt in pixinsight/. In WBPP: „+ Directory“ auf
    Rohbilder – nicht auf pixinsight/, dort liegen auch die Ergebnisse und Masters von WBPP."""
    base = session_path(out, session)
    dest = os.path.join(base, "pixinsight")
    inp = os.path.join(dest, EXPORT_INPUT)
    sets = frame_sets(out, session, library)
    if not sets["light"]["files"]:
        raise ValueError("Keine Lights in %s" % session)
    _refresh_export(dest)
    counts, rows = {}, []
    for ftype, s in sets.items():
        if not s["files"]:
            continue
        sub = os.path.join(inp, WBPP_FOLDERS[ftype])
        os.makedirs(sub, exist_ok=True)
        for p in s["files"]:
            _link(p, os.path.join(sub, wbpp_name(p, ftype)))
            m = meta_of(p)
            rows.append({"typ": ftype, "datei": p, "zeit": m.get("time") or "", "iso": m.get("iso") or "",
                         "belichtung_s": m.get("exposure") or "", "temp_c": m.get("temp") if m.get("temp") is not None
                         else "", "blende": m.get("fnumber") or "", "brennweite_mm": m.get("focal") or "",
                         "objektiv": m.get("lens") or "", "herkunft": s["from"] or session})
        with open(os.path.join(inp, astro.FRAME_TYPES[ftype]["folder"] + ".txt"), "w") as f:
            f.write("\n".join(s["files"]) + "\n")
        counts[ftype] = len(s["files"])
    with open(os.path.join(inp, "bilder.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    notes = ["PixInsight – Weighted Batch Preprocessing (WBPP) für %s" % session, "",
             "Im Studio: Bilder → Sitzung → „Mit PixInsight stapeln“ macht alles Folgende ohne Oberfläche.", "",
             "Von Hand:",
             "1. Script → Batch Processing → WeightedBatchPreprocessing",
             "2. „+ Directory“ und den Ordner %s wählen – WBPP erkennt Light, Dark, Flat und Bias am" % EXPORT_INPUT,
             "   Dateinamen (die Rohbilder sind nur verknüpft, nicht kopiert). Nicht diesen ganzen Ordner: hier",
             "   liegen auch Ergebnisse (wbpp_*.xisf) und Masters (wbpp/master) früherer Läufe.",
             "3. Lights-Reiter: CFA images an, Debayer-Muster Auto (Canon EOS 6D: RGGB).",
             "4. Dark-Flats liegen bei den Darks – WBPP ordnet sie über die Belichtungszeit den Flats zu.",
             "5. Run.", "",
             "Ordner:",
             "  %-14s Verknüpfungen auf die Rohbilder, Listen (*.txt), bilder.csv" % (EXPORT_INPUT + "/"),
             "  %-14s Arbeitsordner von WBPP im Studio: master/ (Masters), logs/ (Protokolle)" % (WBPP_DIR + "/"),
             "  %-14s Ergebnis von WBPP, *_fertig-pi.* fertig entwickelt" % "wbpp_*.xisf", "", "Enthalten:"]
    for ftype, n in counts.items():
        s = sets[ftype]
        notes.append("  %-9s %3d Bilder%s%s" % (WBPP_FOLDERS[ftype] if ftype != "darkflat" else "Dark-Flat", n,
                                                " – aus %s (zum Master %s)" % (s["from"], s["master"]["name"])
                                                if s["from"] else "",
                                                " – %d mit anderer ISO/Zeit/Optik weggelassen" % s["skipped"]
                                                if s["skipped"] else ""))
    for ftype in ("bias", "dark", "flat"):
        if not counts.get(ftype):
            m = sets[ftype]["master"]
            notes.append("  %-9s fehlt%s" % (WBPP_FOLDERS[ftype], " – Master %s ist FITS; WBPP nimmt Masters nur als "
                                             "XISF" % m["name"] if m else ""))
    with open(os.path.join(dest, "LIESMICH.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(notes) + "\n")
    return {"folder": dest, "input": inp, "counts": counts,
            "skipped": {t: s["skipped"] for t, s in sets.items() if s["skipped"]},
            "from": {t: s["from"] for t, s in sets.items() if s["from"]},
            "lights_raw": sum(kind_of(p) == "raw" for p in sets["light"]["files"])}


# ---------------------------------------------------------------- PixInsight ohne Oberfläche
# Das Studio startet PixInsight als neue Instanz im Automatikbetrieb (Kommandozeile siehe pi_command) und liest
# den Fortschritt von der Standardausgabe: Zeilen, die mit PI_MARK beginnen, schreiben die Skripte in
# studio/pixinsight/ (WBPP: Ereignisskript; Fertig entwickeln: 6d-fertig.js).
PI_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pixinsight")
PI_EVENTS = os.path.join(PI_SCRIPTS, "6d-wbpp-ereignisse.js")
PI_FINISH = os.path.join(PI_SCRIPTS, "6d-fertig.js")
PI_MARK = "6D>> "
WBPP_DIR = "wbpp"                                # Arbeitsordner von WBPP in pixinsight/
WBPP_KEEP = ("master", "logs")                   # bleiben nach dem Lauf: Masters der Kalibrierbilder, Protokolle
WBPP_RESULT = re.compile(r"^wbpp_(\d+)x([0-9.]+)s(?=[_.]|$)")
WBPP_FAILED = (3, 4)                             # OperationBlockStatus: FAILED, CANCELED (BPP-OperationQueue.js)
# Der Automatikbetrieb liest die Einstellungen der WBPP-Oberfläche nicht, es gelten die Voreinstellungen von WBPP
# (Gewichtung PSF Signal, lokale Normalisierung, Autocrop, Debayer-Muster aus der RAW) – bis auf diese:
WBPP_OPTIONS = ("platesolve=false",)             # Astrometrie braucht die Gaia-Datenbank, sonst schlägt sie fehl
WBPP_LABELS = {"Debayer": "Debayern", "Measurements": "Bilder bewerten", "Weights generation": "Gewichte berechnen",
               "Bad frames rejection": "Schlechte Bilder aussortieren", "Frame selection": "Bilder auswählen",
               "Reference frame data preparation": "Referenzbild vorbereiten",
               "Reference frame selection": "Referenzbild wählen", "Registration": "Ausrichten",
               "LN reference generation": "Referenz für lokale Normalisierung",
               "Local Normalization": "Lokal normalisieren", "Autocrop": "Ränder beschneiden",
               "Astrometric solution": "Astrometrie", "Cosmetic Correction": "Hotpixel entfernen",
               "LPS": "Streifen entfernen", "FastIntegration": "Schnell stapeln", "Fast Integration": "Schnell stapeln",
               "Drizzle Integration": "Drizzle", "RGB Combination": "Farbkanäle zusammensetzen"}
FINISHED = re.compile(r"^(.*)_fertig(-pi)?$")    # <Stapel>_fertig (Siril) bzw. <Stapel>_fertig-pi (PixInsight)
PI_FINISH_SUFFIX = "_fertig-pi"
PI_FINISH_STEPS = ("Bild laden", "Hintergrund und Gradient entfernen", "Farben kalibrieren", "Strecken",
                   "Grünstich entfernen", "Speichern")                  # wie in 6d-fertig.js


def pixinsight_cli(found=None):
    """Programmdatei von PixInsight (macOS: PixInsight.app/Contents/MacOS/PixInsight)."""
    found = find_tools() if found is None else found
    app = found.get("pixinsight")
    exe = os.path.join(app, "Contents", "MacOS", "PixInsight") if app else None
    return exe if exe and os.path.isfile(exe) else None


def pixinsight_root(exe):
    """Installationsordner: …/PixInsight/PixInsight.app/Contents/MacOS/PixInsight → …/PixInsight (macOS),
    /opt/PixInsight/bin/PixInsight → /opt/PixInsight (Linux)."""
    d = os.path.dirname(exe)
    if d.endswith(os.path.join("Contents", "MacOS")):
        return os.path.dirname(os.path.dirname(os.path.dirname(d)))
    return os.path.dirname(d)


def wbpp_script(exe):
    p = os.path.join(pixinsight_root(exe), "src", "scripts", "BatchPreprocessing", "WBPP.js") if exe else ""
    return p if os.path.isfile(p) else None


def pi_command(exe, script, args):
    """Skript in einer neuen PixInsight-Instanz ohne Oberfläche und ohne den Fokus zu nehmen; PixInsight beendet
    sich danach (laut doc/docs/CommandLineInterface). Die Argumente trennt PixInsight am Komma (jsArguments)."""
    for a in [script] + list(args):
        if "," in a:
            raise ValueError("PixInsight trennt die Angaben für Skripte am Komma – bitte Ordner ohne Komma: %s" % a)
    return [exe, "-n", "--automation-mode", "--no-focus-stealing", "-r=" + ",".join([script] + list(args)),
            "--force-exit"]


def prepare_wbpp(out, session, library, exe):
    """Für PixInsight exportieren und die WBPP-Kommandozeile bauen: Eingang pixinsight/Rohbilder (dir= liest
    rekursiv – deshalb nicht pixinsight/ selbst, sonst sähe WBPP auch wbpp/ und frühere Ergebnisse), Ausgabe nach
    pixinsight/wbpp/."""
    script = wbpp_script(exe)
    if not script:
        raise ValueError("WBPP nicht gefunden – PixInsight 1.9.6 oder neuer nötig")
    res = export_pixinsight(out, session, library)
    if not res["lights_raw"]:
        raise ValueError("Keine RAW-Lights in %s – WBPP braucht RAWs" % session)
    dest = res["folder"]
    wbpp = os.path.join(dest, WBPP_DIR)
    os.makedirs(wbpp, exist_ok=True)
    args = ["automationMode=true", "dir=" + res["input"], "outputDirectory=" + wbpp, "usePipelineScript=true",
            "pipelineScriptFile=" + PI_EVENTS] + list(WBPP_OPTIONS)
    return {"folder": dest, "out": wbpp, "command": pi_command(exe, script, args), "counts": res["counts"],
            "lights": res["counts"].get("light", 0),
            "calib": sum(n for t, n in res["counts"].items() if t != "light")}


def pi_event(line):
    """Zeile der Standardausgabe von PixInsight → Ereignis (dict) oder None. WBPP (6d-wbpp-ereignisse.js):
    pipeline, start, done, end; Fertig entwickeln (6d-fertig.js): step, saved, fertig, error."""
    line = line.strip()
    if not line.startswith(PI_MARK):
        return None
    word, _, rest = line[len(PI_MARK):].partition(" ")
    if word == "pipeline":
        return {"event": word, "count": int(rest)} if rest.strip().isdigit() else None
    if word in ("start", "done"):
        parts = [p.strip() for p in rest.split(" | ")]
        m = re.match(r"(\d+)/(\d+) (.+)$", parts[0])
        if not m:
            return None
        ev = {"event": word, "index": int(m.group(1)), "count": int(m.group(2)), "name": m.group(3).strip(),
              "group": parts[1] if len(parts) > 1 else ""}
        if word == "done":
            ev["status"] = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            ev["message"] = parts[3] if len(parts) > 3 else ""
            ev["failed"] = ev["status"] in WBPP_FAILED
        return ev
    return {"event": word, "text": rest.strip()}


def wbpp_label(name, group=""):
    """Schritt von WBPP auf Deutsch – „Integration“ mit der Bildgruppe („Dark frames, …“ → Master-Dark erzeugen)."""
    kind = group.split(",")[0].split(" ")[0] if group else ""
    if name == "Integration":
        return "Lights stapeln" if kind == "Light" else "Master-%s erzeugen" % kind if kind else "Stapeln"
    if name == "Calibration":
        return {"Light": "Lights kalibrieren", "Flat": "Flats kalibrieren"}.get(kind, "Kalibrieren")
    return WBPP_LABELS.get(name, name)


def wbpp_detail(group):
    """Bildgruppe von WBPP kurz: „Light frames, 5496x3670, NoFilter, CFA, 1x1, 3.20s, frames = 8 (8 active)“ →
    „8 Lights · 3.2 s“."""
    if not group:
        return ""
    kind = group.split(",")[0].split(" ")[0]
    n = re.search(r"frames = (\d+)", group)
    exp = re.search(r",\s*([0-9.]+)s\b", group)
    parts = ["%s %s" % (n.group(1), {"Light": "Lights", "Dark": "Darks", "Flat": "Flats"}.get(kind, kind))
             if n else kind]
    if exp and float(exp.group(1)) >= 0.01:          # Bias: WBPP rundet 1/4000 s auf „0.00s“
        parts.append("%g s" % float(exp.group(1)))
    return " · ".join(parts)


def wbpp_problem(wbpp, since):
    """Fehlerzeilen aus dem neuesten WBPP-Protokoll (logs/*.log) seit since – wenn WBPP gar nicht erst anfängt
    (z. B. Fehler in der Prüfung vor dem Lauf), steht der Grund nur dort."""
    logs = os.path.join(wbpp, "logs")
    files = [os.path.join(logs, n) for n in os.listdir(logs) if n.endswith(".log")] if os.path.isdir(logs) else []
    files = [p for p in files if os.path.getmtime(p) >= since - 1]
    if not files:
        return ""
    with open(max(files, key=os.path.getmtime), encoding="utf-8", errors="replace") as f:
        errors = [re.sub(r"^\[[^\]]*\]\s*", "", line).strip() for line in f if "*** Error" in line]
    return " · ".join(re.sub(r"<[^>]+>", "", e) for e in errors[:2])


def _tree_size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for n in files:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def collect_wbpp(dest, since, lights=None):
    """Nach WBPP: das Master-Light (beschnitten, sonst das ganze) wird pixinsight/wbpp_<n>x<Zeit>s.xisf; die
    Zwischenbilder in wbpp/ (calibrated, debayered, registered … – je Light der 6D rund 300 MB) und das
    unbeschnittene Master mit den Ausreißerkarten werden gelöscht. Masters der Kalibrierbilder und Protokolle
    bleiben. → (Pfad des Ergebnisses, freigegebene Bytes)"""
    wbpp = os.path.join(dest, WBPP_DIR)
    master = os.path.join(wbpp, "master")
    found = [os.path.join(master, n) for n in os.listdir(master)
             if n.startswith("masterLight") and n.lower().endswith(".xisf")] if os.path.isdir(master) else []
    found = [p for p in found if os.path.getmtime(p) >= since - 1]
    if not found:
        why = wbpp_problem(wbpp, since)
        raise ValueError("WBPP hat kein Master-Light erzeugt" + (": " + why if why else
                                                                 " – Protokoll in pixinsight/wbpp/logs"))
    found.sort(key=lambda p: ("_autocrop" not in os.path.basename(p), -os.path.getmtime(p)))
    best = found[0]
    header, _ = imaging.image_header(best)
    n = lights or len(_files(os.path.join(dest, EXPORT_INPUT, WBPP_FOLDERS["light"])))
    result = os.path.join(dest, "wbpp_%dx%gs.xisf" % (n, round(float(header.get("EXPTIME") or 0), 1)))
    os.replace(best, result)
    drop = [p for p in found[1:] if os.path.basename(p).replace("_autocrop", "") ==
            os.path.basename(best).replace("_autocrop", "")]        # das unbeschnittene Gegenstück desselben Stapels
    drop += [os.path.join(master, n) for n in os.listdir(master) if n.startswith("LN_Reference")]
    drop += [os.path.join(wbpp, n) for n in os.listdir(wbpp) if n not in WBPP_KEEP]
    freed = 0
    for p in drop:
        freed += _tree_size(p)
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    return result, freed


def _finish_source(out, rel):
    """Gestapeltes, farbiges Bild für „Fertig entwickeln“ (beide Programme) → absoluter Pfad."""
    path = safe_path(out, rel)
    if kind_of(path) not in DATA_KINDS or not os.path.isfile(path):
        raise ValueError("Fertig entwickeln geht mit gestapelten Bildern (FITS oder XISF: Live-Stack, Siril, "
                         "PixInsight)")
    if "/Bibliothek/" in path.replace(os.sep, "/"):
        raise ValueError("Masters der Bibliothek sind Kalibrierbilder, keine Ergebnisse")
    _, channels = imaging.image_header(path)
    if channels != 3:
        raise ValueError("Nur farbige (debayerte) Stapel lassen sich fertig entwickeln")
    return path


def prepare_pi_finish(out, rel, exe):
    """Fertig entwickeln mit PixInsight (6d-fertig.js) → <Stapel>_fertig-pi.jpg und .tif daneben."""
    path = _finish_source(out, rel)
    target = os.path.splitext(path)[0] + PI_FINISH_SUFFIX
    return {"folder": os.path.dirname(path), "command": pi_command(exe, PI_FINISH, [path, target]),
            "steps": list(PI_FINISH_STEPS), "outputs": [target + ".jpg", target + ".tif"]}


# ---------------------------------------------------------------- Siril
SIRIL_SCRIPT = "vorverarbeitung.ssf"


def _q(path):
    return '"%s"' % path if " " in path else path


def siril_script(sets, black=2047):
    """Siril-Skript nach dem Vorbild von OSC_Preprocessing.ssf aus Siril 1.4 (requires 1.3.4, -32b). Arbeitsordner:
    <Sitzung>/siril mit den verknüpften RAWs. Fehlt ein Kalibriertyp, nimmt es den Master aus der Bibliothek (Siril
    liest unsere FITS direkt), sonst den Schwarzwert als Offset. Vor jedem Schritt steht „# >> <Schritt>“ – Siril
    gibt Kommentare wörtlich aus, daran erkennt das Studio den Fortschritt (Sirils eigene Meldungen sind übersetzt)."""
    has = {t: bool(s["files"]) for t, s in sets.items()}
    lines = ["############################################",
             "# 6D Studio – Vorverarbeitung für Siril",
             "# erstellt %s" % time.strftime("%Y-%m-%d %H:%M"),
             "# Arbeitsordner: dieser Ordner (lights, darks, flats, biases, darkflats",
             "# sind Verknüpfungen auf die RAWs der Sitzung)",
             "# Aufruf: siril-cli -d <dieser Ordner> -s %s" % SIRIL_SCRIPT,
             "############################################", "", "requires 1.3.4", ""]

    def stack_raw(folder, name, label, extra, out_name, calibrate=None):
        lines.extend(["# >> %s einlesen" % label, "cd %s" % folder, "convert %s -out=../process" % name,
                      "cd ../process"])
        seq = name
        if calibrate:
            lines.extend(["# >> %s kalibrieren" % label, "calibrate %s %s" % (name, calibrate)])
            seq = "pp_" + name
        lines.extend(["# >> Master-%s stapeln" % MASTER_LABEL[name],
                      "stack %s rej 3 3 %s -out=../masters/%s" % (seq, extra, out_name), "cd ..", ""])
        return "../masters/" + out_name

    def master(kind):
        m = sets[kind]["master"]
        return _q(m["path"]) if m and m.get("path") else None

    bias = stack_raw("biases", "bias", "Bias", "-nonorm", "bias_stacked") if has["bias"] else master("bias")
    offset = bias or '"=%d"' % black
    darkflat = (stack_raw("darkflats", "darkflat", "Dark-Flats", "-nonorm", "darkflat_stacked")
                if has["darkflat"] else None)
    if has["flat"]:
        cal = "-dark=%s" % darkflat if darkflat else "-bias=%s" % offset
        flat = stack_raw("flats", "flat", "Flats", "-norm=mul", "pp_flat_stacked", calibrate=cal)
    else:
        flat = master("flat")
    dark = stack_raw("darks", "dark", "Darks", "-nonorm", "dark_stacked") if has["dark"] else master("dark")
    cal = []
    if dark:
        cal += ["-dark=%s" % dark, "-cc=dark"]
    else:
        cal.append("-bias=%s" % offset)
    if flat:
        cal.append("-flat=%s" % flat)
    lines.extend(["# >> Lights einlesen", "cd lights", "convert light -out=../process", "cd ../process",
                  "# >> Lights kalibrieren und debayern",
                  "calibrate light %s -cfa -equalize_cfa -debayer" % " ".join(cal),
                  "# >> Lights ausrichten (Homographie)", "register pp_light",
                  "# >> Lights stapeln",
                  "stack r_pp_light rej 3 3 -norm=addscale -output_norm -rgb_equal -32b -out=result",
                  "# >> Ergebnis speichern",
                  "load result", "mirrorx -bottomup", "save ../ergebnis_$LIVETIME:%d$s", "cd ..", "close", ""])
    return "\n".join(lines)


MASTER_LABEL = {"bias": "Bias", "darkflat": "Dark-Flat", "flat": "Flat", "dark": "Dark"}
FINISH_SUFFIX = "_fertig"


def script_steps(script):
    """Schritte eines Skripts („# >> …“) – für die Fortschrittsanzeige."""
    return [line[5:].strip() for line in script.splitlines() if line.startswith("# >> ")]


def prepare_siril(out, session, library):
    """<Sitzung>/siril/ mit verknüpften RAWs und dem Skript. → Infos für die Oberfläche."""
    base = session_path(out, session)
    sets = frame_sets(out, session, library)
    lights = [p for p in sets["light"]["files"] if kind_of(p) == "raw"]
    if not lights:
        raise ValueError("Keine RAW-Lights in %s – Siril braucht RAWs" % session)
    dest = os.path.join(base, "siril")
    if os.path.isdir(dest) and not os.path.isfile(os.path.join(dest, EXPORT_MARK)):
        raise ValueError("%s gibt es schon und stammt nicht von 6D Studio – bitte umbenennen" % dest)
    keep = [n for n in os.listdir(dest) if kind_of(n) in ("fits", "tiff", "jpeg", "png")
            and os.path.isfile(os.path.join(dest, n))] if os.path.isdir(dest) else []
    if keep:                                          # frühere Ergebnisse behalten
        tmp = os.path.join(base, ".siril-ergebnisse")
        os.makedirs(tmp, exist_ok=True)
        for n in keep:
            os.replace(os.path.join(dest, n), os.path.join(tmp, n))
    _fresh_dir(dest)
    for n in keep:
        os.replace(os.path.join(base, ".siril-ergebnisse", n), os.path.join(dest, n))
    if keep:
        os.rmdir(os.path.join(base, ".siril-ergebnisse"))
    for f, t in FRAME_FOLDERS.items():
        s = sets[t]
        if s["files"] and kind_of(s["files"][0]) == "raw":
            os.makedirs(os.path.join(dest, f))
            for p in s["files"]:
                _link(p, os.path.join(dest, f, os.path.basename(p)))
        if s["master"] and not s["files"]:
            s["master"]["path"] = library.path(s["master"]["id"])
            imaging.to_bottom_up(s["master"]["path"])  # ältere Masters: sonst läge er gespiegelt über den Lights
    script = siril_script(sets)
    with open(os.path.join(dest, SIRIL_SCRIPT), "w", encoding="utf-8") as f:
        f.write(script)
    used = {t: ("%d RAWs" % len(s["files"]) if s["files"] else ("Master %s" % s["master"]["name"])
                if s["master"] else None) for t, s in sets.items()}
    for t, s in sets.items():
        if s["skipped"] and used[t]:
            used[t] += " (%d mit anderer ISO/Zeit/Optik weggelassen)" % s["skipped"]
    return {"folder": dest, "script": os.path.join(dest, SIRIL_SCRIPT), "used": used,
            "steps": script_steps(script), "lights": len(lights)}


def cleanup_siril(folder):
    """Nach dem Stapeln: Zwischenbilder (process/) löschen – bei 69 Lights der 6D rund 40 GB. Masters und Ergebnis
    bleiben. → freigegebene Bytes."""
    process = os.path.join(folder, "process")
    if not os.path.isdir(process) or not os.path.isfile(os.path.join(folder, EXPORT_MARK)):
        return 0
    freed = 0
    for root, _, files in os.walk(process):
        for n in files:
            try:
                freed += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    shutil.rmtree(process)
    return freed


def finish_script(name, out_name):
    """Fertig entwickeln mit Siril: Hintergrund (subsky, Polynom 1. Grades), Grünstich (rmgreen), automatisch
    strecken, als 16-Bit-TIFF und JPEG speichern."""
    return "\n".join([
        "# 6D Studio – fertig entwickeln: %s" % name, "requires 1.3.4",
        "# >> Bild laden", "load %s" % _q(name),
        "# >> Hintergrund und Gradient entfernen", "subsky 1 -samples=20 -tolerance=1.0 -smooth=0.5",
        "# >> Grünstich entfernen", "rmgreen",
        "# >> Strecken", "autostretch",
        "# >> Speichern", "savetif %s" % _q(out_name), "savejpg %s 92" % _q(out_name), "close", ""])


def prepare_finish(out, rel):
    """Gestapeltes Bild (Live-Stack-, Siril-FITS oder WBPP-XISF – Siril 1.4 liest XISF) für das Fertig-Skript von
    Siril vorbereiten. → Infos mit Skript."""
    path = _finish_source(out, rel)
    if kind_of(path) == "fits":
        imaging.to_bottom_up(path)                   # ältere Live-Stacks: sonst steht das Ergebnis auf dem Kopf
    folder, name = os.path.split(path)
    stem = os.path.splitext(name)[0]
    script = finish_script(name, stem + FINISH_SUFFIX)
    script_path = os.path.join(folder, ".6d-fertig.ssf")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)
    return {"folder": folder, "script": script_path, "steps": script_steps(script),
            "outputs": [os.path.join(folder, stem + FINISH_SUFFIX + e) for e in (".jpg", ".tif")]}


def siril_cli(found=None):
    """siril-cli im App-Paket (Siril 1.4: Contents/MacOS/siril-cli; ersatzweise siril mit -s – läuft dann ohne
    Oberfläche, laut Handbuchseite)."""
    found = find_tools() if found is None else found
    app = found.get("siril")
    for name in ("siril-cli", "siril"):
        cli = os.path.join(app, "Contents", "MacOS", name) if app else None
        if cli and os.path.isfile(cli):
            return cli
    return None
