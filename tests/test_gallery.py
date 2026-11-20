"""Tests der Bildverwaltung (studio/gallery.py) – mit Simulator-RAWs in einem leeren Fotoordner.

Ausführen:  .venv/bin/python -m unittest tests.test_gallery -v
"""
import csv
import datetime
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "studio"))
sys.path.insert(0, os.path.join(ROOT, "tests"))                   # xisf_helper
import astro  # noqa: E402
import calib  # noqa: E402
import gallery  # noqa: E402
import imaging  # noqa: E402

_spec = importlib.util.spec_from_file_location("sim6d_gal", os.path.join(ROOT, "sim", "sim6d.py"))
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)
HAS_NUMPY = imaging.np is not None
JPEG = (b"\xff\xd8\xff\xe0" + b"\0" * 64 + b"\xff\xd9")          # reicht für die Verwaltung (kein EXIF)
SESSION = "2026-11-18_Milchstrasse"


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class Ablage:
    """Fotoordner mit einer Sitzung (Lights RAW+JPG, Darks, Bias mit zwei ISOs), Einzelaufnahmen und Live-Stack."""

    def __init__(self):
        self.out = tempfile.mkdtemp(prefix="6d-gal-test-")
        self.sensor = sim.Sensor()
        self.base = os.path.join(self.out, "Astro", SESSION)

    def raw(self, path, iso=800, exposure=3.2, temp=30.0, fnumber=4.0, when=None):
        px = self.sensor.frame(min(exposure, 1.0), iso, temp)
        t = when or datetime.datetime(2026, 11, 18, 23, 30)
        meta = {"exposure": exposure, "iso": iso, "temp": temp, "time": t.strftime("%Y:%m:%d %H:%M:%S"),
                "fnumber": fnumber, "focal": 24.0, "lens": "Simulator 24mm"}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(sim.dng_bytes(px, meta))
        return path

    def frame(self, ftype, i, jpeg=False, **kw):
        folder = os.path.join(self.base, astro.FRAME_TYPES[ftype]["folder"])
        name = "%s_Milchstrasse_20261118-2330%02d_ISO%d_3s_f4_%04d" % (
            astro.FRAME_TYPES[ftype]["prefix"], i, kw.get("iso", 800), i)
        p = self.raw(os.path.join(folder, name + ".CR2"), **kw)
        if jpeg:
            with open(os.path.join(folder, name + ".JPG"), "wb") as f:
                f.write(JPEG)
        return p

    def single(self, n, minutes=0):
        when = datetime.datetime(2026, 11, 19, 0, 10 + minutes)
        p = self.raw(os.path.join(self.out, "IMG_%04d.CR2" % n), iso=1600, exposure=20.0, when=when)
        with open(os.path.join(self.out, "IMG_%04d.JPG" % n), "wb") as f:
            f.write(JPEG)
        ts = time.mktime(when.timetuple())
        for q in (p, p[:-4] + ".JPG"):
            os.utime(q, (ts, ts))
        return p

    def cleanup(self):
        shutil.rmtree(self.out, ignore_errors=True)


