# Security

The toolbox's web page listens only on `127.0.0.1` with a per-session token and sends nothing over the network after setup.
If you find a security problem, please report it **privately** through GitHub: *Security → Report a vulnerability* on this repository. Please do not open a public issue for it.

Things worth knowing:
- **Plug-ins are code that runs with your rights.** Every plug-in in `tostep/plugins/` and every folder on `TOSTEP_PLUGIN_PATH` is Python that runs as you, like any program you install. There is **no sandbox**. The registry reads manifests as data and skips a broken plug-in, but it cannot tell good code from bad: add plug-ins only from people you trust. The toolbox never downloads plug-ins itself.
- **Worker processes** run as `python -E -s -u -m tostep.core.worker` from the toolbox folder, so environment variables and your personal Python folders are not read into a job.
- The *SCAD → STEP* tab runs OpenSCAD on the model you choose, and an OpenSCAD model is a program (it can `include` other files and `import` files from disk). Convert only models you trust, as you would only open them in OpenSCAD.
- Choosing a file by path reads that path from your own computer. The page is reachable only from this computer, with the session token.
- The **FreeCAD workbench and Fusion add-in** start the toolbox as a command (`run_tostep.py …`) in the folder you point them at, with the arguments the buttons build. Point them at a toolbox folder you trust: whatever is in it runs. They have never been audited in the real programs.
- The preview's *Diagnostic* panel shows a QR code with a job's settings and result. It is a picture on the page and is sent nowhere. It holds the version, the time, the settings, the result numbers and which of Linux / Windows / macOS it ran on; it does not hold the file's name, any path, the log or the error text (the panel shows the file name and error text beside it, marked "not in the code", so crop them out of a screenshot if they are private).

This is a work-in-progress, as-is project with no warranty; fixes are best-effort.
