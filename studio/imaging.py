"""Bilddaten für Live-Stack und Kalibrier-Bibliothek: RAW, JPEG und FITS lesen, FITS und PNG schreiben, Metadaten.

numpy und rawpy sind optional (venv: requirements.txt). Ohne sie läuft das Studio wie bisher, nur Live-Stack und
Master-Erstellung sind aus. Metadaten (ISO, Zeit, Sensortemperatur) und FITS-Köpfe lesen geht auch ohne.

Konventionen:
  * RAW-Daten bleiben ungestreckt in ADU (Kamerawerte, inkl. Schwarzwert ~2047 bei der 6D). Das Bayer-Muster wird
    beim Lesen auf RGGB gebracht (ggf. eine Zeile/Spalte weg) – Masters und Lights passen dadurch immer zueinander.
  * FITS: 32-Bit-Gleitkomma, Werte 0…1 = ADU / 65535 wie bei Siril, Zeilen von oben (ROWORDER = 'TOP-DOWN').
"""
import os
import struct
import subprocess
import tempfile
import zlib

try:
    import numpy as np
except ImportError:                 # System-Python ohne venv
    np = None
try:
    import rawpy
except ImportError:
    rawpy = None

RAW_EXT = (".cr2", ".cr3", ".dng", ".nef", ".arw", ".raf", ".orf", ".rw2")
JPEG_EXT = (".jpg", ".jpeg")
FITS_EXT = (".fit", ".fits", ".fts")
FULL_SCALE = 65535.0

# Sensortemperatur im Canon-MakerNote (CameraInfo, Tag 0x000d): Byte-Position je Modell, Wert − 128 = °C.
# Für die 6D an einer echten CR2 geprüft: ISO, Blende und Zeit an 0x06/0x03/0x04 stimmen mit den EXIF-Werten.
CANON_TEMP_OFFSET = {"Canon EOS 6D": 0x1B}


def missing():
    """None, wenn Live-Stack und Master-Erstellung möglich sind – sonst ein Hinweis für die Oberfläche."""
    gone = [n for n, m in (("numpy", np), ("rawpy", rawpy)) if m is None]
    if gone:
        return "%s fehlt – einmalig: .venv/bin/pip install -r requirements.txt" % " und ".join(gone)
    return None


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    return "raw" if ext in RAW_EXT else "jpeg" if ext in JPEG_EXT else "fits" if ext in FITS_EXT else None


# ---------------------------------------------------------------- Metadaten (nur Standardbibliothek)
_TIFF_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


def _ifd(d, bo, off):
    n = struct.unpack_from(bo + "H", d, off)[0]
    tags = {}
    for i in range(n):
        p = off + 2 + 12 * i
        tag, typ, cnt = struct.unpack_from(bo + "HHI", d, p)
        voff = p + 8
        if _TIFF_SIZE.get(typ, 1) * cnt > 4:
            voff = struct.unpack_from(bo + "I", d, voff)[0]
        tags[tag] = (typ, cnt, voff)
    return tags


def _val(d, bo, entry):
    typ, cnt, off = entry
    if typ == 2:
        return d[off:off + cnt].split(b"\0")[0].decode("latin-1").strip()
    if typ == 3:
        vals = struct.unpack_from(bo + "%dH" % cnt, d, off)
    elif typ in (4, 13):
        vals = struct.unpack_from(bo + "%dI" % cnt, d, off)
    elif typ in (5, 10):
        raw = struct.unpack_from(bo + ("%dI" if typ == 5 else "%di") % (2 * cnt), d, off)
        vals = [a / b if b else 0.0 for a, b in zip(raw[::2], raw[1::2])]
    elif typ in (1, 7):
        vals = d[off:off + cnt]
    else:
        return None
    return vals[0] if cnt == 1 else list(vals)