class TestVerwaltung(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        a = cls.a = Ablage()
        for i in range(1, 4):
            a.frame("light", i, jpeg=True)
        for i in range(1, 4):
            a.frame("dark", i)
        for i in range(1, 4):
            a.frame("bias", i, exposure=1 / 4000)
        a.frame("bias", 9, exposure=1 / 4000, iso=3200)               # andere ISO – darf nicht mit
        live = os.path.join(a.base, "livestack")
        os.makedirs(live)
        for ext in (".fits", ".png"):
            with open(os.path.join(live, "LIVESTACK_20261119-0100_3x3.2s_raw" + ext), "wb") as f:
                f.write(b"x")
        a.single(9901)
        a.single(9902, minutes=1)
        with open(os.path.join(a.out, "thumb_preview.jpg"), "wb") as f:   # Vorschau von gphoto2 – keine Aufnahme
            f.write(JPEG)

    @classmethod
    def tearDownClass(cls):
        cls.a.cleanup()

    def test_uebersicht_und_paare(self):
        idx = gallery.index(self.a.out)
        s = idx["sessions"][0]
        self.assertEqual((s["name"], s["night"], s["target"]), (SESSION, "2026-11-18", "Milchstrasse"))
        groups = {g["key"]: (g["count"], g["files"]) for g in s["groups"]}
        self.assertEqual(groups["lights"], (3, 6))                     # RAW+JPG = ein Bild
        self.assertEqual(groups["livestack"], (1, 2))                  # FITS+PNG = ein Bild
        self.assertEqual(idx["results"][0]["groups"][0]["count"], 1)   # „Ergebnisse“ über alle Sitzungen
        res = gallery.items(self.a.out, "r:", "all")
        self.assertEqual((res[0]["kind"], res[0]["session"]), ("fits", SESSION))
        self.assertEqual([(n["night"], n["groups"][0]["count"]) for n in idx["singles"]], [("2026-11-18", 2)])
        lights = gallery.items(self.a.out, "s:" + SESSION, "lights")
        self.assertEqual(len(lights), 3)
        self.assertEqual(lights[0]["kind"], "raw")
        self.assertTrue(lights[0]["preview"].endswith(".JPG"))       # Vorschau aus dem JPEG
        self.assertEqual((lights[0]["meta"]["iso"], lights[0]["meta"]["fnumber"]), (800, 4.0))
        stack = gallery.items(self.a.out, "s:" + SESSION, "livestack")[0]
        self.assertEqual((stack["kind"], stack["preview"][-4:]), ("fits", ".png"))

    def test_pfade_bleiben_im_fotoordner(self):
        for bad in ("../etc/passwd", "/etc/passwd", "Astro/../../x"):
            with self.assertRaises(ValueError):
                gallery.safe_path(self.a.out, bad)
        with self.assertRaises(ValueError):
            gallery.items(self.a.out, "s:../x", "lights")
        with self.assertRaises(ValueError):
            gallery.items(self.a.out, "s:" + SESSION, "../../")

    def test_programme_finden_und_oeffnen(self):
        apps = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, apps)
        for p in ("PixInsight/PixInsight.app", "Siril.app", "GraXpert-3.1.app", "Notizen.app"):
            os.makedirs(os.path.join(apps, p))
        found = gallery.find_tools(dirs=(apps,))
        self.assertEqual(sorted(found), ["graxpert", "pixinsight", "siril"])
        self.assertTrue(found["pixinsight"].endswith("PixInsight/PixInsight.app"))   # zwei Ebenen tief
        calls = []
        rel = gallery.items(self.a.out, "s:" + SESSION, "lights")[0]["id"]
        gallery.open_with(self.a.out, [rel], "siril", found=found, run=calls.append)
        gallery.open_with(self.a.out, [rel], reveal=True, run=calls.append)
        self.assertEqual(calls[0][:3], ["open", "-a", found["siril"]])
        self.assertTrue(calls[0][3].endswith(".CR2") and os.path.isabs(calls[0][3]))
        self.assertEqual(calls[1][:2], ["open", "-R"])
        with self.assertRaises(ValueError):
            gallery.open_with(self.a.out, [rel], "photoshop", found=found, run=calls.append)
        os.makedirs(os.path.join(apps, "Siril.app", "Contents", "MacOS"))
        open(os.path.join(apps, "Siril.app", "Contents", "MacOS", "siril-cli"), "w").close()
        self.assertTrue(gallery.siril_cli(found).endswith("Contents/MacOS/siril-cli"))

    @unittest.skipUnless(sys.platform == "darwin", "Vorschau über sips (macOS)")
    def test_vorschaubild_aus_raw(self):
        rel = gallery.items(self.a.out, "s:" + SESSION, "darks")[0]["preview"]
        data, ctype = gallery.thumbnail(self.a.out, rel, 200)
        self.assertEqual(ctype, "image/jpeg")
        self.assertTrue(data.startswith(b"\xff\xd8"))
        self.assertEqual(gallery.thumbnail(self.a.out, rel, 200)[0], data)       # zweites Mal aus dem Cache


class TestUebernehmenUndAussortieren(unittest.TestCase):
    def setUp(self):
        self.a = Ablage()
        self.addCleanup(self.a.cleanup)

    def test_einzelaufnahmen_als_lights_uebernehmen(self):
        self.a.frame("light", 1)                                       # Sitzung hat schon Light 0001
        p1, p2 = self.a.single(9901), self.a.single(9902, minutes=1)
        rels = [gallery.rel_of(self.a.out, p) for p in (p2, p1)]
        used = {}
        new = gallery.adopt(self.a.out, rels, "light", "Milchstraße", used=used)
        self.assertEqual(len(new), 4)                                  # je RAW und JPG
        names = sorted(os.path.basename(r) for r in new)
        self.assertEqual(names[0], "LIGHT_Milchstrasse_20261119-001000_ISO1600_20s_f4_0002.CR2")
        self.assertTrue(names[2].endswith("_0003.CR2") and names[3].endswith("_0003.JPG"))   # nach Zeit nummeriert
        self.assertFalse(os.path.exists(p1) or os.path.exists(p1[:-4] + ".JPG"))
        with open(os.path.join(self.a.base, "serie.csv"), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["kameradatei"] for r in rows], ["IMG_9901.CR2", "IMG_9901.JPG", "IMG_9902.CR2",
                                                            "IMG_9902.JPG"])
        self.assertEqual((rows[0]["typ"], rows[0]["iso"], rows[0]["blende"], rows[0]["zeit"]),
                         ("Light", "1600", "f/4", '20"'))
        self.assertEqual(used[(os.path.join(self.a.base, "lights"), "light")], 3)
        with self.assertRaises(ValueError):                             # nur Einzelaufnahmen
            gallery.adopt(self.a.out, [new[0]], "light", "x")

    def test_aussortieren_und_zuruecklegen(self):
        p = self.a.frame("light", 1, jpeg=True)
        rel = gallery.rel_of(self.a.out, p)
        self.assertEqual(gallery.cull(self.a.out, [rel]), 2)
        bad = os.path.join(self.a.base, "verworfen", "lights", os.path.basename(p))
        self.assertTrue(os.path.isfile(bad) and os.path.isfile(bad[:-4] + ".JPG"))
        rejected = gallery.items(self.a.out, "s:" + SESSION, "verworfen")
        self.assertEqual((len(rejected), rejected[0]["from"]), (1, "lights"))
        self.assertEqual(gallery.cull(self.a.out, [rejected[0]["id"]], restore=True), 2)
        self.assertTrue(os.path.isfile(p))
        with self.assertRaises(ValueError):
            gallery.cull(self.a.out, [gallery.rel_of(self.a.out, self.a.single(9901))])   # keine Sitzung


