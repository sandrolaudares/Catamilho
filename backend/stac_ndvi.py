"""Serie temporal NDVI via STAC (Planetary Computer) + leitura parcial de COG.

So le as janelas (windows) das bandas B04/B08/SCL dentro do poligono —
nunca baixa a cena inteira. Offset harmonizado do Sentinel-2 (baseline >= 4.0)
e corrigido antes do NDVI, pois a razao nao e invariante a offset.
"""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import planetary_computer
import rasterio
from pystac_client import Client
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from rasterio.warp import transform_bounds, transform_geom
from rasterio.windows import Window, from_bounds
from shapely.geometry import shape

CATALOG = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-2-l2a"
SCL_KEEP = (4, 5)       # 4 = vegetacao, 5 = solo descoberto (ciclo do talhao)
READ_SIZE = 256         # limite de pixels por lado na leitura (downsample)
MIN_PIXELS = 8          # minimo de pixels validos p/ aceitar uma data
WORKERS = 8

# Curvas mensais (jan..dez) de referencia — sistema soja -> milho safrinha
# no Medio Norte do MT (Sorriso / Lucas do Rio Verde / Primavera do Leste).
REFERENCE_CURVES = {
    "milho_safrinha": {
        "rotulo": "Soja + milho safrinha (dupla safra, Medio Norte MT)",
        "mensal": [0.80, 0.45, 0.70, 0.85, 0.80, 0.50,
                   0.35, 0.30, 0.28, 0.35, 0.60, 0.82],
    },
    "soja_unica": {
        "rotulo": "Soja em safra unica (sem 2a safra)",
        "mensal": [0.80, 0.55, 0.35, 0.30, 0.28, 0.26,
                   0.25, 0.25, 0.26, 0.35, 0.60, 0.82],
    },
    "pastagem": {
        "rotulo": "Pastagem / cobertura perene",
        "mensal": [0.72, 0.74, 0.75, 0.70, 0.62, 0.55,
                   0.50, 0.47, 0.48, 0.55, 0.65, 0.70],
    },
}


def _geom_bounds(geom):
    return shape(geom).bounds  # (minx, miny, maxx, maxy) em EPSG:4326


def search_scenes(geom, start, end, cloud_max):
    client = Client.open(CATALOG)
    search = client.search(
        collections=[COLLECTION],
        intersects=geom,
        datetime=f"{start}/{end}",
        query={"eo:cloud_cover": {"lt": cloud_max}},
        max_items=500,
    )
    items = list(search.items())
    # uma cena por dia: a de menor cobertura de nuvem
    by_date = {}
    for it in items:
        d = it.datetime.date().isoformat()
        cc = it.properties.get("eo:cloud_cover", 100)
        if d not in by_date or cc < by_date[d].properties.get("eo:cloud_cover", 100):
            by_date[d] = it
    return sorted(by_date.values(), key=lambda i: i.datetime)


def _scene_point(item, geom):
    """NDVI medio do poligono numa cena. Retorna dict ou None."""
    try:
        it = planetary_computer.sign(item)
        assets = it.assets
        if not all(k in assets for k in ("B04", "B08", "SCL")):
            return None
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                          GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
                          CPL_VSIL_CURL_USE_HEAD="NO",
                          VSI_CACHE="YES", VSI_CACHE_SIZE="5000000"):
            with rasterio.open(assets["B04"].href) as src:
                crs = src.crs
                gb = transform_bounds(CRS.from_epsg(4326), crs, *_geom_bounds(geom))
                w0 = from_bounds(*gb, transform=src.transform)
                c0 = max(0, int(w0.col_off))
                r0 = max(0, int(w0.row_off))
                c1 = min(src.width, int(np.ceil(w0.col_off + w0.width)))
                r1 = min(src.height, int(np.ceil(w0.row_off + w0.height)))
                if c1 - c0 < 2 or r1 - r0 < 2:
                    return None
                win = Window(c0, r0, c1 - c0, r1 - r0)
                h, w = win.height, win.width
                s = max(1, int(np.ceil(max(h, w) / READ_SIZE)))
                out = (max(1, int(h // s)), max(1, int(w // s)))
                red = src.read(1, window=win, out_shape=out,
                               boundless=True, fill_value=0).astype("float32")
                t0 = src.window_transform(win)
            with rasterio.open(assets["B08"].href) as src8:
                nir = src8.read(1, window=win, out_shape=out,
                                boundless=True, fill_value=0).astype("float32")
            with rasterio.open(assets["SCL"].href) as ss:
                w2 = from_bounds(*gb, transform=ss.transform)
                scl = ss.read(1, window=w2, out_shape=out,
                              resampling=Resampling.nearest,
                              boundless=True, fill_value=0)
        t_out = t0 * Affine.scale(w / out[1], h / out[0])
        gm = transform_geom("EPSG:4326", crs.to_string(), geom)
        inpoly = geometry_mask([gm], out_shape=out, transform=t_out,
                               invert=True, all_touched=True)
        baseline = str(item.properties.get("s2:processing_baseline", "04.00"))
        off = 1000.0 if baseline >= "04.00" else 0.0
        r = red - off
        n = nir - off
        okm = (inpoly & (red > 0) & (nir > 0)
               & np.isin(scl, SCL_KEEP) & ((n + r) > 200))
        if int(okm.sum()) < MIN_PIXELS:
            return None
        with np.errstate(invalid="ignore", divide="ignore"):
            nd = (n[okm] - r[okm]) / (n[okm] + r[okm])
        v = float(np.mean(nd))
        if not np.isfinite(v) or v < -0.2 or v > 0.98:
            return None
        return {
            "date": item.datetime.date().isoformat(),
            "ndvi": round(v, 4),
            "pixels": int(okm.sum()),
            "cloud": round(float(item.properties.get("eo:cloud_cover", 0)), 1),
            "tile": item.properties.get("s2:mgrs_tile"),
        }
    except Exception:
        return None


def serie_ndvi(geom, start, end, cloud_max=70, max_scenes=140):
    """Retorna (series, meta). series = [{date, ndvi, pixels, cloud, tile}]."""
    items = search_scenes(geom, start, end, cloud_max)
    total = len(items)
    if total > max_scenes:  # amostra uniforme para respeitar o teto
        step = int(np.ceil(total / max_scenes))
        items = items[::step]
    out = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_scene_point, it, geom): it for it in items}
        for f in as_completed(futs):
            p = f.result()
            if p:
                out.append(p)
    out.sort(key=lambda d: d["date"])
    meta = {
        "cenas_encontradas": total,
        "cenas_processadas": len(items),
        "datas_validas": len(out),
        "periodo": [start, end],
        "cloud_max": cloud_max,
        "fonte": "Sentinel-2 L2A · Planetary Computer (STAC/COG) · 10 m",
    }
    return out, meta
