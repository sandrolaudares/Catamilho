"""Suavizacao / interpolacao temporal da serie NDVI.

- Reamostragem para grade regular (dekad = 10 dias, ~36 obs/ano).
- Interpolacao linear com limite de gap (evita inventar meses chuvosos inteiros).
- Filtro Savitzky-Golay (janela impar, ordem <= janela-1) — padrao de facto para
  suavizar series fenologicas de NDVI (Chen et al. 2004, Jonsson & Eklundh).

O resultado suavizado NAO substitui a serie observada exibida no grafico;
serve como entrada estavel para o DTW e para as regras fenologicas quando o
usuario ativa modo `smoothed=True`.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
from scipy.signal import savgol_filter


def _to_days(dates):
    """Converte lista de ISO dates em dias desde a primeira data (inteiros)."""
    d0 = dt.date.fromisoformat(str(dates[0])[:10])
    return np.array([(dt.date.fromisoformat(str(d)[:10]) - d0).days
                     for d in dates], dtype=float), d0


def regularize(series, step_days: int = 10, max_gap_days: int = 40,
               sg_window: int = 7, sg_order: int = 2):
    """Reamostra + interpola + Savitzky-Golay.

    Args:
      series: [{date, ndvi}] observada, ja ordenada.
      step_days: passo da grade (10 = decadal; usar 5 para grade densa).
      max_gap_days: se o gap entre observacoes reais for maior, marca NaN
        na grade interpolada (o SG entao trabalha com pontos vizinhos).
      sg_window: janela do filtro (impar). 7 dekads = ~70 dias.
      sg_order: ordem do polinomio (<= sg_window - 1).

    Returns:
      dict com listas paralelas: `dates`, `ndvi_raw` (na grade — NaN se buraco),
      `ndvi_smooth` (suavizado), `n_pontos`, `params`.
    """
    if len(series) < 4:
        return {"dates": [], "ndvi_raw": [], "ndvi_smooth": [],
                "n_pontos": 0, "params": None,
                "aviso": "menos de 4 observacoes — suavizacao ignorada"}

    days, d0 = _to_days([s["date"] for s in series])
    y = np.array([float(s["ndvi"]) for s in series], dtype=float)
    # ordena por data (defensivo)
    order = np.argsort(days)
    days, y = days[order], y[order]

    grid = np.arange(0, int(days[-1]) + 1, step_days, dtype=float)
    interp = np.interp(grid, days, y)

    # invalida pontos da grade que caem em gaps grandes
    for i, g in enumerate(grid):
        prev = days[days <= g]
        nxt = days[days >= g]
        if prev.size and nxt.size:
            if (g - prev.max()) > max_gap_days or (nxt.min() - g) > max_gap_days:
                interp[i] = np.nan

    valid = ~np.isnan(interp)
    if valid.sum() < max(sg_window, 5):
        smooth = interp.copy()  # sem SG se houver poucos pontos validos
    else:
        # preenche NaNs por interpolacao para o SG rodar; guarda mascara
        tmp = interp.copy()
        idx = np.arange(len(tmp))
        tmp[~valid] = np.interp(idx[~valid], idx[valid], tmp[valid])
        w = min(sg_window, len(tmp) if len(tmp) % 2 else len(tmp) - 1)
        if w % 2 == 0:
            w -= 1
        w = max(5, w)
        o = min(sg_order, w - 1)
        smooth = savgol_filter(tmp, window_length=w, polyorder=o, mode="interp")
        smooth[~valid] = np.nan  # nao invente valor em gap real

    grid_dates = [(d0 + dt.timedelta(days=int(g))).isoformat() for g in grid]
    return {
        "dates": grid_dates,
        "ndvi_raw": [None if np.isnan(v) else round(float(v), 4) for v in interp],
        "ndvi_smooth": [None if np.isnan(v) else round(float(v), 4) for v in smooth],
        "n_pontos": int(valid.sum()),
        "params": {"step_days": step_days, "max_gap_days": max_gap_days,
                   "sg_window": sg_window, "sg_order": sg_order},
    }


def to_monthly(regularized: dict) -> dict:
    """Media mensal da serie suavizada (chave = mes inteiro 1..12)."""
    out = {m: [] for m in range(1, 13)}
    for d, v in zip(regularized["dates"], regularized["ndvi_smooth"]):
        if v is None:
            continue
        m = int(d[5:7])
        out[m].append(v)
    return {m: (float(np.mean(vs)) if vs else None) for m, vs in out.items()}