class TestWeitergeben(unittest.TestCase):
    def setUp(self):
        a = self.a = Ablage()
        self.addCleanup(a.cleanup)
        for i in range(1, 4):
            a.frame("light", i, jpeg=True)
            a.frame("dark", i)
            a.frame("bias", i, exposure=1 / 4000)
            a.frame("flat", i, exposure=0.02, iso=400)
            a.frame("darkflat", i, exposure=0.02, iso=400)
        a.frame("bias", 9, exposure=1 / 4000, iso=3200)
        a.frame("dark", 9, exposure=30.0)                              # andere Zeit
        self.lib = calib.Library(os.path.join(a.out, "Astro", calib.LIBRARY_FOLDER))

    def test_kalibrierbilder_passen_zu_den_lights(self):
        sets = gallery.frame_sets(self.a.out, SESSION, self.lib)
        self.assertEqual({t: (len(s["files"]), s["skipped"]) for t, s in sets.items()},
                         {"light": (3, 0), "dark": (3, 1), "bias": (3, 1), "flat": (3, 0), "darkflat": (3, 0)})

    def test_pixinsight_ordner_fuer_wbpp(self):
        res = gallery.export_pixinsight(self.a.out, SESSION, self.lib)
        dest = res["input"]                                            # pixinsight/Rohbilder – Ziel für „+ Directory“
        self.assertEqual(dest, os.path.join(res["folder"], "Rohbilder"))
        self.assertEqual(res["counts"], {"light": 3, "dark": 3, "bias": 3, "flat": 3, "darkflat": 3})
        self.assertEqual(res["skipped"], {"dark": 1, "bias": 1})
        expect = {"Light": "LIGHT", "Dark": "DARK", "Flat": "FLAT", "Bias": "BIAS"}
        names = {}
        for sub, typ in expect.items():
            files = sorted(os.listdir(os.path.join(dest, sub)))
            names[sub] = files
            for n in files:                                            # so liest WBPP den Typ aus dem Pfad
                self.assertEqual(gallery.wbpp_type(os.path.join(dest, sub, n)), typ, n)
        self.assertEqual(len(names["Dark"]), 6)                       # Darks + Dark-Flats
        self.assertIn("Milchstrasse_20261118-233001_ISO400_3s_f4_0001_DARK.CR2", names["Dark"])
        self.assertIsNone(gallery.wbpp_type("/x/darkflats/DARKFLAT_a.CR2"))   # darum umbenannt
        light = os.path.join(dest, "Light", names["Light"][0])
        orig = os.path.join(self.a.base, "lights", "LIGHT_" + names["Light"][0].replace("_LIGHT", ""))
        self.assertEqual(os.stat(light).st_ino, os.stat(orig).st_ino)        # Verknüpfung, keine Kopie
        with open(os.path.join(dest, "lights.txt")) as f:
            self.assertEqual(len(f.read().split()), 3)
        with open(os.path.join(dest, "bilder.csv"), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 15)
        self.assertEqual((rows[0]["typ"], rows[0]["iso"], rows[0]["blende"]), ("light", "800", "4.0"))
        with open(os.path.join(res["folder"], "LIESMICH.txt"), encoding="utf-8") as f:
            self.assertIn("1 mit anderer ISO/Zeit/Optik weggelassen", f.read())
        # noch einmal: Rohbilder/ neu, Ergebnisse und wbpp/ bleiben, Ordner früherer Versionen verschwinden
        top = res["folder"]
        keep = [os.path.join(top, "wbpp_3x3.2s.xisf"), os.path.join(top, "wbpp", "master", "masterDark_x.xisf")]
        os.makedirs(os.path.dirname(keep[1]))
        os.makedirs(os.path.join(top, "Light"))                        # Export einer früheren Version
        open(os.path.join(top, "Light", "alt_LIGHT.CR2"), "w").close()
        for p in keep:
            open(p, "w").close()
        open(os.path.join(dest, "fremd.txt"), "w").close()
        gallery.export_pixinsight(self.a.out, SESSION, self.lib)
        self.assertTrue(all(os.path.isfile(p) for p in keep))
        self.assertFalse(os.path.exists(os.path.join(top, "Light")))
        self.assertFalse(os.path.exists(os.path.join(dest, "fremd.txt")))
        self.assertEqual(len(os.listdir(os.path.join(dest, "Light"))), 3)
        shutil.rmtree(top)
        os.makedirs(top)                                                # fremder Ordner gleichen Namens
        with self.assertRaises(ValueError):
            gallery.export_pixinsight(self.a.out, SESSION, self.lib)
        os.rmdir(top)

    def test_siril_skript_mit_allen_rohbildern(self):
        res = gallery.prepare_siril(self.a.out, SESSION, self.lib)
        script = read(res["script"])
        self.assertIn("\nrequires 1.3.4\n", script)                    # wie OSC_Preprocessing.ssf aus Siril 1.4
        self.assertIn("stack r_pp_light rej 3 3 -norm=addscale -output_norm -rgb_equal -32b -out=result", script)
        steps = gallery.script_steps(script)
        self.assertEqual(steps, res["steps"])
        self.assertEqual(steps[:2], ["Bias einlesen", "Master-Bias stapeln"])
        self.assertEqual(steps[-4:], ["Lights kalibrieren und debayern", "Lights ausrichten (Homographie)",
                                      "Lights stapeln", "Ergebnis speichern"])
        for line in ("stack bias rej 3 3 -nonorm -out=../masters/bias_stacked",
                     "calibrate flat -dark=../masters/darkflat_stacked",      # Dark-Flats statt Bias für die Flats
                     "stack pp_flat rej 3 3 -norm=mul -out=../masters/pp_flat_stacked",
                     "calibrate light -dark=../masters/dark_stacked -cc=dark -flat=../masters/pp_flat_stacked "
                     "-cfa -equalize_cfa -debayer",
                     "register pp_light", "save ../ergebnis_$LIVETIME:%d$s"):
            self.assertIn(line, script)
        self.assertEqual(sorted(os.listdir(os.path.join(res["folder"], "biases"))),
                         sorted(n for n in os.listdir(os.path.join(self.a.base, "biases")) if "ISO800" in n))
        self.assertFalse(any(n.endswith(".JPG") for n in os.listdir(os.path.join(res["folder"], "lights"))))

    @unittest.skipUnless(HAS_NUMPY and imaging.rawpy is not None, "braucht numpy und rawpy (venv)")
    def test_siril_nimmt_masters_aus_der_bibliothek(self):
        for folder in ("darks", "biases", "flats", "darkflats"):        # nur Lights in der Sitzung
            shutil.rmtree(os.path.join(self.a.base, folder))
        other = os.path.join(self.a.out, "Astro", "2026-11-10_Darks", "darks")
        darks = [self.a.raw(os.path.join(other, "DARK_x_%04d.CR2" % i)) for i in range(1, 4)]
        dark = calib.build_master(self.lib, "dark", calib.group_frames(darks)[0][2], log=lambda *a: None,
                                  source="2026-11-10_Darks")
        sets = gallery.frame_sets(self.a.out, SESSION, self.lib)
        self.assertEqual((len(sets["dark"]["files"]), sets["dark"]["from"]), (3, "2026-11-10_Darks"))   # für WBPP
        res = gallery.prepare_siril(self.a.out, SESSION, self.lib)
        script = read(res["script"])
        self.assertIn("stack dark rej 3 3", script)                    # Rohbilder aus der anderen Sitzung
        self.assertIn("calibrate light -dark=../masters/dark_stacked -cc=dark -cfa", script)   # Dark enthält den Offset
        self.assertNotIn("-flat=", script)
        self.assertEqual(res["used"]["dark"], "3 RAWs")
        os.remove(os.path.join(self.a.out, "Astro", "2026-11-10_Darks", "darks", "DARK_x_0001.CR2"))
        sets = gallery.frame_sets(self.a.out, SESSION, self.lib)        # Rohbilder fehlen → der Master selbst
        self.assertEqual((sets["dark"]["files"], sets["dark"]["master"]["name"]), ([], dark["name"]))
        res = gallery.prepare_siril(self.a.out, SESSION, self.lib)
        self.assertIn("-dark=%s -cc=dark" % self.lib.path(dark["id"]), read(res["script"]))
        self.lib.delete(dark["id"])                                     # gar kein Dark, kein Bias
        res = gallery.prepare_siril(self.a.out, SESSION, self.lib)
        self.assertIn('calibrate light -bias="=2047" -cfa', read(res["script"]))      # Schwarzwert als Offset

    @unittest.skipUnless(HAS_NUMPY, "braucht numpy (venv)")
    def test_vorschau_eines_fits(self):
        np = imaging.np
        p = os.path.join(self.a.out, "Astro", calib.LIBRARY_FOLDER, "flats", "MASTER-FLAT_x.fits")
        os.makedirs(os.path.dirname(p))
        y, x = np.mgrid[0:200, 0:300]
        imaging.write_fits(p, (5000 - 10 * np.hypot(x - 150, y - 100)).astype(np.float32), [])
        data, ctype = gallery.thumbnail(self.a.out, gallery.rel_of(self.a.out, p), 120)
        self.assertEqual(ctype, "image/png")
        self.assertTrue(data.startswith(b"\x89PNG"))


