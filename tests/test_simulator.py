"""Integrationstests: 6D Studio gegen die simulierte Kamera (sim/sim6d.py) – ohne echte 6D.

Prüft den kompletten Weg: Anmeldung, Ereignisse, Einstellungen, Livebild, Lupe, Fokus, Bulb mit
Download, Zugang für gphoto2 (Session-Vortäuschung) und automatisches Wiederverbinden.
Dauer ~15 s.  Ausführen:  python3 -m unittest tests.test_simulator -v
"""
import importlib.util
import os
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


if __name__ == "__main__":
    unittest.main()
