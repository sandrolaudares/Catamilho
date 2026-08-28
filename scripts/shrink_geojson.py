#!/usr/bin/env python3
"""Valida e compacta os GeoJSONs do IBGE (arredonda coordenadas).

Gera em frontend/data/:
  - mt.geojson                  (limite do estado, para trava de desenho)
  - sorriso.geojson
  - lucas-do-rio-verde.geojson
  - primavera-do-leste.geojson  (presets de zoom)
"""
import json, os, sys

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(os.path.dirname(__file__), "..", "frontend", "data")
os.makedirs(OUT, exist_ok=True)

MUN = {
    "5107925": "sorriso",
    "5105259": "lucas-do-rio-verde",
    "5107040": "primavera-do-leste",
}


def round_coords(obj, nd):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(v), nd) for v in obj]
        return [round_coords(v, nd) for v in obj]
    return obj


def load(path):
    with open(path) as f:
        txt = f.read().strip()
    if not txt.startswith("{"):
        raise ValueError(f"{path} nao parece GeoJSON: {txt[:120]}")
    gj = json.loads(txt)
    if gj.get("type") != "FeatureCollection" or not gj.get("features"):
        raise ValueError(f"{path}: FeatureCollection vazia/invalida")
    return gj


def save(gj, path, nd=4):
    for feat in gj["features"]:
        feat["geometry"]["coordinates"] = round_coords(
            feat["geometry"]["coordinates"], nd)
        feat["properties"] = {
            k: v for k, v in (feat.get("properties") or {}).items()
            if k in ("nome", "codarea", "codigo_ibge", "name")}
    with open(path, "w") as f:
        json.dump(gj, f, separators=(",", ":"))
    return os.path.getsize(path)


# --- estado do MT ---
mt = load(os.path.join(DATA, "mt_raw.json"))
size = save(mt, os.path.join(OUT, "mt.geojson"), nd=4)
if size > 1_800_000:  # se pesado, reduz precisao (11 m -> 111 m)
    size = save(mt, os.path.join(OUT, "mt.geojson"), nd=3)
print(f"mt.geojson: {size/1024:.0f} KB, features={len(mt['features'])}")

# --- municipios foco ---
ok = 0
for code, slug in MUN.items():
    try:
        gj = load(os.path.join(DATA, f"mun_{code}.json"))
        size = save(gj, os.path.join(OUT, f"{slug}.geojson"), nd=4)
        print(f"{slug}.geojson: {size/1024:.0f} KB, features={len(gj['features'])}")
        ok += 1
    except Exception as e:
        print(f"ERRO municipio {code}: {e}", file=sys.stderr)
print("OK" if ok == 3 else "FALHOU")