class TestNachbearbeitung(unittest.TestCase):
    def setUp(self):
        self.a = Ablage()
        self.addCleanup(self.a.cleanup)

    def test_aufraeumen_nach_siril(self):
        folder = os.path.join(self.a.base, "siril")
        os.makedirs(os.path.join(folder, "process"))
        with open(os.path.join(folder, "process", "pp_light_00001.fit"), "wb") as f:
            f.write(b"x" * 1234)
        self.assertEqual(gallery.cleanup_siril(folder), 0)              # ohne Marke: fremder Ordner, nichts tun
        open(os.path.join(folder, gallery.EXPORT_MARK), "w").close()
        self.assertEqual(gallery.cleanup_siril(folder), 1234)
        self.assertFalse(os.path.exists(os.path.join(folder, "process")))

    @unittest.skipUnless(HAS_NUMPY, "braucht numpy (venv)")
    def test_fertig_entwickeln_vorbereiten(self):
        np = imaging.np
        live = os.path.join(self.a.base, "livestack")
        os.makedirs(live)
        rgb = np.random.default_rng(2).uniform(0, 900, (12, 16, 3)).astype(np.float32)
        p = os.path.join(live, "LIVESTACK_x.fits")
        imaging.write_fits(p, rgb, [])
        res = gallery.prepare_finish(self.a.out, gallery.rel_of(self.a.out, p))
        script = read(res["script"])
        for line in ("load LIVESTACK_x.fits", "subsky 1 -samples=20 -tolerance=1.0 -smooth=0.5", "rmgreen",
                     "autostretch", "savetif LIVESTACK_x_fertig", "savejpg LIVESTACK_x_fertig 92"):
            self.assertIn(line, script)
        self.assertEqual(res["steps"][0], "Bild laden")
        self.assertEqual([os.path.basename(o) for o in res["outputs"]], ["LIVESTACK_x_fertig.jpg",
                                                                          "LIVESTACK_x_fertig.tif"])
        cfa = os.path.join(live, "einkanal.fits")                     # ohne Farbe: nicht fertig zu entwickeln
        imaging.write_fits(cfa, rgb[..., 0], [])
        with self.assertRaises(ValueError):
            gallery.prepare_finish(self.a.out, gallery.rel_of(self.a.out, cfa))

    @unittest.skipUnless(HAS_NUMPY and imaging.rawpy is not None, "braucht numpy und rawpy (venv)")
    def test_lights_pruefen_findet_anderes_feld_und_wolken(self):
        field_a, field_b = sim.make_stars(seed=6), sim.make_stars(seed=17)

        def light(i, stars):
            px = self.a.sensor.frame(30, 1600, 15.0, sky=(4, 9, 6.3), stars=stars, sigma=1.2)
            meta = {"exposure": 30, "iso": 1600, "temp": 15.0, "time": "2026:11:18 23:%02d:00" % i, "fnumber": 2.8,
                    "focal": 24.0, "lens": "Simulator 24mm"}
            folder = os.path.join(self.a.base, "lights")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "LIGHT_x_20261118-23%02d00_ISO1600_30s_f2.8_%04d.CR2" % (i, i)), "wb") as f:
                f.write(sim.dng_bytes(px, meta))
        for i in range(1, 5):
            light(i, sim.sky_stars(field_a, i * 60.0))
        light(5, [s for s in sim.sky_stars(field_a, 300.0) if s[2] < 2.5])   # Wolken: nur die hellsten Sterne
        for i in (6, 7):
            light(i, field_b)                                          # neu ausgerichtet
        seen = []
        res = gallery.check_lights(self.a.out, SESSION, lambda i, n, name: seen.append(i))
        self.assertEqual(seen, list(range(1, 8)))
        self.assertEqual((res["n"], res["fields"], res["other_field"]), (7, 2, 2))
        self.assertTrue(res["first_other"].endswith("_0006.CR2"))
        why = {f["name"][-8:-4]: f["why"] for f in res["frames"]}
        self.assertTrue(why["0005"].startswith("wenige Sterne"), why)
        self.assertEqual([why[k] for k in ("0001", "0002", "0003", "0004")], [None] * 4)
        self.assertEqual(len(res["suggest"]), 3)
        sm = gallery.session_summary(self.a.out, SESSION, None)
        self.assertEqual((sm["check"]["main"], sm["check"]["stale"]), (5, False))
        gallery.cull(self.a.out, res["suggest"])
        self.assertTrue(gallery.session_summary(self.a.out, SESSION, None)["check"]["stale"])


