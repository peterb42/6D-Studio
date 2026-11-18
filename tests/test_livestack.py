"""Tests für Live-Stack, Kalibrier-Bibliothek und Bilddaten – ohne Kamera.

Rechentests brauchen numpy (und für RAWs rawpy) aus der venv; ohne werden sie übersprungen.
Ausführen:  .venv/bin/python -m unittest tests.test_livestack -v
"""
import datetime
import importlib.util
import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "studio"))
import calib  # noqa: E402
import imaging  # noqa: E402
import livestack  # noqa: E402

_spec = importlib.util.spec_from_file_location("sim6d_ls", os.path.join(ROOT, "sim", "sim6d.py"))
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)

np = imaging.np
HAS_NUMPY = np is not None
HAS_RAWPY = imaging.rawpy is not None


def tiff_with_makernote(model, camera_info):
    """Minimale CR2-ähnliche TIFF-Datei: IFD0 (Modell) → EXIF (ISO, Zeit) → Canon-MakerNote (CameraInfo)."""
    def entry(tag, typ, count, value):
        return struct.pack("<HHII", tag, typ, count, value)
    model_b = model.encode() + b"\0"
    ifd0_at, exif_at, maker_at, data_at = 8, 64, 128, 192
    ifd0 = struct.pack("<H", 2) + entry(0x110, 2, len(model_b), data_at) + entry(0x8769, 4, 1, exif_at) + b"\0" * 4
    exif = (struct.pack("<H", 3) + entry(0x829A, 5, 1, data_at + 32) + entry(0x8827, 3, 1, 3200)
            + entry(0x927C, 7, 6 + 12 + 4, maker_at) + b"\0" * 4)
    maker = struct.pack("<H", 1) + entry(0x000D, 7, len(camera_info), data_at + 48) + b"\0" * 4
    data = model_b.ljust(32, b"\0") + struct.pack("<II", 30, 1).ljust(16, b"\0") + camera_info
    out = bytearray(b"II*\0" + struct.pack("<I", ifd0_at))
    for pos, block in ((ifd0_at, ifd0), (exif_at, exif), (maker_at, maker), (data_at, data)):
        out += b"\0" * (pos - len(out)) + block
    return bytes(out)


class TestMetadaten(unittest.TestCase):
    """Läuft auch ohne numpy."""

    def test_temperatur_der_6d(self):
        info = bytearray(64)
        info[0x1B] = 128 + 14                                       # 14 °C (Wert − 128)
        with tempfile.NamedTemporaryFile(suffix=".CR2", delete=False) as f:
            f.write(tiff_with_makernote("Canon EOS 6D", bytes(info)))
        self.addCleanup(os.remove, f.name)
        m = imaging.read_meta(f.name)
        self.assertEqual((m["model"], m["iso"], m["exposure"], m["temp"]), ("Canon EOS 6D", 3200, 30.0, 14.0))

    def test_andere_kamera_ohne_temperatur(self):
        info = bytearray(64)
        info[0x1B] = 128 + 14
        with tempfile.NamedTemporaryFile(suffix=".CR2", delete=False) as f:
            f.write(tiff_with_makernote("Canon EOS 5D", bytes(info)))
        self.addCleanup(os.remove, f.name)
        self.assertIsNone(imaging.read_meta(f.name)["temp"])         # Position nur für die 6D geprüft

    def test_simulator_raw_metadaten(self):
        px = sim.Sensor().frame(2, 1600, 12.5)
        data = sim.dng_bytes(px, {"exposure": 2, "iso": 1600, "temp": 12.5, "time": "2026:11:18 23:14:05"})
        with tempfile.NamedTemporaryFile(suffix=".CR2", delete=False) as f:
            f.write(data)
        self.addCleanup(os.remove, f.name)
        m = imaging.read_meta(f.name)
        self.assertEqual((m["iso"], m["exposure"], m["temp"], m["time"]), (1600, 2.0, 12.5, "2026-11-18 23:14:05"))

    def test_fits_kopf_ohne_numpy(self):
        cards = "".join([imaging._card("SIMPLE", True), imaging._card("BITPIX", -32), imaging._card("NAXIS", 0),
                         imaging._card("EXPTIME", 30.0, "s"), imaging._card("INSTRUME", "Canon EOS 6D"),
                         imaging._card("CCD-TEMP", -3.5), "END".ljust(80)])
        with tempfile.NamedTemporaryFile(suffix=".fits", delete=False) as f:
            f.write(cards.ljust(2880).encode())
        self.addCleanup(os.remove, f.name)
        h, pos = imaging.read_fits_header(f.name)
        self.assertEqual(pos, 2880)
        self.assertEqual((h["EXPTIME"], h["INSTRUME"], h["CCD-TEMP"]), (30.0, "Canon EOS 6D", -3.5))
        self.assertIsInstance(h["EXPTIME"], float)

    def test_belichtungsklassen(self):
        self.assertEqual(calib._exposure_class(30, 30.4, False), 0)
        self.assertIsNone(calib._exposure_class(60, 30, False))      # ohne Optimierung nur gleiche Zeit
        self.assertEqual(calib._exposure_class(60, 30, True), 1)
        self.assertEqual(calib._exposure_class(20, 30, True), 2)
        self.assertIsNone(calib._exposure_class(10, 30, True))       # k müsste > 2 sein


