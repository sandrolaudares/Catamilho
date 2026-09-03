#!/usr/bin/env python3
"""Extracao de features de fotos amostradas de milho x algodao (base p/ v0.6).

Uso:
    amostras/
      milho/   *.jpg|png   (fotos de campo/drone de talhoes de milho)
      algodao/ *.jpg|png   (fotos de algodao)
      outras/  *.jpg|png   (opcional: soja, palhada, solo...)

    python scripts/extrair_amostras.py amostras/ -> amostras/features.csv

Features por imagem (RGB, robustas a iluminacao):
  - medias/desvios R, G, B
  - ExG  = 2G - R - B        (excesso de verde — vigor/tipo de dossel)
  - VARI = (G-R)/(G+R-B)     (indice verde atmosfericamente robusto)
  - GLI  = (2G-R-B)/(2G+R+B) (green leaf index)
  - textura: contraste/homogeneidade GLCM (se scikit-image presente)
  - razao de pixels "verdes" (G dominante) vs "amarelos" (R~G>B)
    — algodao em pre-colheita tem dossel mais amarelado/aberto que milho.

Esse CSV vira o dataset de treino do classificador milho x algodao por
foto (v0.6) e complementa as curvas NDVI calibradas por poligono.
"""
from __future__ import annotations

import csv
import os
import sys

import numpy as np
from PIL import Image

EXT = (".jpg", ".jpeg", ".png", ".webp")


def features(path):
    img = Image.open(path).convert("RGB")
    img.thumbnail((512, 512))
    a = np.asarray(img).astype("float32") / 255.0
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    eps = 1e-6
    exg = 2 * g - r - b
    vari = (g - r) / (g + r - b + eps)
    gli = (2 * g - r - b) / (2 * g + r + b + eps)
    verde = ((g > r) & (g > b)).mean()
    amarelo = ((abs(r - g) < 0.08) & (r > b + 0.05)).mean()
    out = {
        "r_mean": r.mean(), "g_mean": g.mean(), "b_mean": b.mean(),
        "r_std": r.std(), "g_std": g.std(), "b_std": b.std(),
        "exg_mean": exg.mean(), "vari_mean": vari.mean(), "gli_mean": gli.mean(),
        "px_verde": verde, "px_amarelo": amarelo,
    }
    try:
        from skimage.feature import graycomatrix, graycoprops
        from skimage.color import rgb2gray
        gray = (rgb2gray(a) * 63).astype("uint8")
        glcm = graycomatrix(gray, [1], [0], levels=64, normed=True)
        out["glcm_contrast"] = graycoprops(glcm, "contrast")[0, 0]
        out["glcm_homog"] = graycoprops(glcm, "homogeneity")[0, 0]
    except Exception:
        out["glcm_contrast"] = ""
        out["glcm_homog"] = ""
    return {k: (round(float(v), 5) if isinstance(v, (int, float, np.floating))
                else v) for k, v in out.items()}


def main(root="amostras"):
    rows = []
    for classe in sorted(os.listdir(root)):
        d = os.path.join(root, classe)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(EXT):
                continue
            p = os.path.join(d, fn)
            try:
                f = features(p)
                f["classe"] = classe
                f["arquivo"] = fn
                rows.append(f)
                print(f"[ok] {classe}/{fn}")
            except Exception as e:
                print(f"[erro] {classe}/{fn}: {e}")
    if not rows:
        print("nenhuma imagem encontrada — crie amostras/milho/ e amostras/algodao/")
        return
    out = os.path.join(root, "features.csv")
    keys = ["classe", "arquivo"] + [k for k in rows[0] if k not in ("classe", "arquivo")]
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} amostras -> {out}")
    # resumo rapido da separabilidade
    for classe in sorted({r['classe'] for r in rows}):
        sub = [r for r in rows if r['classe'] == classe]
        print(f"  {classe}: n={len(sub)}  exg={np.mean([r['exg_mean'] for r in sub]):.3f}  "
              f"amarelo={np.mean([r['px_amarelo'] for r in sub]):.3f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "amostras")
