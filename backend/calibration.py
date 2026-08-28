"""Calibracao de curvas de referencia a partir de talhoes conhecidos.

Fluxo:
1. Usuario desenha um poligono e marca a classe verdadeira (milho_safrinha,
   soja_unica, milho_1a_safra, pastagem, outra_lavoura).
2. Chamamos serie_ndvi() como no /api/analyze.
3. Suavizamos com Savitzky-Golay e extraimos media mensal.
4. Salvamos a curva em `data/calibracao.json` (persistencia simples em disco
   — no Fly a maquina hiberna; para persistencia dura, migrar p/ volume ou DB).
5. Ao chamar `get_calibrated_curves()`, retornamos a media das curvas
   calibradas por classe (fallback para as curvas de literatura).

Formato do arquivo:
{
  "amostras": [
    {"id": "...", "classe": "...", "safra": "2024/25", "municipio": "...",
     "geometry": {...GeoJSON...}, "mensal": [12 valores], "criado_em": "..."},
    ...
  ],
  "curvas": {"milho_safrinha": {"rotulo": "...", "mensal": [12], "n": 3},
             ...}
}
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid

import numpy as np

# curvas iniciais (mesmas do modulo stac_ndvi — mantidas em sync via bootstrap)
LITERATURA = {
    "milho_safrinha": {
        "rotulo": "Soja + milho safrinha (dupla safra, Medio Norte MT)",
        "mensal": [0.80, 0.45, 0.70, 0.85, 0.80, 0.50,
                   0.35, 0.30, 0.28, 0.35, 0.60, 0.82]},
    "soja_unica": {
        "rotulo": "Soja em safra unica (sem 2a safra)",
        "mensal": [0.80, 0.55, 0.35, 0.30, 0.28, 0.26,
                   0.25, 0.25, 0.26, 0.35, 0.60, 0.82]},
    "milho_1a_safra": {
        "rotulo": "Milho 1a safra (plantio out-nov, pico dez-jan)",
        "mensal": [0.75, 0.65, 0.45, 0.30, 0.28, 0.27,
                   0.26, 0.26, 0.28, 0.55, 0.78, 0.85]},
    "pastagem": {
        "rotulo": "Pastagem / cobertura perene",
        "mensal": [0.72, 0.74, 0.75, 0.70, 0.62, 0.55,
                   0.50, 0.47, 0.48, 0.55, 0.65, 0.70]},
    "algodao": {
        "rotulo": "Algodao (plantio dez-jan, pico mar-mai)",
        "mensal": [0.55, 0.70, 0.85, 0.90, 0.80, 0.55,
                   0.35, 0.30, 0.28, 0.30, 0.35, 0.45]},
}


def _store_path():
    return os.getenv("CALIB_STORE",
                     os.path.join(os.path.dirname(__file__), "data",
                                  "calibracao.json"))


def _load():
    p = _store_path()
    if not os.path.exists(p):
        return {"amostras": [], "curvas": {}}
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {"amostras": [], "curvas": {}}


def _save(store):
    p = _store_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(store, f, separators=(",", ":"), ensure_ascii=False)


def _mensal_from_series(series):
    buckets = {m: [] for m in range(1, 13)}
    for s in series:
        try:
            m = int(str(s["date"])[5:7])
            buckets[m].append(float(s["ndvi"]))
        except Exception:
            continue
    return [(float(np.mean(buckets[m])) if buckets[m] else None)
            for m in range(1, 13)]


def _recompute_curves(store):
    """Refaz store['curvas'] a partir das amostras (media por classe)."""
    by_classe = {}
    for a in store["amostras"]:
        by_classe.setdefault(a["classe"], []).append(a["mensal"])
    out = {}
    for classe, amostras in by_classe.items():
        arr = np.array([[np.nan if v is None else v for v in mensal]
                        for mensal in amostras], dtype=float)
        with np.errstate(invalid="ignore"):
            media = np.nanmean(arr, axis=0)
        # preenche meses sem dado com a literatura correspondente
        base = LITERATURA.get(classe, {}).get("mensal", [None] * 12)
        mensal = [round(float(media[i]), 3) if not np.isnan(media[i])
                  else (base[i] if base[i] is not None else 0.3)
                  for i in range(12)]
        out[classe] = {
            "rotulo": LITERATURA.get(classe, {}).get("rotulo", classe),
            "mensal": mensal,
            "n_amostras": len(amostras),
        }
    store["curvas"] = out
    return store


def add_sample(classe: str, geometry: dict, series: list[dict],
               safra: str | None = None, municipio: str | None = None,
               observacao: str | None = None) -> dict:
    """Adiciona uma amostra rotulada e recalcula a curva media da classe."""
    if classe not in LITERATURA:
        raise ValueError(f"classe desconhecida: {classe} "
                         f"(esperadas: {list(LITERATURA)})")
    if len(series) < 6:
        raise ValueError("minimo de 6 observacoes de NDVI para calibrar")
    mensal = _mensal_from_series(series)
    if sum(v is not None for v in mensal) < 6:
        raise ValueError("cobertura mensal insuficiente (< 6 meses distintos)")

    store = _load()
    sample = {
        "id": uuid.uuid4().hex[:12],
        "classe": classe,
        "safra": safra,
        "municipio": municipio,
        "observacao": observacao,
        "geometry": geometry,
        "mensal": mensal,
        "n_datas": len(series),
        "criado_em": dt.datetime.utcnow().isoformat() + "Z",
    }
    store["amostras"].append(sample)
    _recompute_curves(store)
    _save(store)
    return {"sample": sample, "curvas": store["curvas"]}


def list_samples() -> list[dict]:
    store = _load()
    return [{k: v for k, v in s.items() if k != "geometry"}
            for s in store["amostras"]]


def delete_sample(sample_id: str) -> bool:
    store = _load()
    n0 = len(store["amostras"])
    store["amostras"] = [s for s in store["amostras"] if s["id"] != sample_id]
    if len(store["amostras"]) == n0:
        return False
    _recompute_curves(store)
    _save(store)
    return True


def get_reference_curves() -> dict:
    """Curvas usadas pelo DTW / grafico: calibradas quando disponiveis,
    caso contrario literatura."""
    store = _load()
    out = {}
    for classe, base in LITERATURA.items():
        cal = store["curvas"].get(classe)
        if cal and cal.get("n_amostras", 0) >= 1:
            out[classe] = {**cal, "fonte": "calibrada"}
        else:
            out[classe] = {**base, "n_amostras": 0, "fonte": "literatura"}
    return out
