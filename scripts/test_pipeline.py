#!/usr/bin/env python3
"""Teste do pipeline: (1) classificador com curvas sinteticas; (2) leitura real
de NDVI via STAC/COG num talhao de Sorriso-MT (janela curta, poucas cenas)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from classify import classificar  # noqa: E402

# ---- (1) curvas sinteticas ----
def mk(month_ndvi, year0=2025):
    """month_ndvi: {mes: ndvi} cobrindo nov/25..ago/26"""
    out = []
    for m, v in month_ndvi.items():
        y = year0 if m >= 9 else year0 + 1
        out.append({"date": f"{y}-{m:02d}-15", "ndvi": v})
    return out

SAFRINHA = mk({9: .28, 10: .30, 11: .60, 12: .82, 1: .80, 2: .42,
               3: .72, 4: .86, 5: .80, 6: .55, 7: .38, 8: .30})
PASTAGEM = mk({m: .60 + (0.08 if m in (12, 1, 2, 3) else 0) for m in
               (9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8)})
SOJA_UNICA = mk({9: .28, 10: .32, 11: .62, 12: .84, 1: .82, 2: .50,
                 3: .34, 4: .30, 5: .28, 6: .27, 7: .26, 8: .26})

r1 = classificar(SAFRINHA, fim_serie="2026-08-15")
print(f"[sintetico] dupla safra -> {r1['classe']} (conf {r1['confianca']}) "
      f"esperado=milho_safrinha  {'PASS' if r1['classe']=='milho_safrinha' else 'FAIL'}")
for rr in r1["regras"]:
    print("   ", "✔" if rr["atendida"] else ("✖" if rr["atendida"] is False else "○"),
          rr["nome"])

r2 = classificar(PASTAGEM, fim_serie="2026-08-15")
print(f"[sintetico] pastagem   -> {r2['classe']} (conf {r2['confianca']})  "
      f"{'PASS' if r2['classe'] in ('perene','inconclusivo') else 'FAIL'}")

r3 = classificar(SOJA_UNICA, fim_serie="2026-08-15")
print(f"[sintetico] soja unica -> {r3['classe']} (conf {r3['confianca']})  "
      f"{'PASS' if r3['classe'] in ('soja_unica','pico_verao','inconclusivo') else 'FAIL'}")

# ---- (2) leitura real STAC/COG ----
print("\n[stac] lendo NDVI real em talhao de Sorriso (mar-mai/2026)...")
from stac_ndvi import serie_ndvi  # noqa: E402

GEOM = {"type": "Polygon", "coordinates": [[
    [-55.7400, -12.5600], [-55.7000, -12.5600],
    [-55.7000, -12.5400], [-55.7400, -12.5400],
    [-55.7400, -12.5600]]]}

series, meta = serie_ndvi(GEOM, "2026-03-01", "2026-05-31", cloud_max=80, max_scenes=10)
print("[stac] meta:", meta)
for s in series:
    print(f"   {s['date']}  NDVI={s['ndvi']:.3f}  px={s['pixels']}  nuvem={s['cloud']}%")
ok = len(series) >= 3 and all(0.1 <= s["ndvi"] <= 0.95 for s in series)
print("[stac]", "PASS" if ok else "FAIL", f"({len(series)} datas)")
