# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Make the PDF of a research paper in docs/research: the Markdown in the project's paper style, an
orange accent and a faint STL-style triangle mesh behind every page.

    python3 tools/build_paper_pdf.py docs/research/SOLID_EVIDENCE.md      (writes SOLID_EVIDENCE.pdf beside it)

A tool for the author, not part of the app: needs pandoc 2.19 or newer, a Chromium (or Chrome) and the pypdf package.
Pictures in the Markdown are embedded, so the paper may use images/ next to it (and ../images/).
The text is printed by Chromium with normal page margins, the mesh as one full-bleed page, and the mesh
is laid under every text page with pypdf, so it runs to the paper's edge on every page.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ORANGE, DARK_ORANGE = "#F26B1D", "#C4520E"

CSS = """
@page { size: A4; margin: 18mm; }
html, body { -webkit-print-color-adjust: exact; print-color-adjust: exact; background: transparent; }
body { font-family: "DejaVu Serif", Georgia, serif; font-size: 10.2pt; line-height: 1.42; color: #1b1b1b; }
h1 { font-size: 32pt; text-align: center; margin: 2mm 0 2pt; letter-spacing: .5pt; }
h1 u { text-decoration-color: ORANGE; text-decoration-thickness: 3pt; text-underline-offset: 4pt; color: ORANGE; }
h1 + h3 { text-align: center; font-weight: normal; font-style: italic; font-size: 12pt; margin-top: 0; color: #444; }
h1 + h3::before { content: none !important; }
h2 { font-size: 14pt; color: DARK; border-bottom: 1.6pt solid ORANGE; padding-bottom: 2pt; margin-top: 18pt;
     page-break-after: avoid; }
h3 { font-size: 11.5pt; margin-top: 14pt; page-break-after: avoid; }
h3::before { content: "\\25B2  "; color: ORANGE; font-size: 8.5pt; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.6pt; background: #fdeee4; padding: 0 2pt;
       border-radius: 2pt; }
table { border-collapse: collapse; font-size: 8.6pt; margin: 6pt 0; width: 100%; page-break-inside: avoid;
        background: rgba(255,255,255,.94); }
th, td { border: .6pt solid #d9a27f; padding: 3pt 4pt; vertical-align: top; }
th { background: ORANGE; color: #fff; }
tr:nth-child(even) td { background: #fff5ee; }
hr { border: 0; border-top: 1pt solid ORANGE; margin: 12pt 0; opacity: .6; }
a { color: DARK; text-decoration: none; }
p, li { orphans: 3; widows: 3; }
p > img { display: block; margin: 8pt auto 2pt; max-width: 100%; max-height: 118mm; border: .6pt solid #e3c3ad;
          background: #fff; border-radius: 2pt; }
p:has(> img) { page-break-inside: avoid; margin-bottom: 0; }
p:has(> img) + p { font-size: 8.4pt; color: #444; text-align: center; margin: 2pt 6mm 10pt; page-break-before: avoid; }
""".replace("DARK", DARK_ORANGE).replace("ORANGE", ORANGE)


