"""Pictures for the Printables / Thingiverse listing: 1600x1200 (4:3), a plain-English caption bar on each."""
import os, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

REPO = sys.argv[1]
OUT = sys.argv[2]
os.makedirs(OUT, exist_ok=True)
D = REPO + "/docs/images/"
RI = REPO + "/docs/research/images/"
F = "/usr/share/fonts/opentype/inter/Inter-"
def font(w, s): return ImageFont.truetype(F + w + ".otf", s)

W, H = 1600, 1200
BLUE = (43, 108, 235)
INK = (22, 27, 40)
GREY = (95, 102, 118)
BG = (246, 247, 250)


def fit(im, w, h):
    im = im.copy()
    im.thumbnail((w, h), Image.LANCZOS)
    return im


def card(src, title, sub, name, crop=None, bg=BG):
    im = Image.open(src).convert("RGB")
    if crop:
        im = im.crop(crop)
    c = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(c)
    d.rectangle((0, 0, W, 170), fill=BLUE)
    d.text((60, 34), title, font=font("ExtraBold", 58), fill="white")
    d.text((62, 110), sub, font=font("Regular", 30), fill=(222, 232, 255))
    im = fit(im, W - 100, H - 170 - 70)
    x = (W - im.width) // 2
    y = 170 + (H - 170 - im.height) // 2
    sh = Image.new("RGBA", (im.width + 40, im.height + 40), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rectangle((20, 24, im.width + 20, im.height + 24), fill=(0, 0, 0, 50))
    sh = sh.filter(ImageFilter.GaussianBlur(10))
    c.paste(sh, (x - 20, y - 20), sh)
    c.paste(im, (x, y))
    c.save(f"{OUT}/{name}", quality=90)


# 01 cover: the vase, mesh vs STEP
src = Image.open(RI + "parts-before-after.webp").convert("RGB")
mesh = src.crop((20, 115, 520, 470))
step = src.crop((1080, 115, 1580, 470))
c = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(c)
d.rectangle((0, 0, W, 300), fill=BLUE)
d.text((70, 52), "STL in. Real CAD out.", font=font("Black", 104), fill="white")
d.text((74, 192), "Turn printable meshes into smooth, editable STEP solids. Free, offline, no account.",
       font=font("Regular", 34), fill=(222, 232, 255))
mesh = mesh.resize((660, 467), Image.LANCZOS)
step = step.resize((660, 467), Image.LANCZOS)
c.paste(mesh, (60, 380))
c.paste(step, (880, 380))
# arrow
d.polygon([(770, 560), (860, 613), (770, 666)], fill=BLUE)
d.rectangle((720, 595, 775, 631), fill=BLUE)
d.text((60, 870), "Your STL", font=font("ExtraBold", 50), fill=INK)
d.text((60, 935), "24,146 little triangles", font=font("Regular", 36), fill=GREY)
d.text((880, 870), "Your STEP", font=font("ExtraBold", 50), fill=INK)
d.text((880, 935), "14 real surfaces you can edit", font=font("Regular", 36), fill=GREY)
d.rectangle((0, 1060, W, H), fill=(236, 240, 250))
d.text((70, 1098), "Twoo STEP CAD Toolbox", font=font("Bold", 40), fill=INK)
t = "Windows · Mac · Linux"
d.text((W - 70 - d.textlength(t, font=font("SemiBold", 34)), 1103), t, font=font("SemiBold", 34), fill=GREY)
c.save(f"{OUT}/01-cover.jpg", quality=92)

card(RI + "parts-before-after.webp", "Before and after",
     "Left: the STL you start with. Middle: the default way. Right: the newer Staged way.", "02-before-after.jpg")
card(RI + "ghost-before-after.webp", "Blobby shapes too",
     "Organic, sculpted shapes become smooth patches instead of a million facets.", "03-organic.jpg")
card(D + "preview.webp", "Peek before you download",
     "A 3D preview of the result. Blue = flat, green = round. Drag to spin it.", "04-preview.jpg",
     crop=(0, 60, 1300, 1000) if Image.open(D + "preview.webp").width >= 1300 else None)
card(RI + "ui-presets.jpg", "Pick a speed, press Convert",
     "Draft for quick jobs, Fine when it really matters. Standard is a good start.", "05-presets.jpg")
card(D + "convert-done.webp", "It checks its own homework",
     "Every result is re-read and measured against your mesh, so you know how close it is.", "06-done.jpg")
card(RI + "fig-scan-times.png", "Got a 3D scan? Much faster now",
     "A scanned soldering iron: from over three hours to under eight minutes.", "07-scans.jpg", bg="white")

# 08 more tools: four screenshots
tiles = [(D + "image-colours.webp", "Photo of a flat part → STEP"),
         (D + "scad-tab.webp", "OpenSCAD → STEP"),
         (D + "jetwash.webp", "Jetwash: tidy a messy STEP"),
         (D + "hardpoints-tab.webp", "Hardpoint STL")]
c = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(c)
d.rectangle((0, 0, W, 170), fill=BLUE)
d.text((60, 34), "More tools in the box", font=font("ExtraBold", 58), fill="white")
d.text((62, 110), "Same app, same one-folder download. No extra installs.", font=font("Regular", 30),
       fill=(222, 232, 255))
cw, ch = 760, 470
for i, (p, lab) in enumerate(tiles):
    x = 30 + (i % 2) * (cw + 20)
    y = 195 + (i // 2) * (ch + 15)
    d.rectangle((x, y, x + cw, y + ch), fill="white", outline=(220, 224, 232), width=2)
    d.text((x + 22, y + 14), lab, font=font("Bold", 34), fill=INK)
    im = Image.open(p).convert("RGB")
    im = im.crop((0, 0, im.width, min(im.height, int(im.width * 0.62))))
    im = fit(im, cw - 30, ch - 75)
    c.paste(im, (x + (cw - im.width) // 2, y + 64))
c.save(f"{OUT}/08-more-tools.jpg", quality=90)
print("ok")
