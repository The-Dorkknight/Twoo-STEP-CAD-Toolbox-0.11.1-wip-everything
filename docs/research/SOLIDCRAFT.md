<!-- Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight) -->
<!-- Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. -->

# SolidCraft: how the toolbox can plug into it (research note, 10 November 2026)

**Question:** can the toolbox have a plug-in for SolidCraft, the new open-source Fusion-style CAD app?

**Source read:** the public repository `github.com/tangency-labs/solidcraft` at commit `e7ac83a0`
(10 November 2026; `bherbruck/solidcraft` is the same tree): README, `docs/rfcs/0002-plugins.md`,
`docs/control-protocol.md`, `docs/mcp.md`, `docs/step-import.md`, `docs/scripts.md`, and the command
schemas under `crates/engine/src/schema/`. Nothing was built or run: building SolidCraft here was not
allowed by this session's safety rules, and its release downloads were not reachable.

## What SolidCraft offers an outside program

| Way in | State on 10 Nov 2026 | Usable by the toolbox? |
|---|---|---|
| **Plug-ins** (RFC 0002: sandboxed WebAssembly components, `run(command, args)`, importers, exporters, parametric features) | Draft. "Only the Preferences page exists today. No plugin runs." | Not yet |
| **Control channel** (TCP on 127.0.0.1, JSON lines, per-launch token, `engine.execute` any command) | Built, documented, used by its own MCP server | **Yes: used now** |
| **MCP server** (`solidcraft-cli mcp`, stdio JSON-RPC, bridges to the app) | Built | Possible, but needs `solidcraft-cli` found and run; the control channel is simpler |
| **Headless CLI scripts** (`solidcraft-cli run script.json --out part.step`) | Built | For batch use only, not a live link |

## Licences

- SolidCraft is MIT / Apache-2.0, and its rules forbid GPL/LGPL/AGPL code in its tree, naming OpenCASCADE.
  This toolbox's converter is built on OpenCASCADE (through OCP) and is PolyForm Noncommercial, so it **cannot
  be merged into SolidCraft**, and shouldn't be.
- A separate program that talks to SolidCraft over its documented control channel shares no code with it. The
  link (`tostep/frontends/solidcraft/link.py`) was written from the protocol description only; no SolidCraft
  code was copied.

## What was built

`tostep solidcraft` and two buttons in the app (take the selected mesh body; Send to SolidCraft), using three
commands: `selection.get`, `file.export_stl`, `file.insert_step`. See [the guide](../guide/solidcraft.md).
Tests: `tests/test_solidcraft.py` runs everything against a stand-in that follows the documented protocol,
including strict parameters (unknown keys refused) and a wrong token.

One finding from building the stand-in: closing a listening socket in Python does not wake a thread blocked in
`accept()`. The next server can get the same descriptor number, and the old thread then answers it with the
old token. The stand-in shuts the socket down before closing it. That was a bug in the test harness, not in the link.

## When SolidCraft's plug-ins arrive

A plug-in for SolidCraft's planned WebAssembly system could not hold this converter: OpenCASCADE is LGPL and
far over the RFC's 16 MiB component cap (a "real converter" there already measured 28–42 MB), and plug-ins get
no way to start programs. Two routes would remain:

1. **A thin plug-in** (a "Convert to solid with Twoo STEP" command) that sends the mesh to the toolbox's local
   web server and inserts the answer. It needs the `net` permission to a localhost origin, which is still an open
   question in the RFC (open question 4, "Local network").
2. **This link**, unchanged.

Worth asking on SolidCraft's issue #1221 whether a plug-in may reach a companion service on `127.0.0.1`, since
it decides route 1.

## Not done

- No run against a real SolidCraft. The places most likely to need a fix are listed in the guide.
- The link takes mesh bodies only. Taking a SolidCraft solid out to clean it with Jetwash and send it back would
  use `file.export_step` the same way; not built.