def mesh_svg(W=210.0, H=297.0, seed=11):
    """A jittered grid, triangulated and flat-shaded like a lit STL; strong in the margins, very faint
    behind the text column."""
    from scipy.spatial import Delaunay
    rng = np.random.default_rng(seed)
    xs, ys = np.meshgrid(np.linspace(-8, W + 8, 15), np.linspace(-8, H + 8, 21))
    P = np.c_[xs.ravel(), ys.ravel()] + rng.uniform(-5, 5, (15 * 21, 2))
    Z = 9 * np.sin(P[:, 0] / 37.0) * np.cos(P[:, 1] / 53.0) + 5 * np.sin((P[:, 0] + P[:, 1]) / 29.0)
    light = np.array([-0.4, -0.6, 0.7]); light /= np.linalg.norm(light)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%gmm" height="%gmm" viewBox="0 0 %g %g">' % (W, H, W, H)]
    for t in Delaunay(P).simplices:
        a, b, c = (np.r_[P[i], Z[i]] for i in t)
        n = np.cross(b - a, c - a); n /= (np.linalg.norm(n) or 1.0)
        n = -n if n[2] < 0 else n
        shade = 0.55 + 0.45 * float(n @ light)
        m = P[t].mean(0)
        r = max(abs(m[0] - W / 2) / (W / 2), abs(m[1] - H / 2) / (H / 2))
        fade = min(1.0, max(0.0, (r - 0.70) / 0.30)) ** 1.4
        g = int(200 + 40 * shade)
        out.append('<polygon points="%s" fill="rgb(%d,%d,%d)" fill-opacity="%.3f" stroke="%s" '
                   'stroke-opacity="%.3f" stroke-width="0.25" stroke-linejoin="round"/>'
                   % (" ".join("%.2f,%.2f" % (x, y) for x, y in P[t]), g, g, g,
                      0.025 + 0.32 * fade, ORANGE, 0.05 + 0.50 * fade))
    out.append("</svg>")
    return "\n".join(out)


def chromium():
    import glob
    found = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"))
                   + glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))
    for c in (os.environ.get("CHROME"), "chromium", "chromium-browser", "google-chrome",
              "/opt/pw-browsers/chromium-1194/chrome-linux/chrome", *found):
        if c and (shutil.which(c) or os.path.isfile(c)):
            return shutil.which(c) or c
    raise SystemExit("No Chromium or Chrome found (set CHROME=/path/to/chrome).")


def print_pdf(html, pdf):
    subprocess.run([chromium(), "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    "--print-to-pdf=" + pdf, "file://" + html], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(md):
    from pypdf import PdfReader, PdfWriter
    md = os.path.abspath(md)
    out = os.path.splitext(md)[0] + ".pdf"
    tmp = tempfile.mkdtemp(prefix="paper_pdf_")
    try:
        with open(os.path.join(tmp, "style.css"), "w") as fh:
            fh.write(CSS)
        html = os.path.join(tmp, "paper.html")
        title = os.path.splitext(os.path.basename(md))[0].replace("_", " ")
        subprocess.run(["pandoc", md, "-f", "gfm", "-t", "html5", "-s", "--metadata", "title=" + title,
                        "-c", "style.css", "--embed-resources", "--resource-path", os.path.dirname(md) + os.pathsep + tmp,
                        "-o", html], check=True, cwd=os.path.dirname(md))
        s = open(html, encoding="utf-8").read()
        s = re.sub(r'<header id="title-block-header">.*?</header>', "", s, flags=re.S)
        open(html, "w", encoding="utf-8").write(s)
        print_pdf(html, os.path.join(tmp, "text.pdf"))
        with open(os.path.join(tmp, "bg.svg"), "w") as fh:
            fh.write(mesh_svg())
        bg_html = os.path.join(tmp, "bg.html")
        with open(bg_html, "w") as fh:
            fh.write('<!doctype html><style>@page{size:A4;margin:0}html,body{margin:0}'
                     'img{display:block;width:210mm;height:297mm}</style><img src="bg.svg">')
        print_pdf(bg_html, os.path.join(tmp, "bg.pdf"))
        bg = PdfReader(os.path.join(tmp, "bg.pdf")).pages[0]
        w = PdfWriter()
        for page in PdfReader(os.path.join(tmp, "text.pdf")).pages:
            page.merge_page(bg, over=False)          # the mesh under the text
            w.add_page(page)
        for page in w.pages:
            page.compress_content_streams()          # (the mesh is in every page's own stream)
        try:
            w.compress_identical_objects(remove_identicals=True, remove_orphans=True)
        except (AttributeError, TypeError):
            pass
        w.add_metadata({"/Title": title, "/Author": "Matthew Armstrong"})
        with open(out, "wb") as fh:
            w.write(fh)
        print("Done:", out, "(%d pages)" % len(w.pages))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