def star_field(h, w, stars, a=1 + 0j, t=0j, sigma=1.6, bg=300.0, noise=10.0, seed=0):
    """CFA-Bild mit Gauß-Sternen an a·z + t (z = Sternposition)."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w), bg, np.float32)
    yy, xx = np.mgrid[0:17, 0:17]
    for (x, y, f) in stars:
        z = a * complex(x, y) + t
        x0, y0 = int(z.real) - 8, int(z.imag) - 8
        if 0 <= x0 < w - 17 and 0 <= y0 < h - 17:
            img[y0:y0 + 17, x0:x0 + 17] += f * np.exp(-((xx + x0 - z.real) ** 2 + (yy + y0 - z.imag) ** 2)
                                                      / (2 * sigma ** 2))
    return img + rng.normal(0, noise, img.shape).astype(np.float32)


@unittest.skipUnless(HAS_NUMPY, "braucht numpy (venv)")
class TestRechnung(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(3)
        cls.h, cls.w = 900, 1400
        cls.stars = list(zip(rng.uniform(0, cls.w, 250), rng.uniform(0, cls.h, 250), rng.uniform(80, 3000, 250)))

    def test_dark_optimierung_findet_k(self):
        rng = np.random.default_rng(5)
        thermal = np.abs(rng.normal(0, 2, (self.h, self.w))).astype(np.float32)
        hot = rng.choice(thermal.size, 3000, replace=False)
        thermal.flat[hot] += rng.exponential(250, 3000).astype(np.float32)
        light = star_field(self.h, self.w, self.stars) + 0.63 * thermal
        self.assertAlmostEqual(livestack.optimize_k(light, thermal), 0.63, delta=0.02)

    def test_ausrichtung_mit_drehung(self):
        rgb_ref = star_field(self.h, self.w, self.stars, seed=1)
        a_true, t_true = np.exp(1j * np.radians(1.2)), 21.5 - 13.25j
        moved = star_field(self.h, self.w, self.stars, a_true, t_true, seed=2)
        ref, _ = livestack.find_stars(rgb_ref)
        new, fwhm = livestack.find_stars(moved)
        self.assertGreater(len(new), 40)
        self.assertAlmostEqual(fwhm, 2.355 * 1.6, delta=1.0)
        a, t, n = livestack.register(new, ref)                         # neue Position → Referenz
        self.assertGreater(n, 30)
        self.assertAlmostEqual(np.degrees(np.angle(a)), -1.2, delta=0.01)
        z = complex(700, 450)                                          # Probe: Punkt hin und zurück
        self.assertLess(abs(a * (a_true * z + t_true) + t - z), 0.1)

    def test_fremdes_sternfeld_wird_abgelehnt(self):
        rng = np.random.default_rng(9)
        other = list(zip(rng.uniform(0, self.w, 250), rng.uniform(0, self.h, 250), rng.uniform(80, 3000, 250)))
        ref, _ = livestack.find_stars(star_field(self.h, self.w, self.stars))
        new, _ = livestack.find_stars(star_field(self.h, self.w, other))
        self.assertIsNone(livestack.register(new, ref))

    def test_hotpixel_filter_laesst_sterne_stehen(self):
        img = star_field(self.h, self.w, self.stars, sigma=1.0, noise=5)
        before = img.copy()
        img[100, 100] += 2000
        img[300, 501] += 900
        livestack.hot_filter(img)
        self.assertLess(img[100, 100] - before[100, 100], 100)
        self.assertLess(img[300, 501] - before[300, 501], 100)
        x, y, _ = max(self.stars, key=lambda s: s[2] if 20 < s[0] < self.w - 20 and 20 < s[1] < self.h - 20 else 0)
        self.assertEqual(img[int(round(y)), int(round(x))], before[int(round(y)), int(round(x))])

    def test_verzerren_trifft_die_referenz(self):
        rgb = np.repeat(star_field(self.h, self.w, self.stars)[..., None], 3, axis=2)
        a, t = np.exp(1j * np.radians(0.5)), 4.25 + 2.5j
        out, valid = livestack.warp(rgb, a, t)
        self.assertGreater(valid.mean(), 0.97)
        back, _ = livestack.find_stars(out[..., 0] + (~valid) * 300)
        reg = livestack.register(back, livestack.find_stars(rgb[..., 0])[0])     # verzerrt → Original
        z = complex(700, 450)                                          # Stern bei z liegt verzerrt bei a·z + t
        self.assertLess(abs(reg[0] * (a * z + t) + reg[1] - z), 0.2)

    def test_streckung_hintergrund_auf_ziel(self):
        rng = np.random.default_rng(1)
        img = (300 + rng.normal(0, 10, (200, 300, 3))).astype(np.float32)
        out = livestack.stretch(img, np.ones((200, 300), bool), (2.0, 1.0, 1.5), 13000, target=0.25)
        self.assertAlmostEqual(float(np.median(out)), 0.25 * 255, delta=3)

    def test_fits_hin_und_zurueck(self):
        d = tempfile.mkdtemp()
        cfa = np.arange(24, dtype=np.float32).reshape(4, 6) * 100 + 2047
        imaging.write_fits(os.path.join(d, "c.fits"), cfa, [("ISOSPEED", 1600, ""), ("EXPTIME", 30.0, "")])
        back, h = imaging.read_fits(os.path.join(d, "c.fits"))
        self.assertEqual(back.shape, (4, 6))
        self.assertLess(float(np.abs(back - cfa).max()), 0.01)
        self.assertEqual((h["ISOSPEED"], h["EXPTIME"], h["ROWORDER"]), (1600, 30.0, "TOP-DOWN"))
        rgb = np.random.default_rng(0).uniform(0, 9000, (5, 7, 3)).astype(np.float32)
        imaging.write_fits(os.path.join(d, "r.fits"), rgb, [])
        self.assertLess(float(np.abs(imaging.read_fits(os.path.join(d, "r.fits"))[0] - rgb).max()), 0.01)


def write_master(lib, kind, iso, exposure, temp, date, shape=(8, 12)):
    os.makedirs(lib.folder(kind), exist_ok=True)
    name = "M_%s_%d_%s_%s_%s.fits" % (kind, iso, exposure, temp, date.replace(":", ""))
    imaging.write_fits(os.path.join(lib.folder(kind), name), np.zeros(shape, np.float32),
                       [("ISOSPEED", iso, ""), ("EXPTIME", float(exposure), ""), ("CCD-TEMP", temp, ""),
                        ("DATE-OBS", date, ""), ("NCOMBINE", 20, ""), ("INSTRUME", "Canon EOS 6D", "")])
    return name


@unittest.skipUnless(HAS_NUMPY, "braucht numpy (venv)")
class TestBibliothek(unittest.TestCase):
    def setUp(self):
        self.lib = calib.Library(tempfile.mkdtemp())
        w = write_master
        self.d30_15 = w(self.lib, "dark", 3200, 30, 15.0, "2026-11-10T22:00:00")
        self.d30_9 = w(self.lib, "dark", 3200, 30, 9.0, "2026-11-12T22:00:00")
        self.d60_10 = w(self.lib, "dark", 3200, 60, 10.0, "2026-11-12T23:00:00")
        self.d30_iso1600 = w(self.lib, "dark", 1600, 30, 10.0, "2026-11-12T23:00:00")
        self.b_old = w(self.lib, "bias", 3200, 0.00025, 20.0, "2026-01-01T12:00:00")
        self.b_new = w(self.lib, "bias", 3200, 0.00025, 20.0, "2026-10-01T12:00:00")

    def test_liste(self):
        names = [e["name"] for e in self.lib.list()]
        self.assertEqual(len(names), 6)
        e = self.lib.get("darks/" + self.d30_9)
        self.assertEqual((e["iso"], e["exposure"], e["temp"], e["n"], e["w"], e["h"]), (3200, 30.0, 9.0, 20, 12, 8))

    def test_dark_gleiche_zeit_naechste_temperatur(self):
        best = self.lib.best("dark", 3200, 30, 10.0, (8, 12), "Canon EOS 6D")
        self.assertEqual(best["name"], self.d30_9)
        best = self.lib.best("dark", 3200, 30, 14.0, (8, 12))
        self.assertEqual(best["name"], self.d30_15)

    def test_dark_andere_zeit_nur_mit_optimierung(self):
        self.assertIsNone(self.lib.best("dark", 3200, 45, 10.0))
        self.assertEqual(self.lib.best("dark", 3200, 45, 10.0, optimize=True)["name"], self.d60_10)

    def test_iso_und_groesse_muessen_passen(self):
        self.assertIsNone(self.lib.best("dark", 6400, 30, 10.0))
        self.assertIsNone(self.lib.best("dark", 3200, 30, 10.0, shape=(912, 1368)))

    def test_bias_das_neueste(self):
        self.assertEqual(self.lib.best("bias", 3200)["name"], self.b_new)

    def test_loeschen_nur_in_der_bibliothek(self):
        with self.assertRaises(ValueError):
            self.lib.path("../../etc/passwd")
        self.lib.delete("bias/" + self.b_old)
        self.assertEqual(len(self.lib.list()), 5)

    @unittest.skipUnless(HAS_RAWPY, "braucht rawpy (venv)")
    def test_zu_kurze_darks_sind_bias(self):
        meta = {"exposure": 1 / 4000, "iso": 3200, "temp": 24.0, "time": None, "model": "Canon EOS 6D"}
        frames = [("/gibt/es/nicht_%d.CR2" % i, dict(meta)) for i in range(3)]   # wird vor dem Laden abgelehnt
        with self.assertRaisesRegex(ValueError, "Bias"):
            calib.build_master(self.lib, "dark", frames, log=lambda *a: None)
        self.assertEqual(len(self.lib.list()), 6)


@unittest.skipUnless(HAS_NUMPY and HAS_RAWPY, "braucht numpy und rawpy (venv)")
class TestMasterUndStack(unittest.TestCase):
    """Echte RAW-Dateien vom Sensor-Simulator: Master bauen, dann Live-Stack mit Dark-Optimierung."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="6d-ls-test-")
        cls.sensor, cls.stars = sim.Sensor(), sim.make_stars()

    def shot(self, name, seconds, temp, cap, sky_seconds=0.0, iso=1600, cosmic=False):
        stars = [] if cap else sim.sky_stars(self.stars, sky_seconds)
        px = self.sensor.frame(seconds, iso, temp, sky=None if cap else (4, 9, 6.3), stars=stars, sigma=1.2)
        if cosmic:
            px[500 * sim.RAW_W + 700] = sim.RAW_WHITE                   # Treffer kosmischer Strahlung
        t = datetime.datetime(2026, 11, 18, 23, 0) + datetime.timedelta(seconds=sky_seconds)
        meta = {"exposure": seconds, "iso": iso, "temp": temp, "time": t.strftime("%Y:%m:%d %H:%M:%S")}
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(sim.dng_bytes(px, meta))
        return path

    def test_master_dark_und_live_stack(self):
        lib = calib.Library(os.path.join(self.dir, "Bibliothek"))
        darks = [self.shot("D%d.CR2" % i, 30, 18.0, True, cosmic=(i == 1)) for i in range(4)]
        bias = [self.shot("B%d.CR2" % i, 1 / 4000, 18.0, True) for i in range(4)]
        groups = calib.group_frames(darks)
        self.assertEqual([(g[0], g[1], len(g[2])) for g in groups], [(1600, 30.0, 4)])
        dark = calib.build_master(lib, "dark", groups[0][2], log=lambda *a: None)
        self.assertEqual((dark["iso"], dark["exposure"], dark["temp"], dark["n"]), (1600, 30.0, 18.0, 4))
        self.assertTrue(dark["name"].startswith("MASTER-DARK_ISO1600_30s_18C_n4_"))
        data, _ = imaging.read_fits(lib.path(dark["id"]))
        self.assertLess(data[500, 700], 3000)                        # Ausreißer per κσ-Clipping verworfen
        calib.build_master(lib, "bias", calib.group_frames(bias)[0][2], log=lambda *a: None)

        logs = []
        ls = livestack.LiveStack(lib, os.path.join(self.dir, "live"), {"satellites": False}, log=logs.append)
        for i in range(3):                                           # Lights 6 °C kälter → k ≈ 0,5
            ls.add(self.shot("L%d.CR2" % i, 30, 12.0, False, sky_seconds=i * 900))
        ls.finish()
        for _ in range(300):
            if ls.status()["done"]:
                break
            import time
            time.sleep(0.1)
        st = ls.status()
        self.assertEqual((st["frames"], st["rejected"], st["kind"]), (3, 0, "raw"), st["history"])
        self.assertTrue(st["calib"]["dark"].startswith("MASTER-DARK") and st["calib"]["bias"])
        ks = [h["k"] for h in st["history"]]
        self.assertTrue(all(abs(k - 0.5) < 0.06 for k in ks), ks)
        rot = sorted(h["rot"] for h in st["history"])                 # 15 min Erddrehung = 3,76°
        self.assertAlmostEqual(rot[0], -2 * 360 * 900 / 86164.1, delta=0.05)
        self.assertTrue(ls.png and ls.png.startswith(b"\x89PNG"))
        self.assertTrue(st["saved"] and os.path.exists(os.path.join(self.dir, "live", st["saved"][0])))


if __name__ == "__main__":
    unittest.main()
