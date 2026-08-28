#!/usr/bin/env python3
"""Testes locais dos 3 novos modulos: smoothing, dtw, calibration.
NAO chama STAC — usa curvas sinteticas."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ["CALIB_STORE"] = tempfile.mktemp(suffix=".json")
import calibration  # noqa: E402
import dtw  # noqa: E402
import smoothing  # noqa: E402


def mk(month_ndvi, year0=2025):
    out = []
    for m, v in month_ndvi.items():
        y = year0 if m >= 9 else year0 + 1
        for day in (5, 15, 25):
            out.append({"date": f"{y}-{m:02d}-{day:02d}", "ndvi": v})
    return out


SAFRINHA = mk({9: .28, 10: .30, 11: .60, 12: .82, 1: .80, 2: .42,
               3: .72, 4: .86, 5: .80, 6: .55, 7: .38, 8: .30})
MILHO1 = mk({9: .30, 10: .55, 11: .78, 12: .85, 1: .75, 2: .45,
             3: .30, 4: .28, 5: .27, 6: .26, 7: .26, 8: .28})
SOJA = mk({9: .28, 10: .32, 11: .62, 12: .84, 1: .82, 2: .50,
           3: .34, 4: .30, 5: .28, 6: .27, 7: .26, 8: .26})

# 1) smoothing
sm = smoothing.regularize(SAFRINHA, step_days=10, sg_window=7, sg_order=2)
print(f"[smoothing] pontos={sm['n_pontos']} datas={len(sm['dates'])} params={sm['params']}")
assert sm["n_pontos"] > 20, "smoothing devolveu poucos pontos"

# 2) DTW — safrinha deve casar melhor com safrinha, milho 1a com milho 1a
refs = calibration.LITERATURA
r_saf = dtw.compare_curves(SAFRINHA, refs)
r_m1 = dtw.compare_curves(MILHO1, refs)
r_soj = dtw.compare_curves(SOJA, refs)
print(f"[dtw] safrinha -> {r_saf['melhor']['classe']} (sim {r_saf['melhor']['similaridade']}, margem {r_saf['margem']})")
print(f"[dtw] milho1a  -> {r_m1['melhor']['classe']} (sim {r_m1['melhor']['similaridade']}, margem {r_m1['margem']})")
print(f"[dtw] soja     -> {r_soj['melhor']['classe']} (sim {r_soj['melhor']['similaridade']}, margem {r_soj['margem']})")
ok_dtw = (r_saf["melhor"]["classe"] == "milho_safrinha"
          and r_m1["melhor"]["classe"] == "milho_1a_safra"
          and r_soj["melhor"]["classe"] == "soja_unica")
print("[dtw]", "PASS" if ok_dtw else "FAIL")

# 3) calibration
geom = {"type": "Polygon",
        "coordinates": [[[-55.7, -12.5], [-55.6, -12.5], [-55.6, -12.4], [-55.7, -12.4], [-55.7, -12.5]]]}
out = calibration.add_sample("milho_safrinha", geom, SAFRINHA,
                             safra="2025/26", municipio="Sorriso")
print(f"[calib] amostra salva: id={out['sample']['id']} classe=milho_safrinha")
print(f"[calib] curva calibrada n={out['curvas']['milho_safrinha']['n_amostras']}")
curvas = calibration.get_reference_curves()
print(f"[calib] fontes: {[(k, v['fonte']) for k, v in curvas.items()]}")
print("[calib] PASS" if curvas["milho_safrinha"]["fonte"] == "calibrada" else "FAIL")