def read_meta(path):
    """ISO, Belichtungszeit, Aufnahmezeit, Kamera, Sensortemperatur und Optik (Blende, Brennweite, Objektiv) aus
    CR2/DNG/JPEG (EXIF) – ohne numpy. Manuelle Objektive melden keine Blende (dann None)."""
    meta = {"iso": None, "exposure": None, "time": None, "model": None, "temp": None,
            "fnumber": None, "focal": None, "lens": None}
    try:
        with open(path, "rb") as f:
            d = f.read(4 << 20)                      # Kopf, EXIF und MakerNote stehen vorn
        if d[:2] == b"\xff\xd8":                     # JPEG: TIFF-Block im APP1-Segment „Exif“
            i = d.find(b"Exif\0\0", 0, 1 << 16)
            if i < 0:
                return meta
            d = d[i + 6:]
        bo = {b"II": "<", b"MM": ">"}[d[:2]]
        ifd0 = _ifd(d, bo, struct.unpack_from(bo + "I", d, 4)[0])
        if 0x110 in ifd0:
            meta["model"] = _val(d, bo, ifd0[0x110])
        if 0x8769 not in ifd0:
            return meta
        exif = _ifd(d, bo, _val(d, bo, ifd0[0x8769]))
        if 0x829A in exif:
            meta["exposure"] = float(_val(d, bo, exif[0x829A]))
        if 0x8827 in exif:
            iso = _val(d, bo, exif[0x8827])
            meta["iso"] = int(iso[0] if isinstance(iso, list) else iso)
        if 0x9003 in exif:
            t = _val(d, bo, exif[0x9003])           # „2026:11:18 23:14:05“
            meta["time"] = t[:10].replace(":", "-") + t[10:] if len(t) >= 19 else t
        if 0x9400 in exif:                           # EXIF 2.31 „Temperature“
            meta["temp"] = round(float(_val(d, bo, exif[0x9400])), 1)
        if 0x829D in exif:                           # FNumber
            f = float(_val(d, bo, exif[0x829D]))
            meta["fnumber"] = round(f, 1) if f > 0 else None
        if 0x920A in exif:                           # FocalLength
            f = float(_val(d, bo, exif[0x920A]))
            meta["focal"] = round(f, 1) if f > 0 else None
        if 0xA434 in exif:                           # LensModel
            lens = str(_val(d, bo, exif[0xA434])).strip()
            meta["lens"] = lens or None
        pos = CANON_TEMP_OFFSET.get(meta["model"])
        if pos is not None and 0x927C in exif:
            maker = _ifd(d, bo, exif[0x927C][2])
            if 0x000D in maker:
                _, cnt, off = maker[0x000D]
                if cnt > pos:
                    t = d[off + pos] - 128
                    if -40 <= t <= 85:
                        meta["temp"] = float(t)
    except (OSError, KeyError, struct.error, ValueError, IndexError, TypeError):
        pass
    return meta


# ---------------------------------------------------------------- FITS (Kopf ohne numpy)
def _card(key, value, comment=""):
    if isinstance(value, bool):
        v = "%20s" % ("T" if value else "F")
    elif isinstance(value, int):
        v = "%20d" % value
    elif isinstance(value, float):
        s = "%.10G" % value
        v = "%20s" % (s if any(c in s for c in ".EN") else s + ".0")
    else:
        v = "'%-8s'" % str(value).replace("'", "''")[:68]
    text = "%-8s= %s" % (key[:8].upper(), v)
    if comment:
        text += " / " + comment
    return text[:80].ljust(80)


def read_fits_header(path):
    """→ (Kopf als dict, Position der Daten). Reicht für die Bibliotheksliste."""
    header, pos = {}, 0
    with open(path, "rb") as f:
        while True:
            block = f.read(2880)
            if len(block) < 2880:
                raise ValueError("FITS-Kopf unvollständig: %s" % path)
            pos += 2880
            for i in range(0, 2880, 80):
                card = block[i:i + 80].decode("ascii", "replace")
                key = card[:8].strip()
                if key == "END":
                    return header, pos
                if card[8:10] != "= ":
                    continue
                raw = card[10:].strip()
                if raw.startswith("'"):
                    end = raw.find("'", 1)
                    while end > 0 and raw[end:end + 2] == "''":
                        end = raw.find("'", end + 2)
                    header[key] = raw[1:end].replace("''", "'").rstrip()
                    continue
                raw = raw.split("/")[0].strip()
                if raw in ("T", "F"):
                    header[key] = raw == "T"
                else:
                    try:
                        header[key] = int(raw)
                    except ValueError:
                        try:
                            header[key] = float(raw)
                        except ValueError:
                            header[key] = raw


def write_fits(path, data, cards):
    """data in ADU: (H, W) für CFA oder (H, W, 3) für RGB. cards: [(Schlüssel, Wert, Kommentar), …]."""
    arr = np.asarray(data, np.float32) / FULL_SCALE
    if arr.ndim == 3:
        arr = arr.transpose(2, 0, 1)                 # FITS-Achsen: Spalten, Zeilen, Ebenen
    shape = arr.shape[::-1]
    head = [_card("SIMPLE", True, "FITS"), _card("BITPIX", -32, "32-Bit-Gleitkomma"),
            _card("NAXIS", len(shape))]
    head += [_card("NAXIS%d" % (i + 1), n) for i, n in enumerate(shape)]
    head += [_card("BZERO", 0.0), _card("BSCALE", 1.0), _card("ROWORDER", "TOP-DOWN", "erste Zeile = oben")]
    head += [_card(k, v, c) for k, v, c in cards if v is not None]
    text = "".join(head) + "END".ljust(80)
    text += " " * (-len(text) % 2880)
    body = arr.astype(">f4").tobytes()
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(text.encode("ascii"))
        f.write(body)
        f.write(b"\0" * (-len(body) % 2880))
    os.replace(tmp, path)


