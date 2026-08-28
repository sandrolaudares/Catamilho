"""DTW (Dynamic Time Warping) — comparacao serie observada vs curvas de referencia.

Implementacao propria com janela de Sakoe-Chiba (padrao) para restringir o
deslocamento maximo — evita casar picos absurdamente fora de fase. Sem
dependencia externa (dtw-python/dtaidistance) para manter a imagem leve.

Curvas de referencia sao vetores mensais (12 valores). Redistribuimos a serie
observada em 12 medias mensais interpoladas e comparamos.

Retorna, para cada classe candidata:
  - distancia DTW normalizada (menor = mais parecida)
  - similaridade em [0..1] = 1 / (1 + dist)
  - deslocamento em meses (offset positivo = ciclo atrasado)
"""
from __future__ import annotations

import numpy as np


def _dtw_distance(a: np.ndarray, b: np.ndarray, window: int = 3):
    """DTW com banda de Sakoe-Chiba. Retorna (dist, offset_medio)."""
    n, m = len(a), len(b)
    inf = float("inf")
    cost = np.full((n + 1, m + 1), inf)
    cost[0, 0] = 0.0
    # matriz para rastrear alinhamento (para offset)
    path = np.zeros((n + 1, m + 1, 2), dtype=int)

    for i in range(1, n + 1):
        j0 = max(1, i - window)
        j1 = min(m, i + window)
        for j in range(j0, j1 + 1):
            d = abs(a[i - 1] - b[j - 1])
            choices = (cost[i - 1, j - 1], cost[i - 1, j], cost[i, j - 1])
            k = int(np.argmin(choices))
            cost[i, j] = d + choices[k]
            path[i, j] = ((i - 1, j - 1), (i - 1, j), (i, j - 1))[k]

    dist = cost[n, m] / max(n, m)

    # reconstroi caminho para calcular offset medio (i-j)
    i, j = n, m
    offsets = []
    while i > 0 and j > 0:
        offsets.append(i - j)
        i, j = path[i, j]
    off = float(np.mean(offsets)) if offsets else 0.0
    return float(dist), off


def _monthly_from_series(series):
    """Converte [{date, ndvi}] em vetor mensal (12,) com media; None -> np.nan."""
    buckets = {m: [] for m in range(1, 13)}
    for s in series:
        try:
            m = int(str(s["date"])[5:7])
            buckets[m].append(float(s["ndvi"]))
        except Exception:
            continue
    vec = np.array([np.mean(buckets[m]) if buckets[m] else np.nan
                    for m in range(1, 13)], dtype=float)
    # preenche vazios por interpolacao circular (o ano e ciclico)
    if np.isnan(vec).all():
        return None
    if np.isnan(vec).any():
        idx = np.arange(12)
        good = ~np.isnan(vec)
        # duplica valores para lidar com bordas de ano (extensao ciclica)
        ext_idx = np.concatenate([idx[good] - 12, idx[good], idx[good] + 12])
        ext_val = np.concatenate([vec[good], vec[good], vec[good]])
        vec = np.interp(idx, ext_idx, ext_val)
    return vec


def compare_curves(series, references: dict, window: int = 3):
    """Compara serie observada contra dict {classe: {rotulo, mensal:[12]}}."""
    obs = _monthly_from_series(series)
    if obs is None:
        return {"ok": False, "erro": "serie sem valores validos"}

    resultados = []
    for classe, ref in references.items():
        vec_ref = np.array(ref["mensal"], dtype=float)
        if len(vec_ref) != 12:
            continue
        dist, offset = _dtw_distance(obs, vec_ref, window=window)
        sim = 1.0 / (1.0 + dist)
        resultados.append({
            "classe": classe,
            "rotulo": ref.get("rotulo", classe),
            "distancia": round(dist, 4),
            "similaridade": round(sim, 4),
            "offset_meses": round(offset, 2),
        })

    resultados.sort(key=lambda x: x["distancia"])
    best = resultados[0]
    # margem: quao melhor o 1o eh vs o 2o (>0.10 = decisao confortavel)
    margem = None
    if len(resultados) >= 2:
        margem = round(resultados[1]["distancia"] - best["distancia"], 4)

    return {
        "ok": True,
        "ranking": resultados,
        "melhor": best,
        "margem": margem,
        "obs_mensal": [round(float(v), 3) for v in obs],
    }
