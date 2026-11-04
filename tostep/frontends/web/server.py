# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
The browser interface of the Twoo STEP CAD Toolbox: no Tk or other GUI toolkit needed.

Runs a small web server on 127.0.0.1 (only this computer can reach it) and opens the page in the
default browser. Files are dropped/uploaded into a work folder, results are saved to the Output
folder and offered as a download.

What is generic lives here: the job queue, uploads, downloads, the 3D preview, the performance
profile, the diagnostic code. What belongs to one tool lives in that tool's plugin folder:
  ui/tab.html, tab.js, tab.css   its tab (the page is assembled from the plugins' fragments)
  web.py                          register(router) adds its /api/... routes; prepare_job() may adjust a job
Nothing in this file names a plugin.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, quote

from tostep import version
from tostep.core import registry
from tostep.core.job import scale_for

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
STEP_EXT = {".step", ".stp"}
BRAND = "Twoo STEP CAD Toolbox"


def safe_name(name, default="file"):
    name = os.path.basename(unquote(name or "")).strip()
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    return name or default


_safe_name = safe_name        # (older spelling used inside this file)


def _unique(path):
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = f"{base} ({n}){ext}"
        n += 1
    return path


class _KeepAwake:
    """Stop the computer idle-sleeping while a (possibly hour-long) job runs."""

    def __enter__(self):
        self.p = None
        try:
            if sys.platform == "darwin":
                self.p = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            elif sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        except Exception:
            pass
        return self

    def __exit__(self, *a):
        try:
            if self.p:
                self.p.terminate()
            elif sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass


def _lower_priority():
    """Heavy CAD work runs at low priority so the computer stays usable meanwhile."""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
        else:
            os.nice(5)
    except Exception:
        pass


# ------------------------------------------------------------------ requests, responses, routes
class Request:
    """What a route function sees. `q` is the parsed query string (lists), `body` the parsed JSON body
    (an empty dict when there is none or it is not read yet), `read(n)` the raw body for uploads."""

    def __init__(self, handler, path, q, length):
        self.path, self.q, self.length, self._h = path, q, length, handler
        self._left = length
        self.groups = ()
        self._body = None

    def arg(self, name, default=""):
        return self.q.get(name, [default])[0]

    @property
    def body(self):
        if self._body is None:
            raw = self.read() if self.length else b""
            self._body = json.loads(raw or b"{}") if raw else {}
        return self._body

    def read(self, n=None):
        n = self._left if n is None else min(n, self._left)
        data = self._h.rfile.read(n) if n > 0 else b""
        self._left -= len(data)
        return data


class Response:
    """Bytes with a type; `filename` makes the browser save it."""

    def __init__(self, data, ctype="application/octet-stream", filename=None, code=200):
        self.data, self.ctype, self.filename, self.code = data, ctype, filename, code


class Router:
    """(method, path pattern) -> function(app, request) returning a dict, (dict, status) or a Response.
    A pattern is an exact path, or a regular expression when it starts with 're:' (groups end up in
    request.groups). Plugins add theirs in web.py's register(router); the first match wins, and an
    exact path may not be claimed twice."""

    def __init__(self):
        self.exact = {}
        self.regex = []

    def _add(self, method, pattern, fn):
        if pattern.startswith("re:"):
            self.regex.append((method, re.compile(pattern[3:]), fn))
        else:
            key = (method, pattern)
            if key in self.exact:
                raise ValueError(f"route {method} {pattern} is already taken")
            self.exact[key] = fn

    def get(self, pattern, fn):
        self._add("GET", pattern, fn)

    def post(self, pattern, fn):
        self._add("POST", pattern, fn)

    def find(self, method, path):
        fn = self.exact.get((method, path))
        if fn:
            return fn, ()
        for m, rx, f in self.regex:
            if m == method:
                mo = rx.fullmatch(path)
                if mo:
                    return f, mo.groups()
        return None, ()


# ------------------------------------------------------------------ the app
class JobContext:
    """What a plugin's prepare_job() can see and change before a job is queued."""

    def __init__(self, app, plugin, jid, file_ref, src, src_name, local):
        self.app, self.plugin, self.jid = app, plugin, jid
        self.file_ref, self.src, self.src_name, self.local = file_ref, src, src_name, local
        self.stem = os.path.splitext(src_name)[0]
        self.dest_dir = app.out
        self.overwrite = False


class App:
    def __init__(self, work_dir, out_dir, reg=None):
        self.work = work_dir
        self.out = out_dir
        os.makedirs(self.work, exist_ok=True)
        os.makedirs(self.out, exist_ok=True)
        self.reg = reg or registry.discover()
        self.token = secrets.token_urlsafe(16)
        self.files = {}      # id -> {"path", "name"}
        self.jobs = {}       # id -> dict
        self.lock = threading.Lock()
        self.run_lock = threading.Lock()   # previews (one at a time)
        from tostep.core import perf
        self.perf = perf
        self.hw = perf.detect()
        self.profile = "auto"
        self.gpu_wanted = True
        self.slots = threading.Condition()  # queue slots (profile decides how many jobs run at once)
        self.running = 0
        self.gpu_log = []
        self.gpu_busy = False
        self.last_step = None              # job id of the last converted STEP
        self.state = {}                    # free space for plugins (a plugin keeps its own keys here)
        self.preselect = None              # file id opened from the command line / right-click
        self.router = Router()
        self.webmods = {}
        for p in self.reg.usable():
            if p.m.web_routes:
                try:
                    mod = p.module(p.m.web_routes)
                    self.webmods[p.id] = mod
                    if hasattr(mod, "register"):
                        mod.register(self.router)
                except Exception as e:  # noqa
                    p.problem = f"its web routes failed to load: {e}"
        _core_routes(self.router)

    # ---------------------------------------------------------------- jobs
    def start_job(self, kind, file_ref, params, start_at=None):
        plugin = self.reg.for_kind(kind)
        if not plugin:
            raise ValueError("unknown job")
        m = plugin.m
        local = False
        if file_ref.startswith("job:"):
            src_job = self.jobs.get(file_ref[4:])
            if not src_job or src_job["state"] != "done":
                raise ValueError("That converted file is no longer available")
            src, src_name = src_job["out_path"], src_job["filename"]
        else:
            f = self.files.get(file_ref)
            if not f:
                raise ValueError("Upload the file first")
            src, src_name, local = f["path"], f["name"], bool(f.get("local"))
        ext = os.path.splitext(src_name)[1].lower()
        if m.inputs and ext not in m.inputs:
            raise ValueError(m.reject or f"{m.name} cannot read {ext or 'this'} files")
        jid = secrets.token_hex(6)
        ctx = JobContext(self, plugin, jid, file_ref, src, src_name, local)
        web = self.webmods.get(plugin.id)
        if web is not None and hasattr(web, "prepare_job"):
            params = web.prepare_job(self, ctx, dict(params or {}))
        out_name = m.output.format(stem=ctx.stem)
        job = {"id": jid, "kind": kind, "plugin": plugin.id, "state": "queued", "progress": 0.0, "log": [],
               "src": src, "scale": scale_for(params.get("units", "mm")),
               "filename": out_name, "out_path": None, "saved_to": None, "summary": "",
               "error": None, "started": time.time(), "last_msg": time.time(),
               "start_at": float(start_at) if start_at else None, "name": src_name,
               "dest_dir": ctx.dest_dir, "overwrite": ctx.overwrite,
               "labelled_tmp": params.get("save_labelled") or None, "labelled_saved": None,
               "params": dict(params), "facts": None, "perf": None, "finished": None}
        with self.lock:
            self.jobs[jid] = job
        threading.Thread(target=self._run, args=(job, src, out_name, params), daemon=True).start()
        return jid

    def _run(self, job, src, out_name, p):
        log = lambda s: job["log"].extend(str(s).splitlines() or [""])
        with _KeepAwake():                       # also while waiting for a scheduled start
            if job.get("start_at"):
                job["state"] = "scheduled"
                while time.time() < job["start_at"] and not job.get("cancel"):
                    time.sleep(1.0)
            if not self._acquire_slot(job):
                job["state"] = "cancelled"
                return
            try:
                job["state"] = "running"
                job["started"] = time.time()
                st = self.settings()
                job["perf"] = dict(st)
                log(f"Performance profile: {st['name']} — {st['threads']} thread(s)"
                    + (", GPU on" if st["gpu"] else ", no GPU") + (", priority lowered" if st["nice"] else ""))
                tmp_out = os.path.join(self.work, job["id"] + ".step")
                spec = os.path.join(self.work, job["id"] + ".job.json")
                try:
                    with open(spec, "w", encoding="utf-8") as fh:
                        json.dump({"plugin": job["plugin"], "src": src, "out": tmp_out, "params": p}, fh)
                    proc = subprocess.Popen(
                        [sys.executable, "-E", "-s", "-u", "-m", "tostep.core.worker", spec],
                        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                        encoding="utf-8", errors="replace", bufsize=1,
                        env=self.perf.worker_env(st, dict(os.environ)))
                    job["proc"] = proc
                    if job.get("cancel"):
                        proc.kill()
                    failed = None
                    for line in proc.stdout:
                        line = line.rstrip("\n")
                        job["last_msg"] = time.time()
                        if line.startswith("@@"):
                            try:
                                m = json.loads(line[2:])
                            except ValueError:
                                continue
                            if "p" in m:
                                job["progress"] = m["p"]
                            elif "l" in m:
                                job["log"].append(m["l"])
                            elif "s" in m:
                                job["summary"] = m["s"]
                            elif "d" in m:
                                job["facts"] = m["d"]
                            elif "e" in m:
                                failed = m["e"]
                        elif line.strip():
                            job["log"].append(line)
                    rc = proc.wait()
                    if job.get("cancel"):
                        job["state"] = "cancelled"
                        log("Cancelled.")
                        return
                    if failed is not None or rc != 0:
                        raise RuntimeError(failed or f"the worker stopped unexpectedly (exit code {rc}) — "
                                           "this usually means the computer ran out of memory")
                    if not os.path.exists(tmp_out):
                        raise RuntimeError(f"{job['plugin']} finished without writing its result file")
                    dest = os.path.join(job["dest_dir"], out_name)
                    if not job["overwrite"]:
                        dest = _unique(dest)
                    os.makedirs(job["dest_dir"], exist_ok=True)
                    shutil.copyfile(tmp_out, dest)
                    lt = job.get("labelled_tmp")
                    if lt and os.path.exists(lt):
                        ld = _unique(os.path.join(self.out, os.path.splitext(out_name)[0] + "_hardpoints.stl"))
                        shutil.copyfile(lt, ld)
                        job["labelled_saved"] = ld
                        log(f"Saved the hardpoint STL to {ld}")
                    job["out_path"] = tmp_out
                    job["saved_to"] = dest
                    job["progress"] = 1.0
                    job["finished"] = time.time()
                    job["state"] = "done"
                    pl = self.reg.plugins.get(job["plugin"])
                    if pl and pl.m.chain:
                        self.last_step = job["id"]
                    log(f"Saved to {dest}")
                except Exception as e:  # noqa
                    if job.get("cancel"):
                        job["state"] = "cancelled"
                        return
                    job["error"] = str(e) or e.__class__.__name__
                    log("ERROR: " + job["error"])
                    job["finished"] = time.time()
                    job["state"] = "error"
                finally:
                    job.pop("proc", None)
                    try:
                        os.remove(spec)
                    except OSError:
                        pass
            finally:
                self._release_slot()

    def diag(self, jid):
        """The diagnostic of a job: one short code that carries its settings and result, the
        same as a QR symbol, and the same again in plain words (see core/diagcode.py)."""
        from tostep.core import diagcode
        job = self.jobs.get(jid)
        if not job:
            raise ValueError("no such job")
        state = job["state"] if job["state"] in diagcode.STATES else "running"
        code = diagcode.build(job["kind"], job.get("params"), state=state, result=job.get("facts"),
                              log=job["log"], error=job.get("error") or "", version=version.__version__,
                              job_id=jid, profile=job.get("perf"), when=job.get("finished") or job["started"])
        return {"code": code, "rows": diagcode.describe(diagcode.decode(code)), "qr": diagcode.qr_rows(code),
                "name": job.get("name", ""), "error": job.get("error") or "", "state": job["state"],
                "has_settings": job["kind"] == "convert"}

    # ------------------------------------------------------------ profiles and queue slots
    def settings(self):
        return self.perf.settings(self.profile, self.hw, self.gpu_wanted)

    def _acquire_slot(self, job):
        """Wait (first come, first served) until fewer jobs than the profile allows are running."""
        with self.slots:
            self.waiting = getattr(self, "waiting", [])
            self.waiting.append(job)
            if job["state"] not in ("scheduled",):
                job["state"] = "queued"
            while True:
                if job.get("cancel"):
                    self.waiting.remove(job)
                    self.slots.notify_all()
                    return False
                if self.waiting[0] is job and self.running < self.settings()["jobs"]:
                    self.waiting.pop(0)
                    self.running += 1
                    self.slots.notify_all()
                    return True
                job["state"] = "queued"
                self.slots.wait(0.5)

    def _release_slot(self):
        with self.slots:
            self.running = max(0, self.running - 1)
            self.slots.notify_all()

    def perf_info(self):
        st = self.settings()
        steps, note, card = self.perf.gpu_pack_steps()
        return {"hw": self.hw, "profile": self.profile, "effective": st,
                "descriptions": self.perf.DESCRIPTIONS, "gpu_wanted": self.gpu_wanted,
                "gpus": self.perf.gpu_cards(), "gpu_probe": self.perf.probe_torch(),
                "gpu_pack_available": steps is not None, "gpu_pack_note": note,
                "gpu_pack_card": card["name"] if card else None,
                "gpu_busy": self.gpu_busy, "gpu_pack_log": self.gpu_log[-40:]}

    def refresh_gpu(self):
        """Look at the cards again and re-test PyTorch (after an install, or when the user presses Look again)."""
        self.perf.gpu_cards(refresh=True)
        self.hw = self.perf.detect()

    def _gpu_thread(self, work):
        if self.gpu_busy:
            raise ValueError("Another graphics-card task is still running.")
        self.gpu_busy = True

        def go():
            try:
                work()
            except Exception as e:  # noqa
                self.gpu_log.append("Failed: " + str(e))
            finally:
                self.gpu_busy = False
        threading.Thread(target=go, daemon=True).start()

    def install_gpu_pack(self, card_name=None):
        steps, note, card = self.perf.gpu_pack_steps(card_name)
        if not steps:
            raise ValueError(note)
        if self.gpu_busy:
            raise ValueError("Another graphics-card task is still running.")
        self.gpu_log = [f"Installing graphics-card support for {card['name']} ({note})…"]

        def work():
            for cmd in steps:
                self.gpu_log.append("$ " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
                pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                      env=dict(os.environ, PYTHONNOUSERSITE="1"))
                for line in pr.stdout:
                    self.gpu_log.append(line.rstrip())
                rc = pr.wait()
                if rc != 0:
                    self.gpu_log.append(f"Failed (exit code {rc}). Nothing else was changed. The lines above say why; "
                                        "a network or disk-space problem is the usual cause.")
                    return
            self.gpu_log.append("Installed. Testing the card…")
            self.refresh_gpu()
            self.gpu_log.append("Done. " + self.perf.gpu_test_text(self.perf.gpu_test()))
        self._gpu_thread(work)

    def test_gpu(self):
        self.gpu_log = ["Testing the graphics card (a few seconds; the first run can take longer)…"]

        def work():
            self.refresh_gpu()
            self.gpu_log.append("Done. " + self.perf.gpu_test_text(self.perf.gpu_test()))
        self._gpu_thread(work)

    def look_gpu(self):
        self.gpu_log = ["Looking for graphics cards…"]

        def work():
            self.refresh_gpu()
            n = len(self.perf.gpu_cards())
            self.gpu_log.append("Done. " + (f"{n} graphics card(s) found." if n else
                                            "The operating system did not list any graphics card."))
        self._gpu_thread(work)

    def jobs_list(self):
        out = []
        for j in sorted(self.jobs.values(), key=lambda j: j["started"]):
            out.append({"id": j["id"], "kind": j["kind"], "name": j.get("name", ""), "state": j["state"],
                        "progress": j["progress"], "start_at": j.get("start_at"), "error": j["error"]})
        return out

    def cancel(self, jid):
        job = self.jobs.get(jid)
        if not job or job["state"] in ("done", "error", "cancelled"):
            return False
        job["cancel"] = True
        proc = job.get("proc")
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        if job["state"] in ("queued", "scheduled"):
            job["state"] = "cancelled"
            with self.slots:
                self.slots.notify_all()
        return True

    # ------------------------------------------------------------ activity monitor
    _act = {"t": 0.0, "disk": None, "cpu": 0.0, "bps": 0.0}

    def activity(self):
        """System CPU % and disk bytes/second since the last call (cheap; psutil if present)."""
        now = time.time()
        a = self._act
        try:
            import psutil
            a["cpu"] = psutil.cpu_percent(interval=None)
            d = psutil.disk_io_counters()
            tot = d.read_bytes + d.write_bytes if d else 0
        except Exception:
            tot = 0
            try:
                a["cpu"] = min(100.0, os.getloadavg()[0] / (os.cpu_count() or 1) * 100)
            except Exception:
                pass
        if a["disk"] is not None and now > a["t"]:
            a["bps"] = max(0.0, (tot - a["disk"]) / (now - a["t"]))
        a["disk"], a["t"] = tot, now
        return {"cpu": round(a["cpu"], 1), "disk": int(a["bps"])}

    def reveal(self, path):
        if not path or not os.path.exists(path):
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception:
            pass

    def shutdown_hooks(self):
        for mod in self.webmods.values():
            if hasattr(mod, "shutdown"):
                try:
                    mod.shutdown(self)
                except Exception:
                    pass


# ------------------------------------------------------------------ the generic routes
def _bad(e, code=400):
    return {"error": str(e) or e.__class__.__name__}, code


def _core_routes(r):
    def job_of(app, req):
        return app.jobs.get(req.groups[0])

    def r_job(app, req):
        job = job_of(app, req)
        if not job:
            return {"error": "no such job"}, 404
        since = int(req.arg("since", "0"))
        return ({k: job[k] for k in ("state", "progress", "filename", "saved_to", "summary", "error", "started",
                                     "last_msg", "start_at", "kind", "labelled_saved")}
                | {"now": time.time(), "log": job["log"][since:], "next": len(job["log"])})

    def r_diag(app, req):
        try:
            return app.diag(req.groups[0])
        except ValueError as e:
            return _bad(e, 404)

    def r_preview3d(app, req):
        job = job_of(app, req)
        if not job or job["state"] != "done":
            return {"error": "not ready"}, 404
        from tostep.core import preview3d
        which = req.arg("which", "result")
        try:
            with app.run_lock:
                if which == "mesh":
                    pl = app.reg.plugins.get(job["plugin"])
                    if pl is not None and not pl.m.mesh_preview:
                        raise ValueError(f"{pl.m.name} jobs have no original mesh to show here")
                    ext = os.path.splitext(job["src"])[1].lower()
                    if ext in STEP_EXT:
                        data = preview3d.step_preview(job["src"])
                    else:
                        data = preview3d.mesh_preview(job["src"], job.get("scale", 1.0))
                else:
                    data = preview3d.step_preview(job["out_path"])
            return data
        except Exception as e:  # noqa
            return _bad(e)

    def r_download(app, req):
        job = job_of(app, req)
        if not job or job["state"] != "done":
            return {"error": "not ready"}, 404
        if req.arg("what", "step") == "labels":
            if not job.get("labelled_saved") or not os.path.exists(job["labelled_saved"]):
                return {"error": "no hardpoint STL for this job"}, 404
            with open(job["labelled_saved"], "rb") as fh:
                data = fh.read()
            return Response(data, "model/stl", os.path.basename(job["labelled_saved"]))
        with open(job["out_path"], "rb") as fh:
            data = fh.read()
        return Response(data, "application/step", job["filename"])

    def r_last(app, req):
        j = app.jobs.get(app.last_step) if app.last_step else None
        return {"job": app.last_step, "name": j["filename"]} if j else {}

    def r_upload(app, req):
        name = _safe_name(req.arg("name", "file"))
        fid = secrets.token_hex(6)
        path = os.path.join(app.work, fid + "_" + name)
        left = req.length
        with open(path, "wb") as f:
            while left > 0:
                chunk = req.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        app.files[fid] = {"path": path, "name": name}
        return {"id": fid, "name": name, "size": req.length}

    def r_run(app, req):
        b = req.body
        try:
            return {"job": app.start_job(b.get("kind"), b.get("file", ""), b.get("params", {}), b.get("start_at"))}
        except ValueError as e:
            return {"error": str(e)}, 400

    def r_perf_set(app, req):
        b = req.body
        if b.get("profile") in app.perf.PROFILE_NAMES:
            app.profile = b["profile"]
        if "gpu" in b:
            app.gpu_wanted = bool(b["gpu"])
        return app.perf_info()

    def r_gpupack(app, req):
        try:
            app.install_gpu_pack((req.body or {}).get("card"))
            return {"ok": True}
        except ValueError as e:
            return {"error": str(e)}, 400

    def r_gputest(app, req):
        try:
            app.test_gpu()
            return {"ok": True}
        except ValueError as e:
            return {"error": str(e)}, 400

    def r_gpulook(app, req):
        try:
            app.look_gpu()
            return {"ok": True}
        except ValueError as e:
            return {"error": str(e)}, 400

    def r_reveal(app, req):
        job = job_of(app, req)
        app.reveal(job and job["saved_to"] or app.out)
        return {"ok": True}

    def r_integrate(app, req):
        from tostep.frontends import install
        w = req.body.get("what")
        fn = {"freecad": install.install_freecad, "fusion": install.install_fusion,
              "menu": install.install_menu_entry, "files": install.install_filemanager,
              "files-remove": lambda: install.install_filemanager(True)}.get(w)
        if not fn:
            return {"error": "unknown"}, 400
        try:
            return {"lines": fn()}
        except Exception as e:  # noqa
            return {"lines": [f"Failed: {e}"]}

    def r_open_output(app, req):
        app.reveal(app.out)
        return {"ok": True}

    def r_quit(app, req):
        app.shutdown_hooks()
        req.quit = True
        return {"ok": True}

    r.get("re:/api/job/([0-9a-f]+)", r_job)
    r.get("re:/api/diag/([0-9a-f]+)", r_diag)
    r.get("re:/api/preview3d/([0-9a-f]+)", r_preview3d)
    r.get("re:/api/download/([0-9a-f]+)", r_download)
    r.get("/api/perf", lambda app, req: app.perf_info())
    r.get("/api/jobs", lambda app, req: {"jobs": app.jobs_list()})
    r.get("/api/activity", lambda app, req: app.activity())
    r.get("/api/last", r_last)
    r.post("/api/upload", r_upload)
    r.post("/api/run", r_run)
    r.post("/api/perf", r_perf_set)
    r.post("/api/gpupack", r_gpupack)
    r.post("/api/gputest", r_gputest)
    r.post("/api/gpulook", r_gpulook)
    r.post("/api/cancel", lambda app, req: {"ok": app.cancel(req.body.get("job", ""))})
    r.post("re:/api/reveal/([0-9a-f]+)", r_reveal)
    r.post("/api/integrate", r_integrate)
    r.post("/api/open-output", r_open_output)
    r.post("/api/quit", r_quit)


# ------------------------------------------------------------------ the HTTP handler
def make_handler(app: App, page: bytes):
    class H(BaseHTTPRequestHandler):
        server_version = BRAND

        def log_message(self, *a):  # keep the terminal quiet
            pass

        def _send(self, data, ctype, code=200, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(json.dumps(obj).encode(), "application/json", code)

        def _authed(self, q=None):
            # A custom header can't be sent cross-site without a CORS preflight we never
            # grant, so web pages elsewhere can't drive this API.
            tok = self.headers.get("X-Token") or (q or {}).get("t", [None])[0]
            return tok and secrets.compare_digest(tok, app.token)

        def _dispatch(self, method):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if method == "GET" and u.path == "/favicon.ico":
                return self._send(FAVICON.encode(), "image/svg+xml")
            if method == "GET" and u.path == "/":
                return self._send(page.replace(b"__TOKEN__", app.token.encode()), "text/html; charset=utf-8")
            if not self._authed(q):
                if method == "POST":
                    self.rfile.read(int(self.headers.get("Content-Length") or 0))
                return self._json({"error": "forbidden"}, 403)
            n = int(self.headers.get("Content-Length") or 0) if method == "POST" else 0
            fn, groups = app.router.find(method, u.path)
            if fn is None:
                if n:
                    self.rfile.read(n)
                return self._json({"error": "not found"}, 404)
            req = Request(self, u.path, q, n)
            req.groups = groups
            req.quit = False
            try:
                out = fn(app, req)
            except (ValueError, KeyError) as e:
                out = ({"error": str(e).strip("'\"") or e.__class__.__name__}, 400)
            except Exception as e:  # noqa
                out = ({"error": str(e) or e.__class__.__name__}, 500)
            if req._left > 0:                       # unread body: drain it so the connection stays sane
                req.read()
            if isinstance(out, Response):
                extra = {}
                if out.filename:
                    fn_ = out.filename
                    ascii_fn = fn_.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
                    extra["Content-Disposition"] = (f"attachment; filename=\"{ascii_fn}\"; "
                                                    f"filename*=UTF-8''{quote(fn_)}")
                self._send(out.data, out.ctype, out.code, extra)
            elif isinstance(out, tuple):
                self._json(out[0], out[1])
            else:
                self._json(out)
            if req.quit:
                threading.Thread(target=self.server.shutdown, daemon=True).start()

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return H


# ------------------------------------------------------------------ the page
FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" '
           'fill="#2f6fed"/><path d="M8 22 16 8l8 14z" fill="none" stroke="#fff" stroke-width="2.5" '
           'stroke-linejoin="round"/></svg>')


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def build_page(reg=None) -> bytes:
    """Assemble the page: the shell, the plugins' tab fragments in manifest order, their scripts and styles."""
    reg = reg or registry.discover()
    tabs = reg.tabs()
    css = [_read(os.path.join(HERE, "style.css"))]
    js = [_read(os.path.join(HERE, "app.js"))]
    nav, sections, ids = [], [], []
    vars_: dict[str, str] = {}
    kind_tab, kind_name, no_orig, no_prev = {}, {}, [], []
    for i, p in enumerate(tabs):
        m = p.m
        ids.append(m.web_tab)
        nav.append(f'<button id="t-{m.web_tab}"{" class=\"on\"" if i == 0 else ""} '
                   f'onclick="tab(\'{m.web_tab}\')">{m.web_title}</button>')
        frag = _read(p.asset(m.web_fragment))
        sections.append(frag)
        if i == 0:
            sections.append(_read(os.path.join(HERE, "ui", "queue.html")))
        if m.web_style:
            css.append(_read(p.asset(m.web_style)))
        if m.web_script:
            js.append(f"// ---- {m.web_title} ({m.id})\n" + _read(p.asset(m.web_script)))
        if m.kind:
            kind_tab[m.kind] = m.web_tab
            kind_name[m.kind] = m.web_title.lower()
            if not m.mesh_preview:
                no_orig.append(m.kind)
            if not m.preview:
                no_prev.append(m.kind)
        mod = None
        if m.web_routes:
            try:
                mod = p.module(m.web_routes)
            except Exception:
                mod = None
        if mod is not None and hasattr(mod, "page_vars"):
            vars_.update(mod.page_vars())
    ids.append("plug")
    nav.append('<button id="t-plug" onclick="tab(\'plug\')">Install</button>')
    sections.append(_read(os.path.join(HERE, "ui", "install.html")))
    body = _read(os.path.join(HERE, "shell_body.html"))
    page = ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
            f"<title>{BRAND}</title><link rel=\"icon\" href=\"/favicon.ico\" type=\"image/svg+xml\">\n"
            "<style>\n" + "\n".join(css) + "\n</style></head><body>\n" + body
            + "\n<script>\n" + "\n".join(js) + "\ntab(window.TABS[0]);\n</script></body></html>\n")
    page = page.replace("<!--NAV-->", "".join(nav)).replace("<!--SECTIONS-->", "\n".join(sections))
    subs = {"__TABS__": json.dumps(ids), "__KIND_TAB__": json.dumps(kind_tab), "__KIND_NAME__": json.dumps(kind_name),
            "__NO_ORIG__": json.dumps(no_orig), "__NO_PREVIEW__": json.dumps(no_prev), "__VERSION__": version.__version__, "__BRAND__": BRAND}
    subs.update(vars_)
    for k, v in subs.items():
        page = page.replace(k, v)
    return page.encode("utf-8")