# Zeilen wie aus dem echten Lauf von WBPP 3.1.2 (PixInsight 1.9.6) mit studio/pixinsight/6d-wbpp-ereignisse.js
DARKS = "Dark frames, 5496x3670, NoFilter, CFA, 1x1, 3.20s, frames = 20 (20 active)"
LIGHTS = "Light frames, 5496x3670, NoFilter, RGB, 1x1, 3.20s, frames = 8 (8 active)"
WBPP_LINES = """
6D>> pipeline 31
6D>> start 2/31 Integration | {d}
6D>> done 2/31 Integration | {d} | 2 | 20 integrated
6D>> start 11/31 Measurements
6D>> done 11/31 Measurements |  | 2 | 8 measured
6D>> start 24/31 Integration | {l}
6D>> done 24/31 Integration | {l} | 2 | 8 integrated
6D>> done 30/31 Astrometric solution | {l} | 3 | 2 failed
6D>> end
""".format(d=DARKS, l=LIGHTS)


def fake_pixinsight(root, linux=False):
    """Installation wie PixInsight 1.9.6 (macOS oder Linux), nur mit leeren Dateien → (Ordner der .app, Programm)."""
    if linux:
        exe = os.path.join(root, "PixInsight", "bin", "PixInsight")
        app = os.path.dirname(os.path.dirname(exe))
    else:
        app = os.path.join(root, "PixInsight", "PixInsight.app")
        exe = os.path.join(app, "Contents", "MacOS", "PixInsight")
    wbpp = os.path.join(root, "PixInsight", "src", "scripts", "BatchPreprocessing", "WBPP.js")
    for p in (exe, wbpp):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
    return app, exe


