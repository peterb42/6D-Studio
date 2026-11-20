"""Tests für 6D Rechner (rechner/6d-rechner.py) und seine Anbindung im Studio (studio/rechner.py).

Der Dienst läuft im selben Prozess auf einem freien Port, PixInsight ist ein Ersatz (tests/fake_pixinsight.py).
Ausführen:  .venv/bin/python -m unittest tests.test_rechner -v
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "studio"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import fake_pixinsight  # noqa: E402
import imaging  # noqa: E402
import rechner  # noqa: E402

_spec = importlib.util.spec_from_file_location("rechner_dienst", os.path.join(ROOT, "rechner", "6d-rechner.py"))
dienst = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dienst)
HAS_NUMPY = imaging.np is not None
dienst.log = lambda text: None                                     # Meldungen des Dienstes nicht in die Testausgabe


def write(path, size, age=0.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(os.urandom(size))
    if age:
        t = time.time() - age
        os.utime(path, (t, t))
    return path


class TestRechner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="6d-rechner-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.exe = fake_pixinsight.make(os.path.join(self.tmp, "app"))
        self.worker = dienst.Worker(os.path.join(self.tmp, "daten"), self.exe, name="test-pc", prefix=[])
        self.httpd = dienst.serve(self.worker, "geheim", "127.0.0.1", 0)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.client = rechner.Client(self.url, "geheim")

    def lights(self, n, size=1500):
        src = os.path.join(self.tmp, "export")
        return {"Rohbilder/Light/x_%04d_LIGHT.CR2" % i: write(os.path.join(src, "x_%04d_LIGHT.CR2" % i), size + i)
                for i in range(1, n + 1)}

    def test_einstellungen(self):
        p = os.path.join(self.tmp, "cfg", "rechner.json")
        self.assertEqual(rechner.load_config(p), rechner.DEFAULTS)
        rechner.save_config({"url": "http://pc-4090:8090", "token": "t", "live": True}, p)
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)          # der Schlüssel steht darin
        self.assertEqual(rechner.load_config(p), {"url": "http://pc-4090:8090", "token": "t", "live": True})
        self.assertEqual(rechner.normalize_url("100.101.102.103:8090/"), "http://100.101.102.103:8090")
        self.assertEqual(rechner.normalize_url(" "), "")
        for bad in ("http://a b:8090", "http://pc/pfad"):
            with self.assertRaises(ValueError):
                rechner.normalize_url(bad)
        self.assertEqual(rechner.remote_session("Kamera", "2026-11-18_M31"), "Kamera_2026-11-18_M31")
        tok = os.path.join(self.tmp, "daten2")
        os.makedirs(tok)
        first = dienst.load_token(tok)
        self.assertGreater(len(first), 20)
        self.assertEqual(dienst.load_token(tok), first)                   # bleibt beim nächsten Start
        self.assertEqual(os.stat(os.path.join(tok, "token")).st_mode & 0o777, 0o600)

    def test_schluessel_und_pfade(self):
        with self.assertRaisesRegex(rechner.RechnerError, "Schlüssel"):
            rechner.Client(self.url, "falsch").status()
        st = self.client.status()
        self.assertEqual((st["name"], st["pixinsight"], st["wbpp"], st["job"]), ("test-pc", True, True, None))
        f = write(os.path.join(self.tmp, "a.CR2"), 10)
        for bad in ("../x.CR2", "a/../../b.CR2", ".versteckt", "a//b.CR2"):
            with self.assertRaises(rechner.RechnerError, msg=bad):
                self.client.put("Kamera_S", bad, f)
        with self.assertRaisesRegex(rechner.RechnerError, "Ungültige Sitzung"):
            self.client.files("../etc")
        self.assertEqual(self.client.files("Kamera_neu"), {})             # noch nichts hochgeladen
        with self.assertRaisesRegex(rechner.RechnerError, "nicht erreichbar"):
            rechner.Client("http://127.0.0.1:9", "x").status(timeout=1)
        self.assertEqual(os.listdir(self.worker.sessions), [])            # nichts angelegt

    def test_abgleich_hoch_und_runter(self):
        wanted = self.lights(3)
        wanted["Rohbilder/Dark/y_0001_DARK.CR2"] = write(os.path.join(self.tmp, "export", "y_DARK.CR2"), 700)
        seen = []
        st = rechner.sync(self.client, "Kamera_S", wanted, delete_prefix="Rohbilder/",
                          progress=lambda *a: seen.append(a))
        total = sum(os.path.getsize(p) for p in wanted.values())
        self.assertEqual((st["uploaded"], st["bytes"], st["deleted"]), (4, total, 0))
        self.assertEqual(seen[-1], (4, 4, total, total))
        remote = self.client.files("Kamera_S")
        self.assertEqual({r: v["size"] for r, v in remote.items()}, {r: os.path.getsize(p) for r, p in wanted.items()})
        p = wanted["Rohbilder/Light/x_0001_LIGHT.CR2"]
        self.assertAlmostEqual(remote["Rohbilder/Light/x_0001_LIGHT.CR2"]["mtime"], os.path.getmtime(p), places=2)
        self.assertEqual(rechner.sync(self.client, "Kamera_S", wanted)["uploaded"], 0)   # schon alles oben
        # geändert, aussortiert, Ergebnis daneben: nur unter Rohbilder/ wird gelöscht
        write(p, 3000)
        self.client.put("Kamera_S", "wbpp_3x2s.xisf", write(os.path.join(self.tmp, "w.xisf"), 50))
        del wanted["Rohbilder/Dark/y_0001_DARK.CR2"]
        st = rechner.sync(self.client, "Kamera_S", wanted, delete_prefix="Rohbilder/")
        self.assertEqual((st["uploaded"], st["deleted"], st["kept"]), (1, 1, 2))
        self.assertEqual(sorted(self.client.files("Kamera_S")),
                         sorted(list(wanted) + ["wbpp_3x2s.xisf"]))
        dest = self.client.get("Kamera_S", "Rohbilder/Light/x_0001_LIGHT.CR2", os.path.join(self.tmp, "zurueck.CR2"))
        with open(dest, "rb") as a, open(p, "rb") as b:
            self.assertEqual(a.read(), b.read())
        self.assertFalse(os.path.exists(dest + ".part"))
        self.client.delete("Kamera_S", "wbpp_3x2s.xisf")
        self.assertNotIn("wbpp_3x2s.xisf", self.client.files("Kamera_S"))

    def test_vorab_hochladen_waehrend_der_serie(self):
        out = os.path.join(self.tmp, "fotos")
        base = os.path.join(out, "Astro", "2026-11-20_M31")
        old = write(os.path.join(base, "lights", "LIGHT_M31_20261120-2300_ISO800_60s_f4_0001.CR2"), 100, age=60)
        write(os.path.join(base, "lights", "LIGHT_M31_20261120-2301_ISO800_60s_f4_0002.CR2"), 100)  # lädt noch
        write(os.path.join(base, "lights", "LIGHT_M31_20261120-2300_ISO800_60s_f4_0001.JPG"), 100, age=60)
        write(os.path.join(base, "darkflats", "DARKFLAT_M31_x_0001.CR2"), 100, age=60)
        wanted = rechner.live_files(out, "2026-11-20_M31", None)          # wie der Export (frame_sets)
        self.assertEqual(sorted(wanted), ["Rohbilder/Dark/M31_x_0001_DARK.CR2",
                                          "Rohbilder/Light/M31_20261120-2300_ISO800_60s_f4_0001_LIGHT.CR2"])
        self.assertEqual(wanted["Rohbilder/Light/M31_20261120-2300_ISO800_60s_f4_0001_LIGHT.CR2"], old)
        self.assertEqual(rechner.recent_sessions(out), ["2026-11-20_M31"])
        self.assertEqual(rechner.recent_sessions(out, now=time.time() + 13 * 3600), [])
        self.assertEqual(rechner.frame_label("Rohbilder/Dark/a.CR2"), "Darks")

    @unittest.skipUnless(HAS_NUMPY, "braucht numpy (venv) – der Ersatz für PixInsight schreibt XISF")
    def test_wbpp_und_fertig_entwickeln(self):
        with self.assertRaisesRegex(rechner.RechnerError, "Sitzung nicht vorhanden"):
            self.client.start("wbpp", "Kamera_S")
        self.client.put("Kamera_S", "Rohbilder/Dark/d_DARK.CR2", write(os.path.join(self.tmp, "d.CR2"), 10))
        with self.assertRaisesRegex(rechner.RechnerError, "Keine Lights"):
            self.client.start("wbpp", "Kamera_S")
        rechner.sync(self.client, "Kamera_S", self.lights(3))
        seen = []
        job = self.client.wait(self.client.start("wbpp", "Kamera_S"), seen.append, poll=0.05)
        self.assertIsNone(job["error"], job)
        self.assertEqual((job["result"], job["freed"], job["warnings"]), ("wbpp_3x2s.xisf", 5000,
                                                                          ["Astrometrie: 1 failed"]))
        self.assertEqual(len(job["steps"]), 4)
        self.assertTrue(seen)
        remote = self.client.files("Kamera_S")
        self.assertIn("wbpp_3x2s.xisf", remote)
        self.assertFalse(any(r.startswith("wbpp/calibrated") for r in remote))     # Zwischenbilder gelöscht
        job = self.client.wait(self.client.start("finish", "Kamera_S", "wbpp_3x2s.xisf"), lambda j: None, poll=0.05)
        self.assertEqual(job["results"], ["wbpp_3x2s_fertig-pi.jpg", "wbpp_3x2s_fertig-pi.tif"])
        self.assertEqual(self.client.status()["job"], None)
        self.client.put("Kamera_S", "kaputt.xisf", write(os.path.join(self.tmp, "k.xisf"), 10))
        job = self.client.wait(self.client.start("finish", "Kamera_S", "kaputt.xisf"), lambda j: None, poll=0.05)
        self.assertEqual(job["error"], "PixInsight: Bild kaputt")
        with self.assertRaisesRegex(rechner.RechnerError, "Nicht vorhanden"):
            self.client.start("finish", "Kamera_S", "gibt-es-nicht.xisf")

    def test_ein_auftrag_zur_zeit(self):
        def slow(command, handle):
            time.sleep(0.6)
        self.worker.run = slow
        rechner.sync(self.client, "Kamera_S", self.lights(1))
        first = self.client.start("wbpp", "Kamera_S")
        self.assertTrue(self.client.status()["job"]["active"])
        with self.assertRaisesRegex(rechner.RechnerError, "Es läuft schon"):
            self.client.start("wbpp", "Kamera_S")
        job = self.client.wait(first, lambda j: None, poll=0.05)
        self.assertIn("kein Master-Light", job["error"])                 # der langsame Ersatz schreibt nichts


if __name__ == "__main__":
    unittest.main()
