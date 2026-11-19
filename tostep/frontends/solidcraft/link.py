# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
solidcraft_link: talks to a running SolidCraft (https://github.com/tangency-labs/solidcraft) through its
control channel, so the toolbox can take a mesh body out of it and put the solid STEP back in.

SolidCraft has no plug-in runtime yet (its RFC 0002 is a draft: "No plugin runs"). What it does have is a
local control channel that runs the same commands as its toolbar. This file uses only what its public
docs describe (docs/control-protocol.md, docs/step-import.md, the command schemas); none of its code is
copied or linked, and nothing of the toolbox is needed here: pure standard library, one file.

The protocol, as documented:
  * TCP on 127.0.0.1, one JSON object per line each way. Port: SolidCraft writes it to
    <control dir>/current.port when Preferences > Assistant > Agents through MCP > "Let AI agents
    connect" is on (default 27878), or the user starts it with `solidcraft --control PORT`.
  * The first request carries a top-level "token": 64 hex characters read from <control dir>/PORT.token
    (made fresh for each launch, readable only by the user).
  * Requests: {"id": n, "method": "engine.execute", "params": {"command": ..., "params": {...}}};
    replies: {"id": n, "ok": true, "result": ...} or {"ok": false, "error": "..."}.
  * Commands used: selection.get (read only), file.export_stl {path, bodies}, file.insert_step {path, name}.

WORK IN PROGRESS: written from SolidCraft's docs of 10 November 2026 and tested against a stand-in
server that follows them; not yet run against a real SolidCraft.
"""
from __future__ import annotations

import json
import os
import socket
import sys

DEFAULT_PORT = 27878
TIMEOUT_S = 3600.0          # a big STEP insert can take a while; SolidCraft answers when done


class SolidCraftError(RuntimeError):
    """Anything that stops the link, in words a user can act on."""


def control_dir():
    """Where SolidCraft keeps current.port and the token files (its docs, 'Token')."""
    env = os.environ.get("SOLIDCRAFT_CONTROL_DIR")
    if env:
        return env
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "SolidCraft", "control")
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return os.path.join(base, "SolidCraft", "control")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return os.path.join(base, "solidcraft", "control")


def find_port(port=None):
    """The port asked for, else SOLIDCRAFT_CONTROL_PORT, else the one the app wrote, else 27878."""
    if port:
        return int(port)
    env = os.environ.get("SOLIDCRAFT_CONTROL_PORT")
    if env and env.strip().isdigit():
        return int(env)
    try:
        with open(os.path.join(control_dir(), "current.port"), encoding="utf-8") as fh:
            txt = fh.read().strip()
        if txt.isdigit():
            return int(txt)
    except OSError:
        pass
    return DEFAULT_PORT


def read_token(port, token=None):
    """The token asked for, else SOLIDCRAFT_CONTROL_TOKEN, else <control dir>/<port>.token."""
    tok = token or os.environ.get("SOLIDCRAFT_CONTROL_TOKEN")
    if tok:
        return tok.strip()
    path = os.path.join(control_dir(), f"{port}.token")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        raise SolidCraftError(
            f"No SolidCraft token at {path}. Is SolidCraft running, with Preferences > Assistant > "
            "Agents through MCP > 'Let AI agents connect' turned on?")


class Link:
    """One connection to a running SolidCraft. Use as `with Link() as sc: ...`."""

    def __init__(self, port=None, token=None, host="127.0.0.1", timeout=TIMEOUT_S):
        self.port = find_port(port)
        self.host = host
        self.timeout = timeout
        self._token = token
        self._sock = None
        self._buf = b""
        self._id = 0
        self._authed = False

    # -- connection
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def connect(self):
        tok = read_token(self.port, self._token)
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=10)
        except OSError as e:
            raise SolidCraftError(
                f"No SolidCraft is listening on {self.host}:{self.port} ({e}). Start SolidCraft and turn on "
                "Preferences > Assistant > Agents through MCP > 'Let AI agents connect' "
                "(or start it with: solidcraft --control PORT).")
        self._sock.settimeout(self.timeout)
        self._token = tok
        return self

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _readline(self):
        while b"\n" not in self._buf:
            chunk = self._sock.recv(1 << 16)
            if not chunk:
                raise SolidCraftError("SolidCraft closed the connection (it may have quit, or refused the token).")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line

    def call(self, method, params=None):
        """One request; the reply's result, or SolidCraftError with SolidCraft's own message."""
        if self._sock is None:
            self.connect()
        self._id += 1
        req = {"id": self._id, "method": method, "params": params or {}}
        if not self._authed:
            req["token"] = self._token
        self._sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
        while True:
            line = self._readline().strip()
            if not line:
                continue
            try:
                rep = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            if not isinstance(rep, dict):
                continue
            if rep.get("id") not in (None, self._id) and "method" in rep:
                continue                              # a notification (design.changed and the like)
            break
        if not rep.get("ok", False):
            err = str(rep.get("error") or "unknown error")
            if err.startswith("unauthorized"):
                raise SolidCraftError("SolidCraft refused the token. If SolidCraft was restarted or force-quit, "
                                      "start it again (a stale token file is left behind by a crash).")
            raise SolidCraftError(f"SolidCraft: {err}")
        self._authed = True
        return rep.get("result")

    def execute(self, command, params=None):
        return self.call("engine.execute", {"command": command, "params": params or {}})

    # -- what the toolbox needs
    def selected_bodies(self):
        """Names of the bodies selected in SolidCraft (selection.get, read only)."""
        res = self.execute("selection.get", {"detail": False}) or {}
        names = []
        for it in res.get("items") or []:
            # the docs: each item "as select.set takes it (type, body, …) with kind"; a face or edge
            # item names its body too, so only whole-body items count
            if isinstance(it, dict) and "body" in (it.get("type"), it.get("kind")) and it.get("body"):
                b = str(it["body"])
                if b not in names:
                    names.append(b)
        return names

    def export_stl(self, body, path):
        """Write one body as a binary STL (file.export_stl). SolidCraft writes the file itself, so the
        path must be one it can write to: on this computer, absolute."""
        return self.execute("file.export_stl", {"path": os.path.abspath(path), "bodies": [body]})

    def insert_step(self, path, name=None):
        """Insert a STEP file as a new component (file.insert_step). Returns SolidCraft's reply
        (component, component_name, occurrence)."""
        p = {"path": os.path.abspath(path)}
        if name:
            p["name"] = name
        return self.execute("file.insert_step", p)


def status(port=None, token=None):
    """(True, text) if a SolidCraft answers, else (False, why). Never raises."""
    try:
        with Link(port, token) as sc:
            rev = sc.call("document.revision")
            return True, f"SolidCraft is running on port {sc.port} (design {rev.get('document_id', '?')})."
    except SolidCraftError as e:
        return False, str(e)
    except (OSError, ValueError) as e:
        return False, f"SolidCraft did not answer properly: {e}"