@unittest.skipUnless(HAS_NUMPY, "braucht numpy (venv)")
class TestPixInsight(unittest.TestCase):
    def setUp(self):
        self.a = Ablage()
        self.addCleanup(self.a.cleanup)
        self.np = imaging.np
        import xisf_helper
        self.write_xisf = xisf_helper.write_xisf
        self.rgb = self.np.random.default_rng(4).uniform(0, 1, (6, 8, 3)).astype(self.np.float32)

    def test_xisf_lesen(self):
        np, d = self.np, tempfile.mkdtemp(dir=self.a.out)
        kw = [("IMAGETYP", "'Master Light'"), ("EXPTIME", "3.200"), ("ISOSPEED", "800."), ("CCD-TEMP", "38.30"),
              ("DATE-OBS", "'2026-11-19T00:14:37.000'"), ("BAYERPAT", "'RGGB'")]
        p = self.write_xisf(os.path.join(d, "m.xisf"), self.rgb, kw)
        data, h = imaging.read_xisf(p)
        self.assertEqual(data.shape, (6, 8, 3))                         # zweites Bild (Maske) übergangen
        self.assertTrue(np.allclose(data, self.rgb * imaging.FULL_SCALE))
        self.assertEqual((h["IMAGETYP"], h["EXPTIME"], h["ISOSPEED"], h["BAYERPAT"]), ("Master Light", 3.2, 800.0,
                                                                                       "RGGB"))
        self.assertEqual(imaging.image_header(p)[1], 3)
        gray = (np.arange(48).reshape(6, 8) * 1000).astype(np.uint16)
        for comp in (None, "zlib", "zlib+sh"):                          # Kompression wie PixInsight sie anbietet
            q = self.write_xisf(os.path.join(d, "g.xisf"), gray, compression=comp, sample="UInt16")
            self.assertTrue(np.array_equal(imaging.read_image(q)[0], gray), comp)
        q = self.write_xisf(os.path.join(d, "n.xisf"), self.rgb, planar=False)   # pixelStorage="Normal"
        self.assertTrue(np.allclose(imaging.read_xisf(q)[0], self.rgb * imaging.FULL_SCALE))
        q = self.write_xisf(os.path.join(d, "z.xisf"), self.rgb, compression="lz4")
        with self.assertRaisesRegex(ValueError, "lz4"):
            imaging.read_xisf(q)
        with open(q, "wb") as f:
            f.write(b"SIMPLE  = T")
        with self.assertRaises(ValueError):
            imaging.read_xisf_header(q)

    def test_programm_und_kommandozeile(self):
        root = tempfile.mkdtemp(dir=self.a.out)
        app, exe = fake_pixinsight(root)
        self.assertEqual(gallery.pixinsight_cli({"pixinsight": app}), exe)
        self.assertIsNone(gallery.pixinsight_cli({}))
        self.assertEqual(gallery.pixinsight_root(exe), os.path.join(root, "PixInsight"))
        self.assertTrue(gallery.wbpp_script(exe).endswith("src/scripts/BatchPreprocessing/WBPP.js"))
        _, lexe = fake_pixinsight(tempfile.mkdtemp(dir=self.a.out), linux=True)    # /opt/PixInsight/bin/PixInsight
        self.assertTrue(gallery.wbpp_script(lexe))
        cmd = gallery.pi_command(exe, "/s/x.js", ["a=1", "/b c"])
        self.assertEqual(cmd, [exe, "-n", "--automation-mode", "--no-focus-stealing", "-r=/s/x.js,a=1,/b c",
                               "--force-exit"])
        with self.assertRaisesRegex(ValueError, "Komma"):              # jsArguments werden am Komma getrennt
            gallery.pi_command(exe, "/s/x.js", ["/Astro/2026-11-18_M31, M32/x.fits"])
        for p in (gallery.PI_EVENTS, gallery.PI_FINISH):
            self.assertTrue(os.path.isfile(p), p)
        with open(gallery.PI_FINISH, encoding="utf-8") as f:            # Schritte des Skripts = Schritte im Studio
            import re
            self.assertEqual(tuple(re.findall(r'sixdStep\( "([^"]+)" \)', f.read())), gallery.PI_FINISH_STEPS)

    def test_ereignisse_von_wbpp(self):
        ev = [gallery.pi_event(line) for line in WBPP_LINES.strip().splitlines()]
        self.assertEqual(ev[0], {"event": "pipeline", "count": 31})
        self.assertEqual((ev[1]["index"], ev[1]["count"], ev[1]["name"]), (2, 31, "Integration"))
        self.assertEqual(gallery.wbpp_label(ev[1]["name"], ev[1]["group"]), "Master-Dark erzeugen")
        self.assertEqual(gallery.wbpp_detail(ev[1]["group"]), "20 Darks · 3.2 s")
        self.assertEqual(gallery.wbpp_detail("Bias frames, 5496x3670, 1x1, 0.00s, frames = 30 (30 active)"), "30 Bias")
        self.assertEqual((ev[4]["group"], ev[4]["status"], ev[4]["message"], ev[4]["failed"]),
                         ("", 2, "8 measured", False))
        self.assertEqual(gallery.wbpp_label(ev[3]["name"]), "Bilder bewerten")
        self.assertEqual(gallery.wbpp_label("Integration", ev[5]["group"]), "Lights stapeln")
        self.assertEqual(gallery.wbpp_label("Calibration", "Light frames, …"), "Lights kalibrieren")
        self.assertEqual(gallery.wbpp_label("Neuer Schritt"), "Neuer Schritt")
        self.assertTrue(ev[7]["failed"])
        self.assertEqual(ev[8], {"event": "end", "text": ""})
        self.assertEqual(gallery.pi_event("6D>> step Strecken"), {"event": "step", "text": "Strecken"})
        for junk in ("", "PixInsight Core 1.9.6", ":LOGFILE:/x.log:LOGFILE:", "6D>> start kaputt"):
            self.assertIsNone(gallery.pi_event(junk), junk)

    def test_wbpp_vorbereiten_und_ergebnis_einsammeln(self):
        for i in range(1, 4):
            self.a.frame("light", i)
            self.a.frame("dark", i)
        _, exe = fake_pixinsight(tempfile.mkdtemp(dir=self.a.out))
        res = gallery.prepare_wbpp(self.a.out, SESSION, None, exe)
        dest = os.path.join(self.a.base, "pixinsight")
        self.assertEqual((res["folder"], res["out"], res["lights"], res["calib"]),
                         (dest, os.path.join(dest, "wbpp"), 3, 3))
        run = res["command"][4]
        self.assertEqual(res["command"][:4], [exe, "-n", "--automation-mode", "--no-focus-stealing"])
        self.assertEqual(res["command"][-1], "--force-exit")
        args = run[3:].split(",")
        self.assertTrue(args[0].endswith("BatchPreprocessing/WBPP.js"))
        for a in ("automationMode=true", "dir=" + os.path.join(dest, "Rohbilder"), "outputDirectory=" +
                  os.path.join(dest, "wbpp"), "usePipelineScript=true", "pipelineScriptFile=" + gallery.PI_EVENTS,
                  "platesolve=false"):
            self.assertIn(a, args)
        # was WBPP hinterlässt (Namen wie im echten Lauf)
        since = time.time()
        wbpp = res["out"]
        stem = "masterLight_BIN-1_5496x3670_EXPOSURE-3.20s_FILTER-NoFilter_RGB"
        master = os.path.join(wbpp, "master")
        os.makedirs(master)
        self.write_xisf(os.path.join(master, stem + "_autocrop.xisf"), self.rgb, [("EXPTIME", "3.200")])
        for name, size in ((stem + ".xisf", 7000), ("LN_Reference_Light_x.xisf", 500),
                           ("masterDark_BIN-1_5496x3670_EXPOSURE-3.20s.xisf", 300)):
            with open(os.path.join(master, name), "wb") as f:
                f.write(b"x" * size)
        for sub in ("calibrated", "debayered", "registered"):
            os.makedirs(os.path.join(wbpp, sub, "Light_x"))
            with open(os.path.join(wbpp, sub, "Light_x", "a.xisf"), "wb") as f:
                f.write(b"x" * 1000)
        os.makedirs(os.path.join(wbpp, "logs"))
        open(os.path.join(wbpp, "logs", "20261120002453.log"), "w").close()
        path, freed = gallery.collect_wbpp(dest, since, lights=8)
        self.assertEqual(path, os.path.join(dest, "wbpp_8x3.2s.xisf"))
        self.assertEqual(freed, 7000 + 500 + 3000)
        self.assertEqual(sorted(os.listdir(wbpp)), ["logs", "master"])
        self.assertEqual(os.listdir(master), ["masterDark_BIN-1_5496x3670_EXPOSURE-3.20s.xisf"])
        # in der Bildverwaltung: Gruppe „PixInsight-Ergebnisse“ (nur die oberste Ebene), Aufnahmedaten, Vorschau
        idx = gallery.index(self.a.out)
        groups = {g["key"]: g["count"] for g in idx["sessions"][0]["groups"]}
        self.assertEqual(groups["pixinsight"], 1)
        it = gallery.items(self.a.out, "s:" + SESSION, "pixinsight")[0]
        self.assertEqual((it["kind"], it["meta"]["n"], it["meta"]["exposure"]), ("xisf", 8, 3.2))
        self.assertIn("wbpp_8x3.2s", [i["name"] for i in gallery.items(self.a.out, "r:", "all")])
        data, ctype = gallery.thumbnail(self.a.out, it["id"], 120)
        self.assertEqual(ctype, "image/png")
        # beim nächsten Lauf ohne Master-Light: Grund aus dem Protokoll
        with open(os.path.join(wbpp, "logs", "20261120010000.log"), "w") as f:
            f.write("[2026-11-20 01:00:00] *** Error: <raw>No light frames</raw> found\n")
        with self.assertRaisesRegex(ValueError, "No light frames found"):
            gallery.collect_wbpp(dest, time.time())

    def test_fertig_entwickeln_mit_pixinsight_und_siril(self):
        _, exe = fake_pixinsight(tempfile.mkdtemp(dir=self.a.out))
        folder = os.path.join(os.path.realpath(self.a.base), "pixinsight")       # /var → /private/var (macOS)
        os.makedirs(folder)
        p = self.write_xisf(os.path.join(folder, "wbpp_8x3.2s.xisf"), self.rgb, [("EXPTIME", "3.200")])
        rel = gallery.rel_of(self.a.out, p)
        res = gallery.prepare_pi_finish(self.a.out, rel, exe)
        target = os.path.join(folder, "wbpp_8x3.2s_fertig-pi")
        self.assertEqual(res["command"][4], "-r=%s,%s,%s" % (gallery.PI_FINISH, p, target))
        self.assertEqual(res["outputs"], [target + ".jpg", target + ".tif"])
        self.assertEqual(res["steps"], list(gallery.PI_FINISH_STEPS))
        self.assertIn("load wbpp_8x3.2s.xisf", read(gallery.prepare_finish(self.a.out, rel)["script"]))  # Siril 1.4
        gray = self.write_xisf(os.path.join(folder, "grau.xisf"), self.rgb[..., 0])
        with self.assertRaisesRegex(ValueError, "farbig"):
            gallery.prepare_pi_finish(self.a.out, gallery.rel_of(self.a.out, gray), exe)
        # fertig: in der Übersicht der Sitzung beim Stapel, von welchem Programm
        os.remove(gray)
        old = time.time() - 60
        os.utime(p, (old, old))
        for ext in (".jpg", ".tif"):
            with open(target + ext, "wb") as f:
                f.write(JPEG)
        sm = gallery.session_summary(self.a.out, SESSION, None)
        r = next(x for x in sm["results"] if x["name"] == "wbpp_8x3.2s")
        self.assertEqual((r["finished_jpg"], r["finished_by"]), (gallery.rel_of(self.a.out, target + ".jpg"),
                                                                ["pixinsight"]))
        done = next(i for i in gallery.items(self.a.out, "s:" + SESSION, "pixinsight") if i["finished"])
        self.assertEqual((done["name"], done["finished_by"]), ("wbpp_8x3.2s_fertig-pi", "pixinsight"))


if __name__ == "__main__":
    unittest.main()
