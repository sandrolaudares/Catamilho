#!/usr/bin/env python3
"""Gera icons/icon-192.png e icons/icon-512.png (Pillow)."""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "frontend", "icons")
os.makedirs(OUT, exist_ok=True)


def icon(size):
    img = Image.new("RGB", (size, size), "#14532d")
    d = ImageDraw.Draw(img)
    u = size / 100.0
    d.rounded_rectangle([4 * u, 4 * u, 96 * u, 96 * u], radius=18 * u, fill="#14532d")
    # espiga estilizada: grao amarelo + folhas verdes
    d.ellipse([38 * u, 26 * u, 62 * u, 74 * u], fill="#fbbf24")
    d.polygon([(38 * u, 70 * u), (22 * u, 92 * u), (44 * u, 84 * u)], fill="#4ade80")
    d.polygon([(62 * u, 70 * u), (78 * u, 92 * u), (56 * u, 84 * u)], fill="#22c55e")
    for yy in (34, 44, 54, 64):  # linhas dos graos
        d.line([(40 * u, yy * u), (60 * u, yy * u)], fill="#b45309", width=max(1, int(u)))
    return img


for s in (192, 512):
    icon(s).save(os.path.join(OUT, f"icon-{s}.png"))
    print(f"icon-{s}.png ok")
