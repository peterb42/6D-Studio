#!/usr/bin/env python3
"""6D Rechner – PixInsight auf einem zweiten Rechner für 6D Studio (z. B. Linux-PC mit RTX 4090).

Das Studio auf dem Mac schickt die Rohbilder einer Sitzung hierher, startet WBPP oder „Fertig entwickeln“ und holt
das Ergebnis zurück. Hier läuft PixInsight ohne Oberfläche, genau wie im Studio (studio/gallery.py: wbpp_command,
run_wbpp, pi_finish_command, run_pi_finish; Skripte in studio/pixinsight/). Nur Standardbibliothek – auf dem Rechner
reicht ein Klon dieses Repositorys und Python 3.9 oder neuer.

Aufruf:
    python3 rechner/6d-rechner.py [--daten ~/6d-rechner] [--port 8090] [--adresse auto]
                                  [--pixinsight /opt/PixInsight/bin/PixInsight.sh] [--name pc-4090]

    --adresse auto   die Tailscale-Adresse (tailscale ip -4), sonst 127.0.0.1 – nur übers VPN erreichbar
    Schlüssel        RECHNER_TOKEN oder <daten>/token (beim ersten Start erzeugt); das Studio schickt ihn als
                     „Authorization: Bearer …“
    Linux ohne Bildschirm: PixInsight läuft in xvfb-run (Paket xvfb), solange DISPLAY nicht gesetzt ist.

KI-Entrauschen: liegt in <daten>/modelle ein MLDenoise-Modell (.xmlm, von pixinsight.com/dist), entwickelt der
Rechner damit fertig – auf der Grafikkarte (CUDA) etwa 19 s für ein 20-Megapixel-Bild.

Ablage: <daten>/sitzungen/<Sitzung>/ wie pixinsight/ einer Sitzung im Studio – Rohbilder/{Light,Dark,Flat,Bias}/,
wbpp/ (Arbeitsordner von WBPP), Ergebnisse wbpp_*.xisf und *_fertig-pi.* sowie hochgeladene Stapel.

Schnittstelle (JSON, außer Dateien):
    GET    /api/status                                  Rechner, PixInsight, Grafikkarte, Platz, laufender Auftrag
    GET    /api/sessions/<Sitzung>/files                {Pfad: {size, mtime}}
    PUT    /api/sessions/<Sitzung>/files/<Pfad>         Datei hochladen (X-Mtime: Änderungszeit, optional)
    GET    /api/sessions/<Sitzung>/files/<Pfad>         Datei herunterladen
    DELETE /api/sessions/<Sitzung>/files/<Pfad>         Datei löschen
    DELETE /api/sessions/<Sitzung>                      ganze Sitzung löschen
    POST   /api/jobs   {"kind": "wbpp", "session": …}   WBPP über Rohbilder/
                       {"kind": "finish", "session": …, "rel": …}   Stapel fertig entwickeln
    GET    /api/jobs/<id>                               Fortschritt wie im Studio (steps, index, step, …)
"""
import argparse
import hmac
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "studio"))
import gallery  # noqa: E402

VERSION = 1
SESSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
CHUNK = 1 << 20
LINUX_PIXINSIGHT = "/opt/PixInsight/bin/PixInsight.sh"
XVFB = ["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24"]


def log(text):
    print(time.strftime("%H:%M:%S ") + text, flush=True)


def default_pixinsight():
    if sys.platform == "darwin":
        return gallery.pixinsight_cli()
    return LINUX_PIXINSIGHT if os.path.isfile(LINUX_PIXINSIGHT) else None


def tailscale_ip():
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.split()
        return out[0] if out else None
    except (OSError, subprocess.SubprocessError):
        return None


