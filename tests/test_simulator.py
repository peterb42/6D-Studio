"""Integrationstests: 6D Studio gegen die simulierte Kamera (sim/sim6d.py) – ohne echte 6D.

Prüft den kompletten Weg: Anmeldung, Ereignisse, Einstellungen, Livebild, Lupe, Fokus, Bulb mit
Download, Zugang für gphoto2 (Session-Vortäuschung) und automatisches Wiederverbinden.
Dauer ~15 s.  Ausführen:  python3 -m unittest tests.test_simulator -v
"""
import importlib.util
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["OUT"] = tempfile.mkdtemp(prefix="6d-sim-test-")


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, [rel]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    return mod


studio = load("studio_mit_sim", "studio/6d-studio.py")
sim = load("sim6d", "sim/sim6d.py")


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def recv_packet(s):
    hdr = b""
    while len(hdr) < 8:
        hdr += s.recv(8 - len(hdr))
    length = struct.unpack_from("<I", hdr)[0]
    rest = b""
    while len(rest) < length - 8:
        rest += s.recv(length - 8 - len(rest))
    return hdr + rest


@unittest.skipUnless(sys.platform == "darwin", "braucht sips (macOS)")
class TestStudioMitSimulator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        args = sim.parse_args(["--port", "0", "--init-delay", "0.3", "--restart-delay", "0.5",
                               "--mbps", "0", "--quiet"])
        cls.cam = sim.Camera(args)
        cls.server = sim.Server(cls.cam, args)
        cls.server.start()
        studio.CAM_IP, studio.CAM_PORT, studio.GPHOTO_PORT = "127.0.0.1", cls.server.port, 0
        cls.st = studio.Studio()
        cls.logs = []
        cls.st.log = lambda *a: cls.logs.append(" ".join(str(x) for x in a))
        threading.Thread(target=cls.st.camera_thread, args=(b"\x11" * 16, "test"), daemon=True).start()
        threading.Thread(target=cls.st.download_worker, daemon=True).start()
        if not wait_for(lambda: cls.st.connected and studio.P_ISO in cls.st.props, 15):
            raise RuntimeError("Studio verbindet sich nicht mit dem Simulator:\n" + "\n".join(cls.logs))

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_1_anmeldung_und_werte(self):
        st = self.st
        self.assertEqual(st.model, "Canon EOS 6D")
        self.assertIn(studio.RELEASE_ON, st.supported)
        self.assertNotIn(studio.BULB_START, st.supported)
        self.assertEqual(studio.fmt_iso(st.props[studio.P_ISO]), "3200")
        self.assertGreater(len(st.avail[studio.P_ISO]), 20)
        self.assertTrue(st.is_bulb_mode())
        # Speicherort „nur Computer“ (4) wird auf die Karte umgestellt
        self.assertTrue(wait_for(lambda: self.cam.props[sim.P_DEST] == 1, 5))

    def test_2_einstellungen(self):
        self.st.act_set({"prop": "iso", "value": 0x58})
        self.assertEqual(self.cam.props[sim.P_ISO], 0x58)
        self.assertTrue(wait_for(lambda: self.st.props[studio.P_ISO] == 0x58, 3))
        with self.assertRaises(studio.CameraError):
            self.st.act_set({"prop": "iso", "value": 0x47})           # gibt es nicht
        with self.assertRaises(studio.CameraError):
            self.st.act_set({"prop": "shutter", "value": 0x70})       # im Modus B nicht möglich

    def test_3_livebild_lupe_fokus(self):
        st, cam = self.st, self.cam
        st.act_liveview({"on": True})
        self.assertTrue(wait_for(lambda: st.frame_seq > 2, 5))
        self.assertTrue(st.frame.startswith(b"\xff\xd8"))
        st.act_zoom({"level": 10, "x": 0.53, "y": 0.47})
        self.assertEqual(cam.zoom, 10)
        self.assertEqual(cam.zoom_pos, st.zoom_xy(10))
        before = cam.zoom_pos
        st.act_zoommove({"dx": 1, "dy": 0})
        self.assertGreater(cam.zoom_pos[0], before[0])
        focus = cam.focus
        st.act_focus({"step": -2})
        self.assertEqual(cam.focus, focus - 8)
        st.act_zoom({"level": 1})
        st.act_liveview({"on": False})
        self.assertEqual(cam.props[sim.P_EVF_OUT], 0)

    def test_4_bulb_mit_download(self):
        st = self.st
        n = len(st.photos)
        st.act_bulb_start({"seconds": 0})
        self.assertIsNotNone(st.bulb)
        with self.assertRaises(studio.CameraError):
            st.act_bulb_start({})                                     # läuft schon
        time.sleep(0.5)
        st.act_bulb_stop({})
        self.assertIsNone(st.bulb)
        self.assertTrue(wait_for(lambda: len(st.photos) > n, 20), "\n".join(self.logs[-10:]))
        photo = st.photos[0]
        self.assertTrue(os.path.isfile(os.path.join(studio.OUT, photo["name"])))
        self.assertTrue(photo["preview"] and os.path.isfile(os.path.join(studio.PREVIEW_DIR, photo["preview"])))

    def test_5_bulb_auto_stopp(self):
        st = self.st
        n = len(st.photos)
        st.act_bulb_start({"seconds": 1})
        self.assertTrue(wait_for(lambda: st.bulb is None, 4))
        self.assertTrue(wait_for(lambda: len(st.photos) > n, 20))

    def test_6_gphoto2_zugang(self):
        """gphoto2 öffnet/schließt eine Session – das darf die Kamera nie erreichen."""
        c = socket.create_connection(("127.0.0.1", self.st.gphoto_port), timeout=10)
        body = b"\x22" * 16 + "gphoto\0".encode("utf-16-le") + struct.pack("<HH", 0, 1)
        c.sendall(struct.pack("<II", 8 + len(body), 1) + body)
        ack = recv_packet(c)
        self.assertEqual(struct.unpack_from("<I", ack, 4)[0], studio.INIT_CMD_ACK)
        e = socket.create_connection(("127.0.0.1", self.st.gphoto_port), timeout=10)
        e.sendall(struct.pack("<III", 12, 3, struct.unpack_from("<I", ack, 8)[0]))
        self.assertEqual(struct.unpack_from("<I", recv_packet(e), 4)[0], studio.INIT_EVT_ACK)

        def op(code, tid, *params):
            b = struct.pack("<IHI", 1, code, tid) + b"".join(struct.pack("<I", p) for p in params)
            c.sendall(struct.pack("<II", 8 + len(b), 6) + b)
            while True:
                p = recv_packet(c)
                if struct.unpack_from("<I", p, 4)[0] == 7:
                    return struct.unpack_from("<HI", p, 8)

        self.assertEqual(op(0x1002, 0, 1), (studio.RC_OK, 0))     # OpenSession – vom Studio beantwortet
        self.assertEqual(op(0x1001, 1), (studio.RC_OK, 1))        # GetDeviceInfo – Transaktion zurückgerechnet
        self.assertEqual(op(0x1003, 2), (studio.RC_OK, 2))        # CloseSession – vom Studio beantwortet
        c.close()
        e.close()
        self.assertEqual(self.cam.session, "open")
        self.st.act_set({"prop": "aperture", "value": 0x38})     # Session funktioniert weiter
        self.assertEqual(self.cam.props[sim.P_APERTURE], 0x38)

    def test_7_wiederverbinden(self):
        st = self.st
        self.server.drop()                                         # WLAN-Abbruch
        self.assertTrue(wait_for(lambda: not st.connected, 5))
        self.assertTrue(wait_for(lambda: st.connected, 15), "\n".join(self.logs[-10:]))
        self.assertTrue(wait_for(lambda: studio.P_AEMODE in st.props, 5))

    # ---------- Serien für Stacking
    def run_series(self, timeout=60, **body):
        self.st.act_series_start(body)
        self.assertTrue(wait_for(lambda: not self.st.series_active(), timeout), "\n".join(self.logs[-12:]))
        self.assertTrue(wait_for(lambda: self.st.downloads.empty() and not self.st.downloading, 30))
        time.sleep(0.3)
        return self.st.series

    def csv_rows(self, s):
        import csv
        with open(os.path.join(os.path.dirname(s["folder"]), "serie.csv"), encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_8_serie_lights_mit_download(self):
        s = self.run_series(type="light", target="Milchstraße", count=3, exposure=1, pause=0.2, download=True)
        self.assertEqual((s["done"], s["error"]), (3, None))
        self.assertTrue(s["folder"].endswith("_Milchstrasse/lights"))
        files = sorted(os.listdir(s["folder"]))
        self.assertEqual(len(files), 3, files)
        for i, name in enumerate(files, s["first"]):
            self.assertRegex(name, r"^LIGHT_Milchstrasse_\d{8}-\d{6}_ISO\d+_1s_f[\d.]+_%04d\.JPG$" % i)
        rows = [r for r in self.csv_rows(s) if r["typ"] == "Light"]
        self.assertEqual([r["geladen"] for r in rows[-3:]], ["ja"] * 3)
        self.assertAlmostEqual(float(rows[-1]["belichtung_ist_s"]), 1.0, delta=0.5)

    def test_9_serie_bias_im_modus_m(self):
        st, cam = self.st, self.cam
        cam.set_mode("M")
        self.assertTrue(wait_for(lambda: not st.is_bulb_mode() and st.avail.get(studio.P_SHUTTER), 5))
        try:
            s = self.run_series(type="bias", target="Milchstraße", count=3, pause=0, download=False)
            self.assertEqual(s["done"], 3)
            self.assertEqual(cam.props[sim.P_SHUTTER], max(sim.SHUTTER_CODES))     # kürzeste Zeit gestellt
            self.assertEqual(os.listdir(s["folder"]), [])                           # bleibt auf der Karte
            rows = [r for r in self.csv_rows(s) if r["typ"] == "Bias"]
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(r["geladen"].startswith("nein") and r["kameradatei"].startswith("IMG_")
                                for r in rows))
            self.assertTrue(all("_1-4000s_" in r["datei"] for r in rows))
        finally:
            cam.set_mode("B")
            self.assertTrue(wait_for(lambda: st.is_bulb_mode(), 5))

    def test_10_serie_stopp_nach_diesem_bild(self):
        st = self.st
        st.act_series_start({"type": "dark", "target": "Test", "count": 5, "exposure": 2, "pause": 0,
                             "download": False})
        self.assertTrue(wait_for(lambda: st.series["phase"] == "belichtet", 5))
        with self.assertRaises(studio.CameraError):
            st.act_bulb_start({})                                   # Bulb-Knopf gesperrt während der Serie
        st.act_series_stop({})
        self.assertTrue(wait_for(lambda: not st.series_active(), 15))
        self.assertEqual(st.series["done"], 1)

    def test_11_serie_raw_und_jpeg(self):
        self.cam.args.raw = True
        try:
            s = self.run_series(type="light", target="Orion", count=1, exposure=1, pause=0, download=True)
        finally:
            self.cam.args.raw = False
        files = sorted(os.listdir(s["folder"]))
        self.assertEqual([os.path.splitext(f)[1] for f in files], [".CR2", ".JPG"])
        self.assertEqual(os.path.splitext(files[0])[0], os.path.splitext(files[1])[0])   # gleicher Name

    # ---------- Live-Stack und Bibliothek (laufen zuletzt: „9a“ > „9_“)
    def wait_master(self, timeout=60):
        self.assertTrue(wait_for(lambda: self.st.master_job and not self.st.master_job["active"], timeout),
                        "\n".join(self.logs[-12:]))
        job = self.st.master_job
        self.assertIsNone(job["error"], "\n".join(self.logs[-12:]))
        return job["results"]

    @unittest.skipUnless(studio.imaging.missing() is None, "braucht numpy und rawpy (venv)")
    def test_9a_darks_mit_master_dann_live_stack(self):
        st, cam = self.st, self.cam
        cam.args.raw, cam.args.sky_speed, cam.focus = True, 120.0, 0
        st.act_set({"prop": "iso", "value": 0x70})
        st.act_set({"prop": "aperture", "value": 0x10})             # f/1.4: genug Sterne in 1 s
        try:
            cam.cap, cam.ambient = True, 22.0                         # Darks: Deckel drauf, warm
            s = self.run_series(type="dark", target="Stack", count=3, exposure=1, pause=0, master=True)
            self.assertEqual((s["download"], s["master"]), ("raw", True))
            dark = self.wait_master()[0]
            self.assertEqual((dark["kind"], dark["iso"], dark["n"], dark["exposure"]), ("dark", 3200, 3, 1.0))
            self.assertAlmostEqual(dark["temp"], 22, delta=1)
            self.assertEqual(len([f for f in os.listdir(s["folder"]) if f.endswith(".CR2")]), 3)

            cam.cap, cam.ambient = False, 16.0                         # Lights: Deckel ab, kälter
            st.act_live_start({"target": "Stack"})
            with self.assertRaises(studio.CameraError):
                st.act_live_start({})                                   # läuft schon
            self.run_series(type="light", target="Stack", count=3, exposure=1, pause=0, download="raw")
            live = st.live
            self.assertTrue(wait_for(lambda: live.status()["frames"] + live.status()["rejected"] >= 3
                                     and not live.status()["busy"], 30))
            info = live.status()
            self.assertEqual((info["frames"], info["kind"]), (3, "raw"), info["history"])
            self.assertEqual(info["calib"]["dark"], dark["name"])      # automatisch gewählt
            self.assertTrue(all("k" in h for h in info["history"]))
            self.assertEqual(st.last_light["iso"], 3200)
            st.act_live_analyze({})                                     # „Bild prüfen“
            self.assertTrue(wait_for(lambda: live.status()["analysis"] is not None, 20))
            self.assertTrue(any(i["topic"] == "Dark" for i in live.status()["analysis"]["items"]))
            st.act_live_stop({})
            self.assertTrue(wait_for(lambda: live.status()["done"], 20))
            self.assertTrue(live.saved and os.path.isfile(live.saved[0]))
            self.assertTrue(os.path.isfile(live.saved[0][:-4] + ".fits"))
        finally:
            cam.args.raw, cam.args.sky_speed, cam.cap, cam.ambient = False, 1.0, False, 10.0

    @unittest.skipUnless(studio.imaging.missing() is None, "braucht numpy und rawpy (venv)")
    def test_9b_bias_master_und_master_aus_ordner(self):
        st, cam = self.st, self.cam
        cam.set_mode("M")
        self.assertTrue(wait_for(lambda: not st.is_bulb_mode() and st.avail.get(studio.P_SHUTTER), 5))
        cam.args.raw, cam.cap = True, True
        try:
            s = self.run_series(type="bias", target="Stack", count=3, pause=0, master=True)
            bias = self.wait_master()[0]
            self.assertEqual((bias["kind"], bias["iso"], bias["n"]), ("bias", 3200, 3))
            self.assertTrue(bias["name"].startswith("MASTER-BIAS_ISO3200_"))
            # Kamera steht jetzt auf 1/4000 – Master-Darks damit wären nur Bias
            with self.assertRaisesRegex(studio.CameraError, "Bias"):
                st.act_series_start({"type": "dark", "target": "Stack", "count": 3, "master": True})
            self.assertFalse(st.series["active"])
            # Master noch einmal aus dem Ordner der Darks (z. B. von der Karte kopiert)
            st.act_library_build({"session": os.path.basename(s["session"]), "kind": "dark"})
            dark = self.wait_master()[0]
            self.assertEqual((dark["kind"], dark["n"]), ("dark", 3))
            info = st.library_info()
            self.assertGreaterEqual(len(info["masters"]), 2)
            self.assertTrue(any(src["dark"] >= 3 for src in info["sources"]))
            st.act_library_delete({"id": dark["id"]})
            self.assertIsNone(st.library.get(dark["id"]))
        finally:
            cam.args.raw, cam.cap = False, False
            cam.set_mode("B")
            self.assertTrue(wait_for(lambda: st.is_bulb_mode(), 5))

    @unittest.skipUnless(studio.imaging.missing() is None, "braucht numpy und rawpy (venv)")
    def test_9c_flats_und_dark_flats_dann_live_stack_mit_flat(self):
        st, cam = self.st, self.cam
        cam.args.raw, cam.args.sky_speed, cam.focus = True, 60.0, 0
        st.act_set({"prop": "aperture", "value": 0x10})              # f/1.4 – Flats und Lights gleich
        st.act_set({"prop": "iso", "value": 0x58})                   # ISO 400 für die Flats
        try:
            # Flats: Moduswahlrad Av, Leuchtfläche vor dem Objektiv
            cam.set_mode("AV")
            self.assertTrue(wait_for(lambda: st.props.get(studio.P_AEMODE) == 2, 5))
            cam.panel = True
            s = self.run_series(type="flat", target="Flats", count=3, pause=0, master=True)
            flat = self.wait_master()[0]
            session = os.path.basename(s["session"])
            self.assertEqual((flat["kind"], flat["fnumber"], flat["focal"], flat["source"]),
                             ("flat", 1.4, 24.0, session))
            self.assertAlmostEqual(st.series["flat_level"], sim.AV_TARGET, delta=0.06)   # Rückmeldung beim Aufnehmen
            self.assertTrue(any("Flat-Helligkeit" in line for line in self.logs))
            self.assertEqual(st.last_flat["iso"], 400)
            # Dark-Flats: Moduswahlrad M, Deckel drauf – ISO und Zeit absichtlich falsch, das Studio stellt sie ein
            cam.panel, cam.cap = False, True
            cam.set_mode("M")
            self.assertTrue(wait_for(lambda: st.props.get(studio.P_AEMODE) == 3, 5))
            st.act_set({"prop": "iso", "value": 0x70})
            st.act_set({"prop": "shutter", "value": 0x6D})
            self.run_series(type="darkflat", target="Flats", count=3, pause=0, master=True)
            self.assertEqual(st.props[studio.P_ISO], 0x58, "\n".join(self.logs[-25:]))
            results = self.wait_master()
            self.assertEqual([r["kind"] for r in results], ["darkflat", "flat"])  # Flat neu – jetzt mit Dark-Flat
            df, flat2 = results
            self.assertEqual(df["iso"], 400)
            self.assertLess(abs(df["exposure"] / st.last_flat["exposure"] - 1), 0.25)
            self.assertEqual(flat2["flatcal"], df["name"])
            flats = [e for e in st.library.list() if e["kind"] == "flat" and e["source"] == session]
            self.assertEqual([e["name"] for e in flats], [flat2["name"]])          # alter Master-Flat ersetzt
            # Lights mit derselben Optik: der Live-Stack nimmt den Flat von selbst
            cam.cap = False
            cam.set_mode("B")
            self.assertTrue(wait_for(lambda: st.is_bulb_mode(), 5))
            st.act_set({"prop": "iso", "value": 0x70})
            st.act_live_start({"target": "Flats"})
            self.run_series(type="light", target="Flats", count=2, exposure=1, pause=0, download="raw")
            live = st.live
            self.assertTrue(wait_for(lambda: live.status()["frames"] + live.status()["rejected"] >= 2
                                     and not live.status()["busy"], 30))
            self.assertEqual(live.status()["calib"]["flat"], flat2["name"])
            st.act_live_stop({})
            self.assertTrue(wait_for(lambda: live.status()["done"], 20))
            titles = [i["title"] for i in live.status()["analysis"]["items"]]
            self.assertTrue(any(t.startswith("Flat gleicht die Vignettierung aus") for t in titles), titles)
        finally:
            cam.args.raw, cam.args.sky_speed, cam.cap, cam.panel = False, 1.0, False, False
            cam.set_mode("B")
            self.assertTrue(wait_for(lambda: st.is_bulb_mode(), 5))

    @unittest.skipUnless(studio.imaging.missing() is None, "braucht numpy und rawpy (venv)")
    def test_9d_bildverwaltung_ablauf_mit_siril(self):
        """Ablagen, Prüfen, Stapeln, Fertig entwickeln – Siril hier als Ersatzprogramm, das die Schrittmarken
        ausgibt, Zwischenbilder anlegt und Ergebnisse schreibt (das echte Siril: tests mit siril-cli separat)."""
        st = self.st
        info = st.gallery_info()
        self.assertEqual(info["roots"][0]["key"], "studio")          # die eigene Ablage zuerst
        sessions = {x["name"]: x for x in info["roots"][0]["index"]["sessions"]}
        name = next(n for n in sessions if n.endswith("_Flats"))        # aus test_9c: Lights, Flats, Dark-Flats
        res = st.act_gallery_export({"session": name, "reveal": False})["export"]
        self.assertEqual(res["counts"]["light"], 2)
        sm = st.gallery_session("studio", name)
        self.assertEqual((sm["lights"], sm["calib"]["flat"]["n"]), (2, 3))
        with self.assertRaises(studio.CameraError):
            st._root("gibt-es-nicht")

        def wait_job():
            self.assertTrue(wait_for(lambda: not st.gallery_job["active"], 60), st.gallery_job)
            self.assertIsNone(st.gallery_job["error"], st.gallery_job)
            return st.gallery_job

        st.act_gallery_check({"session": name})                        # echte Prüfung (numpy, rawpy)
        job = wait_job()
        self.assertEqual((job["check"]["n"], job["check"]["main"]), (2, 2))
        self.assertEqual(st.gallery_session("studio", name)["check"]["main"], 2)

        fake = os.path.join(tempfile.mkdtemp(), "siril-cli")
        with open(fake, "w") as f:
            f.write("#!%s\n" % sys.executable + "\n".join([
                "import os, sys",
                "a = sys.argv; d = a[a.index('-d') + 1]",
                "for line in open(a[a.index('-s') + 1]):",
                "    line = line.strip()",
                "    if line.startswith('# >> '):",
                "        print('log: ' + line); print('progress: Konvertiere, 50.00%', flush=True)",
                "    elif line.startswith('save ../ergebnis_'):",
                "        os.makedirs(os.path.join(d, 'process'), exist_ok=True)",
                "        open(os.path.join(d, 'process', 'pp_light_00001.fit'), 'wb').write(b'x' * 5000)",
                "        open(os.path.join(d, 'ergebnis_2s.fit'), 'wb').write(b'x')",
                "    elif line.split()[:1] in (['savejpg'], ['savetif']):",
                "        ext = '.jpg' if line.startswith('savejpg') else '.tif'",
                "        open(os.path.join(d, line.split()[1] + ext), 'wb').write(b'x')",
                "print('log: Script-Ausführung erfolgreich.')", ""]))
        os.chmod(fake, 0o755)
        real = studio.gallery.siril_cli
        studio.gallery.siril_cli = lambda found=None: fake
        try:
            st.act_gallery_stack({"session": name})
            job = wait_job()
            self.assertGreater(len(job["steps"]), 5)
            self.assertEqual(job["index"], len(job["steps"]))          # jeder Schritt erkannt
            self.assertTrue(job["result"].endswith("siril/ergebnis_2s.fit"))
            folder = os.path.dirname(os.path.join(studio.OUT, job["result"]))
            self.assertEqual(job["freed"], 5000)                       # Zwischenbilder gelöscht
            self.assertFalse(os.path.isdir(os.path.join(folder, "process")))
            # Fertig entwickeln eines Live-Stacks (RGB-FITS)
            live = os.path.join(studio.OUT, "Astro", name, "livestack")
            os.makedirs(live, exist_ok=True)
            fits = os.path.join(live, "LIVESTACK_20261119-0100_3x2s_raw.fits")
            studio.imaging.write_fits(fits, studio.imaging.np.ones((20, 30, 3), studio.imaging.np.float32), [])
            rel = studio.gallery.rel_of(studio.OUT, fits)
            st.act_gallery_finish({"rel": rel})
            job = wait_job()
            stem = "LIVESTACK_20261119-0100_3x2s_raw_fertig"
            self.assertEqual(sorted(os.path.basename(r) for r in job["results"]), [stem + ".jpg", stem + ".tif"])
            self.assertFalse(os.path.exists(os.path.join(live, ".6d-fertig.ssf")))
            results = {r["name"]: r for r in st.gallery_session("studio", name)["results"]}
            self.assertTrue(results["LIVESTACK_20261119-0100_3x2s_raw"]["finished_jpg"].endswith("_fertig.jpg"))
            items = studio.gallery.items(studio.OUT, "r:", "all")          # Gruppe „Ergebnisse“
            self.assertIn("ergebnis_2s", [i["name"] for i in items])
            with self.assertRaises(studio.CameraError):                # Masters sind keine Ergebnisse
                st.act_gallery_finish({"rel": "Astro/Bibliothek/flats/x.fits"})
            with open(fake, "w") as f:                                  # Fehler von Siril kommt an
                f.write("#!/bin/sh\necho 'log: Fehler: keine Bilder'\nexit 1\n")
            st.act_gallery_stack({"session": name})
            self.assertTrue(wait_for(lambda: not st.gallery_job["active"], 30))
            self.assertIn("keine Bilder", st.gallery_job["error"])
        finally:
            studio.gallery.siril_cli = real

    @unittest.skipUnless(studio.imaging.missing() is None, "braucht numpy und rawpy (venv)")
    def test_9e_bildverwaltung_mit_pixinsight(self):
        """Stapeln mit WBPP und Fertig entwickeln mit PixInsight – PixInsight als Ersatzprogramm, das die
        Ereigniszeilen der Skripte aus studio/pixinsight/ ausgibt und Dateien wie WBPP 3.1 hinterlässt (das echte
        PixInsight: von Hand, siehe docs/nachbearbeitung.md)."""
        st = self.st
        sessions = [x["name"] for x in st.gallery_info()["roots"][0]["index"]["sessions"]]
        name = next(n for n in sessions if n.endswith("_Flats"))        # aus test_9c: 2 Lights, Flats, Dark-Flats
        root = tempfile.mkdtemp()
        exe = os.path.join(root, "PixInsight", "PixInsight.app", "Contents", "MacOS", "PixInsight")
        wbpp_js = os.path.join(root, "PixInsight", "src", "scripts", "BatchPreprocessing", "WBPP.js")
        for p in (exe, wbpp_js):
            os.makedirs(os.path.dirname(p), exist_ok=True)
        open(wbpp_js, "w").close()
        group = "Light frames, 5472x3648, NoFilter, CFA, 1x1, 2.00s, frames = %d (%d active)"
        with open(exe, "w") as f:
            f.write("\n".join([
                "#!%s" % sys.executable,
                "import glob, os, sys",
                "sys.path.insert(0, %r)" % os.path.join(ROOT, "tests"),
                "import numpy as np, xisf_helper",
                "run = next(a for a in sys.argv if a.startswith('-r='))[3:].split(',')",
                "say = lambda s: print('6D>> ' + s, flush=True)",
                "print('PixInsight Core 1.9.6 Lockhart (arm64)')",
                "if run[0].endswith('WBPP.js'):",
                "    opt = dict(a.split('=', 1) for a in run[1:] if '=' in a)",
                "    n = len(glob.glob(os.path.join(opt['dir'], 'Light', '*')))",
                "    out, g = opt['outputDirectory'], %r %% (n, n)" % group,
                "    say('pipeline 4')",
                "    say('start 1/4 Calibration | ' + g)",
                "    os.makedirs(os.path.join(out, 'calibrated'), exist_ok=True)",
                "    open(os.path.join(out, 'calibrated', 'a_c.xisf'), 'wb').write(b'x' * 5000)",
                "    say('done 1/4 Calibration | %s | 2 | %d calibrated' % (g, n))",
                "    say('start 3/4 Integration | ' + g)",
                "    os.makedirs(os.path.join(out, 'master'), exist_ok=True)",
                "    xisf_helper.write_xisf(os.path.join(out, 'master', 'masterLight_BIN-1_RGB_autocrop.xisf'),",
                "                           np.full((8, 12, 3), 0.1, np.float32), [('EXPTIME', '2.000')])",
                "    say('done 3/4 Integration | %s | 2 | %d integrated' % (g, n))",
                "    say('done 4/4 Astrometric solution | %s | 3 | 1 failed' % g)",
                "    say('end')",
                "else:",
                "    src, target = run[1], run[2]",
                "    if 'kaputt' in src:",
                "        say('error Bild kaputt')",
                "        sys.exit(0)",
                "    for s in ('Bild laden', 'Strecken', 'Speichern'):",
                "        say('step ' + s)",
                "    for ext in ('.jpg', '.tif'):",
                "        open(target + ext, 'wb').write(b'x')",
                "        say('saved ' + target + ext)",
                "    say('fertig')", ""]))
        os.chmod(exe, 0o755)

        def wait_job():
            self.assertTrue(wait_for(lambda: not st.gallery_job["active"], 60), st.gallery_job)
            return st.gallery_job

        real = studio.gallery.pixinsight_cli
        studio.gallery.pixinsight_cli = lambda found=None: exe
        try:
            info = st.gallery_info()
            self.assertEqual((info["pixinsight"], info["wbpp"]), (True, True))
            st.act_gallery_stack({"session": name, "tool": "pixinsight"})
            job = wait_job()
            self.assertIsNone(job["error"], job)
            self.assertEqual((job["tool"], len(job["steps"]), job["index"]), ("pixinsight", 4, 3))
            self.assertTrue(job["result"].endswith("/pixinsight/wbpp_2x2s.xisf"), job["result"])
            self.assertEqual(job["freed"], 5000)                        # Zwischenbilder gelöscht
            self.assertEqual(job["warnings"], ["Astrometrie: 1 failed"])
            dest = os.path.join(studio.OUT, "Astro", name, "pixinsight")
            self.assertEqual(sorted(os.listdir(os.path.join(dest, "wbpp"))), ["master"])
            sm = st.gallery_session("studio", name)
            self.assertEqual(sm["results"][0]["name"], "wbpp_2x2s")      # neuestes Ergebnis zuerst
            st.act_gallery_finish({"rel": job["result"], "tool": "pixinsight"})
            job = wait_job()
            self.assertIsNone(job["error"], job)
            self.assertEqual((job["label"], job["index"], job["steps"][-1]), ("Mit PixInsight entwickeln", 3,
                                                                              "Speichern"))
            self.assertEqual(sorted(os.path.basename(r) for r in job["results"]),
                             ["wbpp_2x2s_fertig-pi.jpg", "wbpp_2x2s_fertig-pi.tif"])
            r = next(x for x in st.gallery_session("studio", name)["results"] if x["name"] == "wbpp_2x2s")
            self.assertEqual(r["finished_by"], ["pixinsight"])
            bad = os.path.join(dest, "kaputt.xisf")                      # Fehler im Skript kommt an
            shutil.copy(os.path.join(dest, "wbpp_2x2s.xisf"), bad)
            st.act_gallery_finish({"rel": studio.gallery.rel_of(studio.OUT, bad), "tool": "pixinsight"})
            self.assertEqual(wait_job()["error"], "PixInsight: Bild kaputt")
            with self.assertRaises(studio.CameraError):
                st.act_gallery_finish({"rel": job["result"], "tool": "photoshop"})
        finally:
            studio.gallery.pixinsight_cli = real
        studio.gallery.pixinsight_cli = lambda found=None: None          # nicht installiert
        try:
            with self.assertRaisesRegex(studio.CameraError, "PixInsight ist nicht installiert"):
                st.act_gallery_stack({"session": name, "tool": "pixinsight"})
        finally:
            studio.gallery.pixinsight_cli = real


if __name__ == "__main__":
    unittest.main()
