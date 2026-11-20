"""Kleine XISF-Dateien wie PixInsight sie schreibt – nur für die Tests (das Studio liest XISF nur).

Aufbau nach XISF 1.0: „XISF0100“, Länge des XML-Kopfs (uint32 LE), 4 Byte frei, Kopf, Daten als Anhang an einer
festen Position. Wie bei WBPP-Masters folgt dem eigentlichen Bild ein zweites (Maske), das der Leser übergehen muss.
"""
import zlib

import numpy as np

DATA_POS = 4096


def write_xisf(path, data, keywords=(), compression=None, planar=True, sample="Float32"):
    """data: (H, W) oder (H, W, 3); Float32 im Bereich 0…1, UInt16 in ADU. keywords: [(Name, Wert wie in FITS)].
    compression: None, "zlib", "zlib+sh" oder ein anderer Name (nur angegeben, nicht wirklich komprimiert)."""
    arr = np.asarray(data)
    h, w = arr.shape[:2]
    c = 1 if arr.ndim == 2 else arr.shape[2]
    dtype = np.dtype({"Float32": "<f4", "UInt16": "<u2"}[sample])
    pixels = arr.reshape(h, w, c)
    body = (pixels.transpose(2, 0, 1) if planar else pixels).astype(dtype).tobytes()
    attrs = ""
    if compression == "zlib":
        attrs, body = ' compression="zlib:%d"' % len(body), zlib.compress(body)
    elif compression == "zlib+sh":
        shuffled = np.frombuffer(body, np.uint8).reshape(-1, dtype.itemsize).T.tobytes()
        attrs, body = ' compression="zlib+sh:%d:%d"' % (len(body), dtype.itemsize), zlib.compress(shuffled)
    elif compression:
        attrs = ' compression="%s:%d"' % (compression, len(body))
    if not planar:
        attrs += ' pixelStorage="Normal"'
    if sample.startswith("Float"):
        attrs += ' bounds="0:1"'
    cards = "".join('<FITSKeyword name="%s" value="%s" comment=""/>' % (k, v) for k, v in keywords)
    mask = b"\x01\x02\x03\x04"
    xml = ('<?xml version="1.0" encoding="UTF-8"?><xisf version="1.0" xmlns="http://www.pixinsight.com/xisf">'
           '<Image id="integration_autocrop" geometry="%d:%d:%d" sampleFormat="%s" colorSpace="%s"%s '
           'location="attachment:%d:%d">%s</Image>'
           '<Image id="crop_mask" geometry="2:2:1" sampleFormat="UInt8" colorSpace="Gray" location="attachment:%d:4"/>'
           '</xisf>') % (w, h, c, sample, "RGB" if c == 3 else "Gray", attrs, DATA_POS, len(body), cards,
                         DATA_POS + len(body), )
    head = xml.encode("utf-8")
    assert 16 + len(head) <= DATA_POS
    with open(path, "wb") as f:
        f.write(b"XISF0100" + len(head).to_bytes(4, "little") + b"\0" * 4 + head)
        f.write(b"\0" * (DATA_POS - 16 - len(head)))
        f.write(body)
        f.write(mask)
    return path
