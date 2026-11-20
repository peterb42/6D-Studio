"""Anbindung an 6D Rechner (rechner/6d-rechner.py): Einstellungen, Dateien abgleichen, Aufträge.

Der Rechner (z. B. Linux-PC mit RTX 4090, über Tailscale erreichbar) hält je Sitzung einen Ordner wie pixinsight/ im
Studio. Das Studio lädt die Rohbilder hoch (nur, was fehlt oder sich geändert hat), startet WBPP oder „Fertig
entwickeln“ dort und holt das Ergebnis zurück. Übertragung über HTTP mit Schlüssel – eine Verbindung, keine
SSH-Schlüssel; abgebrochene Dateien werden beim nächsten Abgleich neu geschickt.

Nur Standardbibliothek. Einstellungen in ~/.config/6d-studio/rechner.json (RECHNER_CONFIG): url, token, live.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import gallery

CONFIG = os.path.expanduser(os.environ.get("RECHNER_CONFIG", "~/.config/6d-studio/rechner.json"))
DEFAULTS = {"url": "", "token": "", "live": False}
LIVE_SETTLE = 5.0                    # Sekunden: so alt muss eine Datei sein, bevor sie vorab hochgeht (fertig geladen)
LIVE_HOURS = 12                      # vorab hochladen nur für Sitzungen, die in dieser Zeit neue Bilder bekamen


class RechnerError(Exception):
    pass


def load_config(path=CONFIG):
    cfg = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg, path=CONFIG):
    """Nur für den Besitzer lesbar – der Schlüssel steht darin."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({k: cfg.get(k, v) for k, v in DEFAULTS.items()}, f, indent=1)
    os.replace(tmp, path)


def normalize_url(url):
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    if url and not re.match(r"^https?://[^/\s]+$", url):
        raise ValueError("Adresse wie http://100.101.102.103:8090 oder http://pc-4090:8090")
    return url


