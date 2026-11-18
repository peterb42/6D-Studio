"""Tests für 6D Studio – laufen ohne Kamera.

Ausführen:  python3 -m unittest discover -s tests -v
"""
import importlib.util
import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["OUT"] = tempfile.mkdtemp(prefix="6d-studio-test-")   # keine Testdateien in ~/Pictures
_spec = importlib.util.spec_from_file_location("studio", os.path.join(ROOT, "studio", "6d-studio.py"))
studio = importlib.util.module_from_spec(_spec)
_argv, sys.argv = sys.argv, ["6d-studio", "192.0.2.1"]
_spec.loader.exec_module(studio)
sys.argv = _argv


def record(typ, payload=b""):
    """Ein Canon-GetEvent-Datensatz: Länge, Typ, Nutzdaten."""
    return struct.pack("<II", 8 + len(payload), typ) + payload


class FakeOp:
    def __init__(self, data, rc=studio.RC_OK):
        self.rc, self.result = rc, data


def make_studio(test):
    """Studio-Objekt ohne Kameraverbindung; interne Sockets werden nach dem Test geschlossen."""
    s = studio.Studio()
    test.addCleanup(s.wake_r.close)
    test.addCleanup(s.wake_w.close)
    return s


class TestWerte(unittest.TestCase):
    """Canon-Codes → Anzeige. Die Codes in Klammern wurden an der echten 6D beobachtet."""

    def test_iso(self):
        self.assertEqual(studio.fmt_iso(0), "Auto")
        self.assertEqual(studio.fmt_iso(0x48), "100")
        self.assertEqual(studio.fmt_iso(0x70), "3200")      # 6D: 0x70
        self.assertEqual(studio.fmt_iso(0x80), "12800")
        self.assertEqual(studio.fmt_iso(0x4B), "125")

    def test_blende(self):
        self.assertEqual(studio.fmt_aperture(0x38), "f/8")   # 6D: 0x38
        self.assertEqual(studio.fmt_aperture(0x10), "f/1.4")
        self.assertEqual(studio.fmt_aperture(0x20), "f/2.8")
        self.assertEqual(studio.fmt_aperture(0), "–")

    def test_zeit(self):
        self.assertEqual(studio.fmt_shutter(0x0C), "Bulb")
        self.assertEqual(studio.fmt_shutter(0x10), '30"')
        self.assertEqual(studio.fmt_shutter(0x38), '1"')
        self.assertEqual(studio.fmt_shutter(0x6D), "1/100")  # 6D: 0x6d
        self.assertEqual(studio.fmt_shutter(0x98), "1/4000")

    def test_zeit_in_sekunden(self):
        self.assertEqual(studio.shutter_seconds(0x98), 1 / 4000)     # nicht 1/4096
        self.assertEqual(studio.shutter_seconds(0x10), 30)
        self.assertIsNone(studio.shutter_seconds(0x0C))              # Bulb

    def test_dauer(self):
        self.assertEqual(studio.fmt_duration(75), "1:15")
        self.assertEqual(studio.fmt_duration(3725), "1:02:05")


class TestProtokoll(unittest.TestCase):
    def test_jpeg_aus_bloecken(self):
        jpeg = b"\xff\xd8bild\xff\xd9"
        data = struct.pack("<II", 8 + len(jpeg), 1) + jpeg + struct.pack("<II", 12, 4) + b"meta"
        self.assertEqual(studio.extract_jpeg(data), jpeg)

    def test_jpeg_fallback(self):
        self.assertEqual(studio.extract_jpeg(b"xx\xff\xd8abc\xff\xd9yy"), b"\xff\xd8abc\xff\xd9")

    def test_device_info(self):
        def pstr(s):
            s += "\0"
            return bytes([len(s)]) + s.encode("utf-16-le")

        def arr(vals):
            return struct.pack("<I", len(vals)) + b"".join(struct.pack("<H", v) for v in vals)

        d = struct.pack("<HIH", 100, 0xB, 100) + pstr("") + struct.pack("<H", 0)
        d += arr([0x1001, 0x9128, 0x9153]) + arr([]) + arr([]) + arr([]) + arr([])
        d += pstr("Canon Inc.") + pstr("Canon EOS 6D")
        ops, model = studio.parse_device_info(d)
        self.assertEqual(model, "Canon EOS 6D")
        self.assertIn(0x9128, ops)