def gpus():
    """NVIDIA-Karten über nvidia-smi: [„NVIDIA GeForce RTX 4090, 24564 MiB“, …] – für die Anzeige im Studio."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def ram_gb():
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            return round(int(out) / 2 ** 30)
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 2 ** 20)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def load_token(data):
    """RECHNER_TOKEN oder <daten>/token – beim ersten Start erzeugt (nur für den Besitzer lesbar)."""
    token = os.environ.get("RECHNER_TOKEN")
    if token:
        return token.strip()
    path = os.path.join(data, "token")
    if not os.path.isfile(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(24) + "\n")
        log("Neuer Schlüssel in %s – im Studio unter „Rechner“ eintragen" % path)
    with open(path) as f:
        return f.read().strip()


class Busy(Exception):
    pass


class Worker:
    """Sitzungsordner und Aufträge – einer zur Zeit (PixInsight nutzt ohnehin alle Kerne)."""

    def __init__(self, data, exe, name=None, prefix=None, run=gallery.run_pixinsight):
        self.data = os.path.abspath(os.path.expanduser(data))
        self.sessions = os.path.join(self.data, "sitzungen")
        os.makedirs(self.sessions, exist_ok=True)
        self.exe, self.name, self.run = exe, name or socket.gethostname().split(".")[0], run
        if prefix is None:                           # Linux ohne Bildschirm: virtueller X-Server
            prefix = XVFB if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and \
                shutil.which("xvfb-run") else []
        self.prefix = list(prefix)
        self.jobs, self.order, self.lock = {}, [], threading.Lock()
        self.gpu, self.ram = gpus(), ram_gb()
        self.models = os.path.join(self.data, "modelle")          # MLDenoise-Modelle (.xmlm)

    # ---------- Pfade
    def session_dir(self, session, create=False):
        if not SESSION_NAME.match(session or "") or ".." in session:
            raise ValueError("Ungültige Sitzung: %r" % session)
        p = os.path.join(self.sessions, session)
        if create:
            os.makedirs(p, exist_ok=True)
        elif not os.path.isdir(p):
            raise FileNotFoundError("Sitzung nicht vorhanden: %s" % session)
        return p

    def file_path(self, session, rel, create=False):
        parts = (rel or "").split("/")
        if not rel or any(not p or p in (".", "..") or p.startswith(".") or "\\" in p or "\0" in p for p in parts):
            raise ValueError("Ungültiger Pfad: %r" % rel)
        base = self.session_dir(session, create)
        return os.path.join(base, *parts)

    def listing(self, session):
        base = self.session_dir(session)
        files = {}
        for root, dirs, names in os.walk(base):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for n in names:
                if n.startswith(".") or n.endswith(".part"):
                    continue
                p = os.path.join(root, n)
                st = os.stat(p)
                files[os.path.relpath(p, base).replace(os.sep, "/")] = {"size": st.st_size, "mtime": st.st_mtime}
        return files

    # ---------- Zustand
    def status(self):
        exe = self.exe if self.exe and os.path.isfile(self.exe) else None
        free = shutil.disk_usage(self.data).free
        current = self.current()
        return {"ok": True, "name": self.name, "version": VERSION, "platform": platform.platform(terse=True),
                "pixinsight": bool(exe), "wbpp": bool(exe and gallery.wbpp_script(exe)), "cpus": os.cpu_count(),
                "ram_gb": self.ram, "gpu": self.gpu, "free_gb": round(free / 1e9, 1),
                "display": " ".join(self.prefix[:2]) if self.prefix else os.environ.get("DISPLAY") or "",
                "mldenoise": os.path.basename(gallery.find_model([self.models]) or "") or None,
                "job": dict(current) if current else None}

    def current(self):
        with self.lock:
            return next((self.jobs[i] for i in reversed(self.order) if self.jobs[i]["active"]), None)

    # ---------- Aufträge
    def start(self, kind, session, rel=None):
        if kind not in ("wbpp", "finish"):
            raise ValueError("Unbekannter Auftrag: %r" % kind)
        if not self.exe or not os.path.isfile(self.exe):
            raise ValueError("PixInsight nicht gefunden – --pixinsight angeben")
        base = self.session_dir(session)
        if kind == "wbpp":
            if not gallery.wbpp_script(self.exe):
                raise ValueError("WBPP nicht gefunden – PixInsight 1.9.6 oder neuer nötig")
            if not os.path.isdir(os.path.join(base, gallery.EXPORT_INPUT, gallery.WBPP_FOLDERS["light"])):
                raise ValueError("Keine Lights hochgeladen (%s/Light)" % gallery.EXPORT_INPUT)
            src = None
        else:
            src = self.file_path(session, rel)
            if not os.path.isfile(src):
                raise FileNotFoundError("Nicht vorhanden: %s" % rel)
        with self.lock:
            busy = next((self.jobs[i] for i in self.order if self.jobs[i]["active"]), None)
            if busy:
                raise Busy("Es läuft schon: %s (%s)" % (busy["label"], busy["session"]))
            job = {"id": uuid.uuid4().hex[:12], "kind": kind, "session": session, "active": True,
                   "label": "WBPP" if kind == "wbpp" else "Fertig entwickeln", "steps": [],
                   "step": "PixInsight startet", "index": 0, "percent": 0.0, "detail": "", "error": None,
                   "warnings": [], "result": None, "results": [], "freed": 0, "started": time.time(),
                   "finished": None}
            self.jobs[job["id"]] = job
            self.order = (self.order + [job["id"]])[-20:]
            for old in [i for i in self.jobs if i not in self.order]:
                del self.jobs[old]
        threading.Thread(target=self._work, args=(job, base, src), daemon=True).start()
        log("▶︎ %s: %s%s" % (job["label"], session, " · " + rel if rel else ""))
        return job

    def _work(self, job, base, src):
        try:
            if job["kind"] == "wbpp":
                path = gallery.run_wbpp(self.prefix + gallery.wbpp_command(self.exe, base), base, job, run=self.run)
                job["result"] = os.path.relpath(path, base).replace(os.sep, "/")
                job["results"] = [job["result"]]
            else:
                model = gallery.find_model([self.models])
                command, outputs = gallery.pi_finish_command(self.exe, src, model)
                done = gallery.run_pi_finish(self.prefix + command, outputs, job, run=self.run,
                                             steps=gallery.pi_finish_steps(model))
                job["results"] = [os.path.relpath(p, base).replace(os.sep, "/") for p in done]
                job["result"] = job["results"][0]
            job["step"] = "fertig"
            log("✔︎ %s: %s → %s (%.0f s)" % (job["label"], job["session"], job["result"],
                                           time.time() - job["started"]))
        except Exception as e:                       # jeder Fehler landet im Auftrag, der Dienst läuft weiter
            job["error"] = str(e)
            log("❌ %s: %s" % (job["label"], e))
        finally:
            job["finished"] = time.time()
            job["active"] = False


class Handler(BaseHTTPRequestHandler):
    worker = None
    token = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, code, obj, close=False):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, code, text):
        """Fehler – Verbindung danach zu, denn ein Rest der Anfrage (Datei) kann noch ungelesen sein."""
        self._json(code, {"ok": False, "error": text}, close=True)

    def _route(self):
        """→ (Teile des Pfads nach /api/) oder None, nachdem schon geantwortet wurde (Schlüssel falsch)."""
        auth = self.headers.get("Authorization", "")
        given = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if not hmac.compare_digest(given.encode(), self.token.encode()):
            self._drain()
            self._fail(401, "Schlüssel fehlt oder ist falsch")
            return None
        path = urlsplit(self.path).path
        if not path.startswith("/api/"):
            self._drain()
            self._fail(404, "Unbekannt")
            return None
        return [unquote(p) for p in path[5:].split("/")]

    def _drain(self):
        """Kleine Anfragen zu Ende lesen; größere nicht – die Verbindung wird nach dem Fehler ohnehin geschlossen."""
        n = int(self.headers.get("Content-Length") or 0)
        if 0 < n <= CHUNK:
            self.rfile.read(n)

    def _handle(self, method):
        parts = self._route()
        if parts is None:
            return
        w = self.worker
        try:
            if parts == ["status"] and method == "GET":
                return self._json(200, w.status())
            if parts[0] == "jobs":
                if len(parts) == 1 and method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    job = w.start(body.get("kind"), body.get("session"), body.get("rel"))
                    return self._json(200, {"ok": True, "job": job["id"]})
                if len(parts) == 2 and method == "GET":
                    job = w.jobs.get(parts[1])
                    return self._json(200, dict(job, ok=True)) if job else self._fail(404, "Auftrag unbekannt")
            if parts[0] == "sessions" and len(parts) >= 2:
                session = parts[1]
                if len(parts) == 2 and method == "DELETE":
                    shutil.rmtree(w.session_dir(session))
                    log("Sitzung gelöscht: %s" % session)
                    return self._json(200, {"ok": True})
                if len(parts) == 3 and parts[2] == "files" and method == "GET":
                    try:
                        return self._json(200, {"ok": True, "files": w.listing(session)})
                    except FileNotFoundError:
                        return self._json(200, {"ok": True, "files": {}})
                if len(parts) >= 4 and parts[2] == "files":
                    rel = "/".join(parts[3:])
                    if method == "PUT":
                        return self._put(w.file_path(session, rel, create=True))
                    path = w.file_path(session, rel)
                    if method == "GET":
                        return self._send_file(path)
                    if method == "DELETE":
                        os.remove(path)
                        return self._json(200, {"ok": True})
            self._drain()
            self._fail(404, "Unbekannt: %s %s" % (method, self.path))
        except Busy as e:
            self._fail(409, str(e))
        except FileNotFoundError as e:
            self._fail(404, str(e))
        except (ValueError, json.JSONDecodeError) as e:
            self._fail(400, str(e))
        except OSError as e:
            self._fail(500, str(e))

    def _put(self, path):
        n = self.headers.get("Content-Length")
        if n is None:
            return self._fail(411, "Content-Length fehlt")
        n = int(n)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        got = 0
        with open(tmp, "wb") as f:
            while got < n:
                chunk = self.rfile.read(min(CHUNK, n - got))
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        if got != n:
            os.remove(tmp)
            return self._fail(400, "Unvollständig: %d von %d Bytes" % (got, n))
        os.replace(tmp, path)
        mtime = self.headers.get("X-Mtime")
        if mtime:
            os.utime(path, (float(mtime), float(mtime)))
        self._json(200, {"ok": True, "size": got})

    def _send_file(self, path):
        if not os.path.isfile(path):
            return self._fail(404, "Nicht vorhanden")
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("X-Mtime", str(os.path.getmtime(path)))
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, CHUNK)

    def do_GET(self):
        self._handle("GET")

    def do_PUT(self):
        self._handle("PUT")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")


def serve(worker, token, address, port):
    """→ laufender Server (Thread); für Tests mit port=0."""
    handler = type("RechnerHandler", (Handler,), {"worker": worker, "token": token})
    httpd = ThreadingHTTPServer((address, port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main(argv=None):
    ap = argparse.ArgumentParser(description="6D Rechner – PixInsight für 6D Studio auf einem zweiten Rechner")
    ap.add_argument("--daten", default=os.environ.get("RECHNER_DATEN", "~/6d-rechner"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("RECHNER_PORT", "8090")))
    ap.add_argument("--adresse", default=os.environ.get("RECHNER_ADRESSE", "auto"),
                    help="auto = Tailscale-Adresse, sonst 127.0.0.1; 0.0.0.0 = alle Netze")
    ap.add_argument("--pixinsight", default=os.environ.get("RECHNER_PIXINSIGHT") or default_pixinsight())
    ap.add_argument("--name", default=None)
    ap.add_argument("--kein-xvfb", action="store_true", help="PixInsight nicht in xvfb-run starten")
    args = ap.parse_args(argv)
    worker = Worker(args.daten, args.pixinsight, args.name, prefix=[] if args.kein_xvfb else None)
    token = load_token(worker.data)
    address = args.adresse
    if address == "auto":                            # beim Hochfahren ist Tailscale vielleicht noch nicht oben
        deadline = time.time() + (60 if shutil.which("tailscale") else 0)
        address = tailscale_ip()
        while not address and time.time() < deadline:
            time.sleep(5)
            address = tailscale_ip()
        if not address:
            address = "127.0.0.1"
            log("Keine Tailscale-Adresse – nur auf diesem Rechner erreichbar (--adresse angeben)")
    httpd = serve(worker, token, address, args.port)
    st = worker.status()
    if st["mldenoise"]:
        log("KI-Entrauschen mit %s" % st["mldenoise"])
    log("6D Rechner „%s“ auf http://%s:%d – PixInsight: %s%s; Grafik: %s; Daten: %s (%.0f GB frei)" % (
        worker.name, address, httpd.server_address[1], args.pixinsight or "nicht gefunden",
        "" if st["wbpp"] else " (ohne WBPP)", ", ".join(st["gpu"]) or "–", worker.data, st["free_gb"]))
    if worker.prefix:
        log("PixInsight läuft in %s" % " ".join(worker.prefix[:2]))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
