# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The SolidCraft link (tostep/frontends/solidcraft) against a stand-in SolidCraft.

The stand-in follows SolidCraft's documented control channel (docs/control-protocol.md of
tangency-labs/solidcraft, 10 November 2026): JSON lines over TCP on 127.0.0.1, a per-launch token
in <control dir>/<port>.token that the first request must carry, `current.port` beside it,
`engine.execute` replies {ok, result} or {ok: false, error}, and strict parameters (unknown keys are
errors, as SolidCraft's `deny_unknown_fields` schemas make them). It is not SolidCraft: a real run
against the app is still to do (see docs/guide/solidcraft.md).

    python -m unittest tests.test_solidcraft -v
"""
import json
import os
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import trimesh  # noqa: E402

from tostep.frontends.solidcraft import cli as sc_cli  # noqa: E402
from tostep.frontends.solidcraft import link  # noqa: E402

ALLOWED = {"selection.get": {"detail"}, "file.export_stl": {"path", "bodies", "quality"},
           "file.insert_step": {"path", "name", "names", "folder", "matrix"}}


class FakeSolidCraft:
    """Just enough of SolidCraft's control channel, as documented."""

    def __init__(self, control_dir, bodies=("Cube",), selection=None):
        self.dir = control_dir
        self.token = secrets.token_hex(32)
        self.bodies = {b: trimesh.creation.box((20, 30, 10)) for b in bodies}
        self.selection = selection if selection is not None else [{"type": "body", "body": bodies[0]}]
        self.inserted = []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        os.makedirs(control_dir, exist_ok=True)
        with open(os.path.join(control_dir, f"{self.port}.token"), "w") as fh:
            fh.write(self.token + "\n")
        with open(os.path.join(control_dir, "current.port"), "w") as fh:
            fh.write(str(self.port))
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self):
        # shutdown first: close() alone leaves the accept() thread blocked on the old descriptor
        # number, and the next stand-in can get that number and be answered with this one's token
        try:
            self.srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.srv.close()

    def _serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

    def _conn(self, c):
        f = c.makefile("rwb")
        authed = False
        for raw in f:
            if not raw.strip():
                continue
            req = json.loads(raw)
            if not authed:
                if req.get("token") != self.token:
                    f.write(json.dumps({"ok": False, "error": "unauthorized: wrong token"}).encode() + b"\n")
                    f.flush()
                    c.close()
                    return
                authed = True
            try:
                res = self._handle(req["method"], req.get("params") or {})
                out = {"id": req["id"], "ok": True, "result": res}
            except ValueError as e:
                out = {"id": req["id"], "ok": False, "error": str(e)}
            f.write(json.dumps(out).encode() + b"\n")
            f.flush()

    def _handle(self, method, p):
        if method == "document.revision":
            return {"document_id": "18f3a0c2d4e5b6a7-1", "revision": 3}
        if method != "engine.execute":
            raise ValueError(f"unknown method {method}")
        cmd, a = p["command"], p.get("params") or {}
        extra = set(a) - ALLOWED.get(cmd, set())
        if extra:
            raise ValueError(f"{cmd}: unknown field {sorted(extra)[0]}")
        if cmd == "selection.get":
            return {"count": len(self.selection), "items": self.selection}
        if cmd == "file.export_stl":
            if not os.path.isabs(a["path"]):
                raise ValueError("file.export_stl: path must be absolute")
            b = (a.get("bodies") or [None])[0]
            if b not in self.bodies:
                raise ValueError(f"file.export_stl: no body named {b}")
            self.bodies[b].export(a["path"])
            return {"path": a["path"]}
        if cmd == "file.insert_step":
            with open(a["path"], "rb") as fh:
                head = fh.read(12)
            if head != b"ISO-10303-21":
                raise ValueError("file.insert_step: not a STEP file")
            self.inserted.append(dict(a))
            name = a.get("name") or os.path.splitext(os.path.basename(a["path"]))[0]
            return {"component": 7, "component_name": name, "occurrence": 9}
        raise ValueError(f"unknown command {cmd}")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cdir = os.path.join(self.tmp, "control")
        os.environ["SOLIDCRAFT_CONTROL_DIR"] = self.cdir
        for k in ("SOLIDCRAFT_CONTROL_PORT", "SOLIDCRAFT_CONTROL_TOKEN"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.pop("SOLIDCRAFT_CONTROL_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class Link(Base):
    def test_no_app_says_how_to_turn_it_on(self):
        ok, text = link.status()
        self.assertFalse(ok)
        self.assertIn("Let AI agents connect", text)

    def test_status_reads_port_and_token_from_the_control_folder(self):
        fake = FakeSolidCraft(self.cdir)
        try:
            self.assertEqual(link.find_port(), fake.port)
            ok, text = link.status()
            self.assertTrue(ok, text)
            self.assertIn(str(fake.port), text)
        finally:
            fake.close()

    def test_wrong_token_is_explained(self):
        fake = FakeSolidCraft(self.cdir)
        try:
            ok, text = link.status(token="00" * 32)
            self.assertFalse(ok)
            self.assertIn("refused the token", text)
        finally:
            fake.close()

    def test_only_whole_bodies_count_as_selected(self):
        sel = [{"type": "face", "body": "Cube", "index": 2},
               {"type": "body", "body": "Scan"}, {"type": "body", "body": "Scan"}]
        fake = FakeSolidCraft(self.cdir, bodies=("Cube", "Scan"), selection=sel)
        try:
            with link.Link() as sc:
                self.assertEqual(sc.selected_bodies(), ["Scan"])
        finally:
            fake.close()

    def test_solidcraft_errors_come_through(self):
        fake = FakeSolidCraft(self.cdir)
        try:
            with link.Link() as sc:
                with self.assertRaises(link.SolidCraftError) as cm:
                    sc.export_stl("Nope", os.path.join(self.tmp, "x.stl"))
            self.assertIn("no body named Nope", str(cm.exception))
        finally:
            fake.close()


class EndToEnd(Base):
    def test_selected_bodies_become_inserted_solids(self):
        fake = FakeSolidCraft(self.cdir, bodies=("Cube", "Block"),
                              selection=[{"type": "body", "body": "Cube"}, {"type": "body", "body": "Block"}])
        keep = os.path.join(self.tmp, "keep")
        try:
            rc = sc_cli.main(["--preset", "draft", "--keep", keep])
        finally:
            fake.close()
        self.assertEqual(rc, 0)
        self.assertEqual([i["name"] for i in fake.inserted], ["Cube solid", "Block solid"])
        for i in fake.inserted:
            self.assertTrue(os.path.isabs(i["path"]))
            self.assertTrue(os.path.getsize(i["path"]) > 1000)

    def test_a_file_is_converted_and_inserted(self):
        fake = FakeSolidCraft(self.cdir)
        src = os.path.join(self.tmp, "bracket.stl")
        trimesh.creation.cylinder(radius=8, height=12, sections=48).export(src)
        try:
            rc = sc_cli.main(["--file", src, "--preset", "draft"])
        finally:
            fake.close()
        self.assertEqual(rc, 0)
        self.assertEqual(fake.inserted[0]["name"], "bracket")

    def test_nothing_selected_is_a_clear_message(self):
        fake = FakeSolidCraft(self.cdir, selection=[])
        try:
            rc = sc_cli.main([])
        finally:
            fake.close()
        self.assertEqual(rc, 1)
        self.assertEqual(fake.inserted, [])


class Web(Base):
    """The app's two buttons: take the selected body, send a finished STEP back."""

    def test_fetch_then_send(self):
        from http.server import ThreadingHTTPServer
        from tostep.core import registry
        from tostep.frontends.web import server
        fake = FakeSolidCraft(self.cdir, bodies=("Scan",))
        reg = registry.discover(fresh=True)
        app = server.App(os.path.join(self.tmp, "work"), os.path.join(self.tmp, "out"), reg)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(app, b"page"))
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def post(path):
            r = urllib.request.Request(base + path, data=b"{}", method="POST",
                                       headers={"X-Token": app.token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(r) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        try:
            code, j = post("/api/solidcraft/fetch")
            self.assertEqual(code, 200, j)
            self.assertEqual(j["body"], "Scan")
            self.assertTrue(os.path.isfile(app.files[j["id"]]["path"]))
            # a finished job's STEP goes back
            step = os.path.join(self.tmp, "Scan.step")
            with open(step, "w") as fh:
                fh.write("ISO-10303-21;\nEND-ISO-10303-21;\n")
            app.jobs["abc123"] = {"state": "done", "out_path": step, "filename": "Scan.step"}
            code, j = post("/api/solidcraft/send/abc123")
            self.assertEqual(code, 200, j)
            self.assertEqual(j["component"], "Scan")
            self.assertEqual(fake.inserted[-1]["name"], "Scan")
            # and with no SolidCraft running, a message, not a crash
            fake.close()
            os.remove(os.path.join(self.cdir, f"{fake.port}.token"))
            code, j = post("/api/solidcraft/fetch")
            self.assertEqual(code, 400)
            self.assertIn("SolidCraft", j["error"])
        finally:
            httpd.shutdown()
            fake.close()


if __name__ == "__main__":
    unittest.main()