class TestEreignisse(unittest.TestCase):
    def setUp(self):
        self.s = make_studio(self)

    def test_werte_und_listen(self):
        data = record(studio.E_PROP_VALUE, struct.pack("<II", studio.P_ISO, 0x70))
        data += record(studio.E_AVAIL_LIST, struct.pack("<IIIIII", studio.P_ISO, 3, 3, 0x48, 0x50, 0x70))
        data += record(studio.E_PROP_VALUE, struct.pack("<II", studio.P_AEMODE, 4))
        data += record(0)
        self.s.on_events(FakeOp(data))
        self.assertEqual(self.s.props[studio.P_ISO], 0x70)
        self.assertEqual(self.s.avail[studio.P_ISO], [0x48, 0x50, 0x70])
        self.assertTrue(self.s.is_bulb_mode())

    def test_neues_bild_6d(self):
        """Die 6D meldet neue Bilder mit 0xC181 (ObjectAddedEx)."""
        self.s.on_events(FakeOp(record(0xC181, struct.pack("<I", 0x9001) + b"\0" * 36)))
        self.assertEqual(self.s.downloads.get_nowait(), (0x9001, False, None))   # (Handle, Transfer, Serienbild)

    def test_neues_bild_gehoert_zur_serie(self):
        frame = {"handles": []}
        self.s.pending_frame = frame
        self.s.on_events(FakeOp(record(0xC181, struct.pack("<I", 0x9003) + b"\0" * 36)))
        self.assertEqual(frame["handles"], [0x9003])
        self.assertIs(self.s.downloads.get_nowait()[2], frame)

    def test_kein_download_wenn_aus(self):
        self.s.autodownload = False
        self.s.on_events(FakeOp(record(0xC181, struct.pack("<I", 0x9002) + b"\0" * 36)))
        self.assertTrue(self.s.downloads.empty())

    def test_abschaltwarnung_haelt_wach(self):
        self.s.on_events(FakeOp(record(studio.E_WILL_SHUTDOWN)))
        self.assertEqual([op.code for op in self.s.internal_queue], [studio.KEEP_DEVICE_ON])

    def test_speicherort_karte(self):
        data = record(studio.E_PROP_VALUE, struct.pack("<II", studio.P_CAPTURE_DEST, studio.DEST_PC))
        data += record(studio.E_AVAIL_LIST, struct.pack("<IIIII", studio.P_CAPTURE_DEST, 3, 2, 1, studio.DEST_PC))
        self.s.on_events(FakeOp(data))
        op = self.s.internal_queue[-1]
        self.assertEqual(op.code, studio.SET_PROP)
        self.assertEqual(struct.unpack("<III", op.data), (12, studio.P_CAPTURE_DEST, 1))

    def test_kaputte_daten_brechen_nicht(self):
        self.s.on_events(FakeOp(struct.pack("<II", 4, 0xC189) + b"xx"))
        self.s.on_events(FakeOp(struct.pack("<II", 999, 0xC189)))


class TestNamenskonvention(unittest.TestCase):
    astro = studio.astro

    def test_objektname(self):
        self.assertEqual(self.astro.safe_name("Milchstraße über Öhringen!"), "Milchstrasse-ueber-Oehringen")
        self.assertEqual(self.astro.safe_name("  M 31 / Andromeda "), "M-31-Andromeda")
        self.assertEqual(self.astro.safe_name(""), "Ziel")

    def test_belichtung(self):
        self.assertEqual(self.astro.exposure_tag(30), "30s")
        self.assertEqual(self.astro.exposure_tag(2.5), "2.5s")
        self.assertEqual(self.astro.exposure_tag(1 / 4000), "1-4000s")
        self.assertEqual(self.astro.exposure_tag(None), "auto")

    def test_nacht_wechselt_mittags(self):
        import datetime
        ts = datetime.datetime(2026, 11, 19, 1, 30).timestamp()      # nach Mitternacht
        self.assertEqual(self.astro.night_date(ts), "2026-11-18")
        ts = datetime.datetime(2026, 11, 19, 13, 0).timestamp()      # nach Mittag
        self.assertEqual(self.astro.night_date(ts), "2026-11-19")

    def test_dateiname(self):
        import datetime
        ts = datetime.datetime(2026, 11, 18, 23, 14, 5).timestamp()
        self.assertEqual(self.astro.frame_name("light", "Milchstraße", ts, "3200", 30, "f/2.8", 7),
                         "LIGHT_Milchstrasse_20261118-231405_ISO3200_30s_f2.8_0007")
        self.assertEqual(self.astro.frame_name("bias", "Orion", ts, "3200", 1 / 4000, "–", 12),
                         "BIAS_Orion_20261118-231405_ISO3200_1-4000s_f--_0012")

    def test_naechste_nummer(self):
        d = tempfile.mkdtemp()
        for n in ("LIGHT_X_1_ISO100_30s_f2_0001.CR2", "LIGHT_X_1_ISO100_30s_f2_0001.JPG",
                  "LIGHT_X_2_ISO100_30s_f2_0012.CR2", "DARK_X_1_ISO100_30s_f2_0040.CR2", "notiz.txt"):
            open(os.path.join(d, n), "w").close()
        self.assertEqual(self.astro.next_index(d, "light"), 13)
        self.assertEqual(self.astro.next_index(d, "bias"), 1)
        self.assertEqual(self.astro.next_index(os.path.join(d, "fehlt"), "light"), 1)


class TestLupe(unittest.TestCase):
    def test_position_mitte_und_rand(self):
        s = make_studio(self)
        s.zoom_center = [0.5, 0.5]
        self.assertEqual(s.zoom_xy(10), (2462, 1641))
        s.zoom_center = [0.0, 1.0]
        x, y = s.zoom_xy(10)
        self.assertEqual(x, 0)
        self.assertEqual(y, int(studio.ZOOM_H - studio.ZOOM_H / 10))


if __name__ == "__main__":
    unittest.main()
