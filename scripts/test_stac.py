#!/usr/bin/env python3
"""Smoke test do pipeline STAC -> COG assinado (Planetary Computer).

Valida: busca de cenas Sentinel-2 L2A sobre Sorriso-MT, assinatura de URL
e leitura de cabecalho do COG (HEAD request).
"""
import json
import urllib.request

import planetary_computer
from pystac_client import Client

CATALOG = "https://planetarycomputer.microsoft.com/api/stac/v1"

# talhao de ~4 km2 na regiao de Sorriso-MT
GEOM = {
    "type": "Polygon",
    "coordinates": [[
        [-55.7400, -12.5600], [-55.7000, -12.5600],
        [-55.7000, -12.5400], [-55.7400, -12.5400],
        [-55.7400, -12.5600],
    ]],
}

client = Client.open(CATALOG)
search = client.search(
    collections=["sentinel-2-l2a"],
    intersects=GEOM,
    datetime="2026-02-01/2026-04-30",
    query={"eo:cloud_cover": {"lt": 70}},
    max_items=100,
)
items = list(search.items())
print(f"cenas encontradas (fev-abr/2026, nuvem<70%): {len(items)}")
for it in items[:8]:
    print(" ", it.datetime.date(), f"cloud={it.properties.get('eo:cloud_cover'):.0f}%",
          it.properties.get("s2:mgrs_tile"))

assert items, "nenhuma cena encontrada"
it = planetary_computer.sign(items[0])
href = it.assets["B04"].href
print("B04 assinado:", href[:110], "...")

req = urllib.request.Request(href, method="HEAD")
with urllib.request.urlopen(req, timeout=30) as r:
    print("HEAD status:", r.status, "| bytes:", r.headers.get("Content-Length"))

print("assets disponiveis:", sorted(k for k in it.assets if k in
      ("B02", "B03", "B04", "B08", "SCL", "visual")))
print("OK")