def serve(port=0, open_browser=True, work_dir=None, out_dir=None, open_path=""):
    work_dir = work_dir or os.environ.get("TOSTEP_WORK") or os.path.join(ROOT, "runtime", "work")
    out_dir = out_dir or os.environ.get("TOSTEP_OUTPUT") or os.path.join(ROOT, "Output")
    _lower_priority()
    reg = registry.discover()
    app = App(work_dir, out_dir, reg)
    for p in reg.usable():                 # a plugin may want to act on a file named on the command line
        mod = app.webmods.get(p.id)
        if open_path and mod is not None and hasattr(mod, "open_path"):
            try:
                mod.open_path(app, open_path)
            except ValueError as e:
                print(e)
    page = build_page(reg)
    from tostep.frontends import install
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, page.replace(b"__OUT__", json.dumps(out_dir).encode())))
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"{BRAND} is running at {url}")
    print(f"Results are saved in: {out_dir}")
    print("Keep this window open while you use the app. Close it (or press Ctrl+C) to quit.")
    if open_browser:
        threading.Timer(0.5, lambda: install.open_browser(url)).start()
        print("(If no browser window appears, open the address above in your browser.)")
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown_hooks()
        httpd.server_close()
        shutil.rmtree(work_dir, ignore_errors=True)
        print(f"{BRAND} stopped.")