def remote_session(label, session):
    """Name der Sitzung auf dem Rechner: Ablage und Sitzung („Kamera_2026-11-18_TenneMilkyway“) – Kamera- und
    Simulator-Ablage können gleich benannte Sitzungen haben."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", "%s_%s" % (label, session)).strip("-")


class _Reader:
    """Datei zum Hochladen, meldet die gelesenen Bytes."""

    def __init__(self, f, progress):
        self.f, self.progress, self.done = f, progress, 0

    def read(self, n=-1):
        chunk = self.f.read(n if n and n > 0 else 1 << 20)
        self.done += len(chunk)
        if self.progress and chunk:
            self.progress(self.done)
        return chunk


class Client:
    def __init__(self, url, token, timeout=20):
        self.url, self.token, self.timeout = normalize_url(url), token or "", timeout

    def _open(self, method, path, data=None, headers=None, timeout=None):
        h = {"Authorization": "Bearer " + self.token}
        h.update(headers or {})
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=h)
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            with e:
                try:
                    msg = json.loads(e.read() or b"{}").get("error")
                except ValueError:
                    msg = None
            raise RechnerError(msg or "Rechner antwortet mit HTTP %d" % e.code) from None
        except (urllib.error.URLError, OSError) as e:
            raise RechnerError("Rechner nicht erreichbar (%s)" % getattr(e, "reason", e)) from None

    def _json(self, method, path, body=None, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        with self._open(method, path, data, headers, timeout) as r:
            return json.loads(r.read() or b"{}")

    @staticmethod
    def _p(session, rel=None):
        path = "/api/sessions/" + urllib.parse.quote(session, safe="")
        return path + "/files" + ("/" + urllib.parse.quote(rel) if rel else "")

    def status(self, timeout=3):
        return self._json("GET", "/api/status", timeout=timeout)

    def files(self, session):
        return self._json("GET", self._p(session))["files"]

    def put(self, session, rel, path, progress=None):
        size = os.path.getsize(path)
        headers = {"Content-Type": "application/octet-stream", "Content-Length": str(size),
                   "X-Mtime": "%.3f" % os.path.getmtime(path)}
        with open(path, "rb") as f:
            with self._open("PUT", self._p(session, rel), _Reader(f, progress), headers, timeout=120) as r:
                return json.loads(r.read())

    def get(self, session, rel, dest, progress=None):
        """→ dest; erst als .part, dann umbenannt (ein Abbruch hinterlässt nichts Halbes unter dem richtigen Namen)."""
        tmp = dest + ".part"
        with self._open("GET", self._p(session, rel), timeout=120) as r, open(tmp, "wb") as f:
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, int(r.headers.get("Content-Length") or 0))
        os.replace(tmp, dest)
        return dest

    def delete(self, session, rel):
        return self._json("DELETE", self._p(session, rel))

    def start(self, kind, session, rel=None):
        return self._json("POST", "/api/jobs", {"kind": kind, "session": session, "rel": rel})["job"]

    def job(self, job_id):
        return self._json("GET", "/api/jobs/" + urllib.parse.quote(job_id, safe=""))

    def wait(self, job_id, update, poll=1.0, patience=120.0):
        """Auftrag verfolgen, bis er fertig ist; update(Auftrag) bei jeder Abfrage. Kurze Netzaussetzer (WLAN,
        Mobilfunk) übersteht er – erst nach patience Sekunden ohne Antwort gilt der Rechner als weg."""
        lost = None
        while True:
            try:
                job = self.job(job_id)
                lost = None
            except RechnerError:
                lost = lost or time.time()
                if time.time() - lost > patience:
                    raise
                time.sleep(poll)
                continue
            update(job)
            if not job.get("active"):
                return job
            time.sleep(poll)


# ---------------------------------------------------------------- Dateien abgleichen
def export_files(folder, prefix=gallery.EXPORT_INPUT):
    """Bilddateien in pixinsight/Rohbilder (export_pixinsight) → {„Rohbilder/Light/…“: Pfad}."""
    wanted = {}
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for n in sorted(names):
            if not n.startswith(".") and gallery.kind_of(n) in ("raw", "jpeg", "fits", "xisf"):
                rel = os.path.relpath(os.path.join(root, n), folder).replace(os.sep, "/")
                wanted[prefix + "/" + rel] = os.path.join(root, n)
    return wanted


def live_files(out, session, library, settle=LIVE_SETTLE, now=None):
    """Rohbilder einer Sitzung, die der Export für WBPP nähme (gallery.frame_sets: Kalibrierbilder passend zu den
    Lights), unter ihrem Namen im Export – zum Vorab-Hochladen während der Serie. Dateien jünger als settle Sekunden
    lädt die Kamera vielleicht noch."""
    now = time.time() if now is None else now
    wanted = {}
    for ftype, s in gallery.frame_sets(out, session, library).items():
        for p in s["files"]:
            if gallery.kind_of(p) == "raw" and os.path.isfile(p) and now - os.path.getmtime(p) >= settle:
                sub = gallery.WBPP_FOLDERS[ftype]
                wanted["%s/%s/%s" % (gallery.EXPORT_INPUT, sub, gallery.wbpp_name(p, ftype))] = p
    return wanted


def sync(client, session, wanted, remote=None, delete_prefix=None, progress=None):
    """Fehlende oder geänderte Dateien hochladen (Vergleich über die Größe); mit delete_prefix auch Dateien darunter
    löschen, die nicht mehr gewünscht sind (aussortiert, nicht passend). progress(i, n, Bytes, Bytes gesamt).
    → {uploaded, bytes, deleted, kept}"""
    remote = client.files(session) if remote is None else remote
    todo = [(rel, p) for rel, p in sorted(wanted.items())
            if (remote.get(rel) or {}).get("size") != os.path.getsize(p)]
    total, sent = sum(os.path.getsize(p) for _, p in todo), 0
    for i, (rel, p) in enumerate(todo):
        if progress:
            progress(i, len(todo), sent, total)
        client.put(session, rel, p, progress and (lambda n, i=i, s=sent: progress(i, len(todo), s + n, total)))
        sent += os.path.getsize(p)
        remote[rel] = {"size": os.path.getsize(p)}
    deleted = 0
    if delete_prefix:
        for rel in sorted(remote):
            if rel.startswith(delete_prefix) and rel not in wanted:
                client.delete(session, rel)
                del remote[rel]
                deleted += 1
    if progress:
        progress(len(todo), len(todo), sent, total)
    return {"uploaded": len(todo), "bytes": sent, "deleted": deleted, "kept": len(wanted) - len(todo)}


def recent_sessions(out, hours=LIVE_HOURS, now=None):
    """Sitzungen mit neuen Rohbildern in den letzten Stunden – Kandidaten fürs Vorab-Hochladen."""
    now = time.time() if now is None else now
    base = os.path.join(out, "Astro")
    found = []
    if not os.path.isdir(base):
        return found
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if name.startswith(".") or name == "Bibliothek" or not os.path.isdir(d):
            continue
        times = [os.path.getmtime(os.path.join(d, f)) for f in gallery.FRAME_FOLDERS
                 if os.path.isdir(os.path.join(d, f))]
        if times and now - max(times) < hours * 3600:
            found.append(name)
    return found


def frame_label(rel):
    """„Rohbilder/Light/…“ → „Lights“ – für die Anzeige des Vorab-Hochladens."""
    sub = rel.split("/")[1] if rel.count("/") >= 2 else ""
    return {"Light": "Lights", "Dark": "Darks", "Flat": "Flats", "Bias": "Bias"}.get(sub, sub)