def read_fits(path):
    """→ (Daten in ADU als float32: (H, W) oder (H, W, 3), Kopf)."""
    header, pos = read_fits_header(path)
    shape = [header["NAXIS%d" % (i + 1)] for i in range(header["NAXIS"])][::-1]
    dtype = {8: ">u1", 16: ">i2", 32: ">i4", -32: ">f4", -64: ">f8"}[header["BITPIX"]]
    count = 1
    for n in shape:
        count *= n
    with open(path, "rb") as f:
        f.seek(pos)
        arr = np.frombuffer(f.read(count * np.dtype(dtype).itemsize), dtype).reshape(shape).astype(np.float32)
    arr = arr * header.get("BSCALE", 1.0) + header.get("BZERO", 0.0)
    if header["BITPIX"] < 0:
        arr *= FULL_SCALE                            # 0…1 → ADU
    if header.get("ROWORDER", "TOP-DOWN") == "BOTTOM-UP":
        arr = arr[..., ::-1, :]
    if arr.ndim == 3:
        arr = np.ascontiguousarray(arr.transpose(1, 2, 0))
    return arr.astype(np.float32), header


# ---------------------------------------------------------------- Bilder laden
class Frame:
    """Ein geladenes Bild. data: CFA (H, W) bei RAW, sonst RGB (H, W, 3) – float32."""

    def __init__(self, path, data, cfa, black, white, wb, meta):
        self.path, self.data, self.cfa = path, data, cfa
        self.black, self.white, self.wb, self.meta = black, white, wb, meta


def load_raw(path):
    with rawpy.imread(path) as r:
        raw = r.raw_image_visible
        desc = r.color_desc.decode()
        names = [[desc[r.raw_pattern[y][x]] for x in range(2)] for y in range(2)]
        oy, ox = next((y, x) for y in range(2) for x in range(2) if names[y][x] == "R")
        h, w = (raw.shape[0] - oy) // 2 * 2, (raw.shape[1] - ox) // 2 * 2
        data = raw[oy:oy + h, ox:ox + w].astype(np.float32)        # RGGB
        black = float(np.mean(r.black_level_per_channel))
        white = float(r.white_level)
        wb = list(r.camera_whitebalance)
        if not wb[1] or len(set(wb[:3])) == 1:                      # nicht gesetzt → Tageslicht
            wb = list(r.daylight_whitebalance)
    g = wb[1] or 1.0
    wb = (wb[0] / g or 1.0, 1.0, wb[2] / g or 1.0)
    return Frame(path, data, True, black, white, wb, read_meta(path))


def read_bmp(path):
    with open(path, "rb") as f:
        d = f.read()
    off, = struct.unpack_from("<I", d, 10)
    w, h, _, bpp = struct.unpack_from("<iiHH", d, 18)
    if bpp not in (24, 32):
        raise ValueError("BMP mit %d Bit nicht unterstützt" % bpp)
    step = bpp // 8
    row = (w * step + 3) // 4 * 4
    rows = np.frombuffer(d, np.uint8, row * abs(h), off).reshape(abs(h), row)[:, :w * step]
    img = rows.reshape(abs(h), w, step)[..., 2::-1]                 # BGR(A) → RGB
    return img[::-1] if h > 0 else img                              # BMP steht meist auf dem Kopf


def load_jpeg(path):
    """JPEG über sips (macOS) dekodieren. Große Bilder 2×2 zusammenfassen – Arbeitsgröße wie beim RAW."""
    with tempfile.TemporaryDirectory() as tmp:
        bmp = os.path.join(tmp, "bild.bmp")
        subprocess.run(["sips", "-s", "format", "bmp", path, "--out", bmp], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        data = read_bmp(bmp).astype(np.float32)
    if data.shape[1] > 3000:
        data = bin2(data)
    return Frame(path, data, False, 0.0, 255.0, (1.0, 1.0, 1.0), read_meta(path))


def load(path):
    kind = kind_of(path)
    if kind == "raw":
        return load_raw(path)
    if kind == "jpeg":
        return load_jpeg(path)
    raise ValueError("Format nicht unterstützt: %s" % os.path.basename(path))


def bin2(a):
    h, w = a.shape[0] // 2 * 2, a.shape[1] // 2 * 2
    a = a[:h, :w]
    return (a[0::2, 0::2] + a[0::2, 1::2] + a[1::2, 0::2] + a[1::2, 1::2]) * 0.25


def superpixel(cfa):
    """RGGB → RGB in halber Auflösung: kein Interpolieren, schnell, rauscharm (wie „Superpixel“ bei Siril)."""
    g = (cfa[0::2, 1::2] + cfa[1::2, 0::2]) * 0.5
    return np.stack([cfa[0::2, 0::2], g, cfa[1::2, 1::2]], axis=-1)


# ---------------------------------------------------------------- PNG (zlib aus der Standardbibliothek)
def png_bytes(rgb8):
    h, w = rgb8.shape[:2]
    raw = np.zeros((h, w * 3 + 1), np.uint8)                        # Filter 0 je Zeile
    raw[:, 1:] = rgb8.reshape(h, w * 3)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw.tobytes(), 3)) + chunk(b"IEND", b""))
