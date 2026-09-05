"""Classificacao PIXEL A PIXEL + vetorizacao do milho dentro de uma propriedade.

Diferente de stac_ndvi.serie_ndvi (media zonal), aqui cada pixel recebe sua
propria serie temporal de NDVI e e classificado com as MESMAS 6 regras
fenologicas ponderadas do classificador zonal (classify.py). A mascara binaria
de milho e peneirada (sieve) e vetorizada em poligonos GeoJSON.

Grade de referencia: bounds da propriedade na UTM do centroide, resolucao 10 m
(reduzida automaticamente para propriedades grandes — teto de 1024 px/lado).
Cenas de tiles MGRS diferentes sao reaproveitadas via reproject (WRS diferente
nao quebra o empilhamento).

A identidade "milho" vem da ASSINATURA TEMPORAL, nao da forma — SAM entra numa
etapa futura apenas como refinador de fronteiras (object-based post-processing).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import planetary_computer
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.features import geometry_mask, shapes, sieve
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds, transform_geom
from rasterio.windows import Window, from_bounds as win_from_bounds
from shapely.geometry import mapping, shape

from classify import LIMIARES_DEFAULT
from stac_ndvi import search_scenes

SCL_KEEP = (4, 5)
W_VIGOR, W_OUTONO, W_VALE, W_SOJA, W_SENESC, W_AMPL = 3, 1, 2, 1, 2, 1
PESO_MIN = 5
MAX_SIDE = 1024
WORKERS = 6
MAX_FEATURES = 500


def _ref_grid(geom4326, max_side=MAX_SIDE):
    shp = shape(geom4326)
    lon, lat = shp.centroid.x, shp.centroid.y
    zone = int((lon + 180) // 6) + 1
    epsg = (32700 if lat < 0 else 32600) + zone
    crs = CRS.from_epsg(epsg)
    minx, miny, maxx, maxy = transform_bounds(
        CRS.from_epsg(4326), crs, *shp.bounds)
    res = 10.0
    w = int(np.ceil((maxx - minx) / res))
    h = int(np.ceil((maxy - miny) / res))
    scale = max(1, int(np.ceil(max(w, h) / max_side)))
    res *= scale
    w = int(np.ceil((maxx - minx) / res))
    h = int(np.ceil((maxy - miny) / res))
    transform = from_bounds(minx, miny, maxx, maxy, w, h)
    return crs, transform, (h, w), res, (minx, miny, maxx, maxy)


def _scene_to_grid(item, crs, transform, out_hw, grid_bounds):
    """Le B04/B08/SCL da cena (janela do envoltorio) e reprojeta p/ a grade.
    Retorna (mes 1..12, ndvi HxW float32 com NaN) ou None."""
    try:
        it = planetary_computer.sign(item)
        assets = it.assets
        if not all(k in assets for k in ("B04", "B08", "SCL")):
            return None
        H, W = out_hw
        baseline = str(item.properties.get("s2:processing_baseline", "04.00"))
        off = 1000.0 if baseline >= "04.00" else 0.0
        bands = {}
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                          GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
                          CPL_VSIL_CURL_USE_HEAD="NO",
                          VSI_CACHE="YES", VSI_CACHE_SIZE="5000000"):
            for key, resamp in (("B04", Resampling.bilinear),
                                ("B08", Resampling.bilinear),
                                ("SCL", Resampling.nearest)):
                with rasterio.open(assets[key].href) as src:
                    sb = transform_bounds(crs, src.crs, *grid_bounds)
                    win = win_from_bounds(*sb, transform=src.transform)
                    win = win.round_offsets().round_lengths()
                    c0 = max(0, int(win.col_off))
                    r0 = max(0, int(win.row_off))
                    c1 = min(src.width, c0 + int(win.width))
                    r1 = min(src.height, r0 + int(win.height))
                    if c1 - c0 < 2 or r1 - r0 < 2:
                        return None
                    factor = max(1, int(np.ceil(
                        max(c1 - c0, r1 - r0) / (max(W, H) * 1.5))))
                    sw = max(2, int(np.ceil((c1 - c0) / factor)))
                    sh = max(2, int(np.ceil((r1 - r0) / factor)))
                    wsel = Window(c0, r0, c1 - c0, r1 - r0)
                    data = src.read(1, window=wsel, out_shape=(sh, sw),
                                    resampling=resamp)
                    src_t = src.window_transform(wsel) * Affine.scale(
                        (c1 - c0) / sw, (r1 - r0) / sh)
                    dst = np.zeros((H, W), dtype="float32")
                    reproject(data, dst, src_transform=src_t, src_crs=src.crs,
                              dst_transform=transform, dst_crs=crs,
                              resampling=resamp, src_nodata=0, dst_nodata=0)
                    bands[key] = dst
        r = bands["B04"] - off
        n = bands["B08"] - off
        okm = ((bands["B04"] > 0) & (bands["B08"] > 0)
               & np.isin(bands["SCL"].astype("uint8"), SCL_KEEP)
               & ((n + r) > 200))
        with np.errstate(invalid="ignore", divide="ignore"):
            nd = (n - r) / (n + r)
        nd = np.where(okm & (nd > -0.2) & (nd < 0.98), nd, np.nan)
        return item.datetime.month, nd.astype("float32")
    except Exception:
        return None


def _round_coords(o, nd=6):
    if isinstance(o, (list, tuple)):
        if o and isinstance(o[0], (int, float)):
            return [round(float(v), nd) for v in o]
        return [_round_coords(v, nd) for v in o]
    return o


def vectorizar_milho(geom, start, end, cloud_max=70, max_scenes=60,
                     threshold=0.72, min_area_ha=2.0, limiares=None,
                     refinar="off", on_progress=None):
    """Retorna (geojson, stats, mask, transform, crs)."""
    lim = dict(LIMIARES_DEFAULT)
    if limiares:
        for k, v in limiares.items():
            if k in lim and v is not None:
                try:
                    lim[k] = float(v)
                except (TypeError, ValueError):
                    pass
    crs, transform, out_hw, res, grid_bounds = _ref_grid(geom)
    H, W = out_hw
    prop = geometry_mask([transform_geom("EPSG:4326", crs.to_string(), geom)],
                         out_shape=out_hw, transform=transform,
                         invert=True, all_touched=True)
    items = search_scenes(geom, start, end, cloud_max)
    total = len(items)
    if total > max_scenes:
        step = int(np.ceil(total / max_scenes))
        items = items[::step]

    soma = np.zeros((12, H, W), dtype="float32")
    conta = np.zeros((12, H, W), dtype="float32")
    usadas = 0
    _done = 0
    _tot = len(items)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_scene_to_grid, it, crs, transform, out_hw,
                          grid_bounds) for it in items]
        for f in as_completed(futs):
            _done += 1
            if on_progress:
                try:
                    on_progress(_done, _tot)
                except Exception:
                    pass
            r = f.result()
            if r is None:
                continue
            m, nd = r
            val = ~np.isnan(nd)
            soma[m - 1][val] += nd[val]
            conta[m - 1][val] += 1
            usadas += 1
    if usadas < 4:
        raise ValueError(f"poucas cenas validas ({usadas}) — amplie o periodo "
                         "ou eleve o limite de nuvens")
    with np.errstate(invalid="ignore"):
        mensal = np.where(conta > 0, soma / np.maximum(conta, 1e-9), np.nan)
    n_meses = (~np.isnan(mensal)).sum(axis=0)
    nan = np.isnan

    def vmax(*ms):
        return np.fmax.reduce(mensal[[m - 1 for m in ms]], axis=0)

    def vmin(*ms):
        return np.fmin.reduce(mensal[[m - 1 for m in ms]], axis=0)

    def vmean(*ms):
        sub = mensal[[m - 1 for m in ms]]
        s = np.nansum(sub, axis=0)
        c = (~np.isnan(sub)).sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(c > 0, s / np.maximum(c, 1), np.nan)

    pico_soja = vmax(11, 12, 1)
    vale = vmin(1, 2)
    pico_milho = vmax(3, 4, 5)
    pos_pico = vmax(6, 7, 8)
    fim = vmean(6, 7)
    amplitude = (np.fmax.reduce(mensal, axis=0)
                 - np.fmin.reduce(mensal, axis=0))

    d_vigor = ~nan(pico_milho)
    o_vigor = pico_milho >= lim["vigor_min"]
    d_out = ~(nan(pico_milho) | nan(pos_pico))
    o_out = (pico_milho - pos_pico) >= lim["outono_min"]
    d_vale = ~(nan(vale) | nan(pico_soja))
    o_vale = ((pico_soja - vale) >= lim["vale_queda_min"]) & (vale <= lim["vale_max"])
    d_soja = ~nan(pico_soja)
    o_soja = pico_soja >= lim["soja_min"]
    d_sen = ~(nan(fim) | nan(pico_milho))
    o_sen = (pico_milho - fim) >= lim["senesc_min"]
    d_amp = (n_meses >= 4) & ~nan(amplitude)
    o_amp = amplitude >= lim["ampl_min"]

    num = (W_VIGOR * (d_vigor & o_vigor) + W_OUTONO * (d_out & o_out)
           + W_VALE * (d_vale & o_vale) + W_SOJA * (d_soja & o_soja)
           + W_SENESC * (d_sen & o_sen) + W_AMPL * (d_amp & o_amp))
    den = (W_VIGOR * d_vigor + W_OUTONO * d_out + W_VALE * d_vale
           + W_SOJA * d_soja + W_SENESC * d_sen + W_AMPL * d_amp)
    with np.errstate(invalid="ignore", divide="ignore"):
        score = np.where(den >= PESO_MIN,
                         num.astype("float32") / np.maximum(den, 1e-9),
                         np.nan)

    mask = ((score >= threshold) & prop).astype("uint8")
    min_px = max(2, int(min_area_ha * 10000 / (res * res)))
    mask = (sieve(mask, size=min_px, connectivity=8) == 1).astype("uint8")

    # refinamento de fronteiras (SLIC ou SAM) sobre o RGB do mes de pico
    info_refine = {"metodo": "off"}
    if refinar != "off":
        try:
            import sam_refine
            mask, info_refine = sam_refine.refinar(
                mask, prop, transform, crs, geom, start, end,
                cloud_max, metodo=refinar)
            mask = (sieve(mask, size=min_px, connectivity=8) == 1).astype("uint8")
        except Exception as e:
            info_refine = {"metodo": "off", "motivo": str(e)}

    px_total = int(prop.sum())
    px_milho = int((mask == 1).sum())
    area_total = px_total * res * res / 10000
    area_milho = px_milho * res * res / 10000

    feats = []
    for g, v in shapes(mask, mask=mask == 1, transform=transform):
        g4326 = transform_geom(crs.to_string(), "EPSG:4326", g)
        area_ha = shape(g).area / 10000
        if area_ha < min_area_ha:
            continue
        gj = mapping(shape(g4326).simplify(0.0001, preserve_topology=True))
        gj["coordinates"] = _round_coords(gj["coordinates"])
        feats.append({"type": "Feature", "geometry": gj,
                      "properties": {"classe": "milho_safrinha",
                                     "area_ha": round(area_ha, 1)}})
    feats.sort(key=lambda f: -f["properties"]["area_ha"])
    feats = feats[:MAX_FEATURES]

    geojson = {"type": "FeatureCollection", "features": feats}
    sc = score[prop & ~nan(score)]
    stats = {
        "area_total_ha": round(area_total, 1),
        "area_milho_ha": round(area_milho, 1),
        "pct_milho": round(100 * area_milho / area_total, 1) if area_total else 0,
        "pixels_total": px_total,
        "pixels_milho": px_milho,
        "resolucao_m": res,
        "n_poligonos": len(feats),
        "cenas_encontradas": total,
        "cenas_usadas": usadas,
        "score_medio": round(float(np.mean(sc)), 3) if sc.size else None,
        "limiares": lim,
        "refinamento": info_refine,
    }
    return geojson, stats, mask, transform, crs
