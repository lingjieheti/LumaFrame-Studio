"""Render the original LumaFrame optical study and application icon."""

from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

out = Path(__file__).resolve().parents[1] / "assets"
out.mkdir(exist_ok=True)
w, h = 1600, 560
y, x = np.mgrid[0:h, 0:w].astype(float)
x, y = x / w, y / h
rgb = np.zeros((h, w, 3)) + [9, 14, 33]


def light(mask, color):
    global rgb
    rgb += mask[:, :, None] * np.array(color)


light(np.exp(-(((x - 0.85) / 0.43) ** 2) - ((y - 0.34) / 0.7) ** 2), [32, 40, 77])
light(np.exp(-(((x - 0.58) / 0.25) ** 2) - ((y - 0.95) / 0.42) ** 2), [48, 23, 61])
for i in range(6):
    curve = (
        0.64 + 0.43 * np.sin(5.1 * x + 1.5 + i * 0.09) + 0.105 * np.sin(15 * x + i * 0.19) + 0.065 * i
    )
    d = y - curve
    facet = np.exp(-((np.maximum(d, 0) / (0.17 + 0.008 * i)) ** 2)) * (d > 0)
    fade = np.clip((x - 0.16) * 1.5, 0, 1)
    warm = np.exp(-(((x - 0.73 + i * 0.009) / 0.22) ** 2))
    for j, (cold, hot) in enumerate(zip([13, 28, 42], [55, 19, 12])):
        rgb[:, :, j] += (
            fade * facet * (cold * (1 - warm) + hot * warm) * (0.7 + 0.3 * np.sin(x * 12 + i))
        )
    light(fade * np.exp(-((d / 0.004) ** 2)) * (0.31 + 0.14 * np.cos(10 * x + i)), [64, 95, 118])
# A folded optical ribbon: a narrow highlight follows the edge while the
# thicker body refracts progressively from cool cyan to coral.
edge = 0.54 + 0.38 * np.sin(5.0 * x + 1.50) + 0.13 * np.sin(12.5 * x - 0.50)
distance = y - edge
envelope = np.exp(-(((x - 0.755) / 0.28) ** 4))
for band in range(4):
    d = distance - 0.097 * band
    glow = envelope * np.exp(-np.abs(d) / (0.032 + 0.014 * band))
    cyan = np.exp(-(((x - 0.57 - 0.025 * band) / 0.18) ** 2))
    amber = np.exp(-(((x - 0.82 + 0.016 * band) / 0.16) ** 2))
    light(glow * cyan, [9, 72, 98])
    light(glow * amber, [110, 41, 21])
    light(
        envelope * np.exp(-((d / 0.0048) ** 2)) * (0.29 + 0.41 * cyan + 0.38 * amber), [117, 138, 151]
    )
light(np.exp(-(((x - 0.86) / 0.27) ** 2) - ((y - 0.12) / 0.66) ** 2), [35, 14, 34])
rgb = 255 * (1 - np.exp(-rgb / 160))
rgb += np.random.default_rng(37).normal(0, 0.72, (h, w, 1))
img = Image.fromarray(np.uint8(np.clip(rgb, 0, 255)))
img.save(out / "spectrum.jpg", quality=94, subsampling=0, optimize=True)

im = Image.new("RGBA", (256, 256))
px = im.load()
for yy in range(256):
    for xx in range(256):
        u, v = xx / 255, yy / 255
        near = np.exp(-((u - 0.14) ** 2 + (v - 0.03) ** 2) / 0.3)
        px[xx, yy] = (int(37 + 82 * near + 17 * v), int(47 + 75 * near), int(102 + 77 * near), 255)
m = Image.new("L", im.size)
dm = ImageDraw.Draw(m)
dm.rounded_rectangle((3, 3, 253, 253), radius=58, fill=255)
im.putalpha(m)
draw = ImageDraw.Draw(im)
for i, color in [(2, (144, 222, 250, 255)), (1, (206, 206, 255, 255)), (0, (255, 255, 255, 255))]:
    dx, dy = i * 6, i * 16
    draw.polygon(
        [(51 + dx, 99 + dy), (135 + dx, 60 + dy), (200 + dx, 91 + dy), (116 + dx, 131 + dy)], fill=color
    )
im.save(out / "icon.png")
im.save(
    out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
)
