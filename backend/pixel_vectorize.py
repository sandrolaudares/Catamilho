"""Vetorizacao por ZONAS DE USO — CAR com usos mistos (milho + algodao + mata).

Pipeline (v0.9):
1. Constroi a pilha NDVI mensal (12 x H x W) da propriedade — cada pixel tem
   sua propria serie temporal (mesma leitura COG windowed de antes).
2. SEGMENTA a propriedade em zonas homogeneas de uso do solo com k-means
   sobre o vetor NDVI mensal (12 dimensoes) de cada pixel — separa milho,
   algodao e mata em regioes distintas ANTES de classificar.
3. CLASSIFICA CADA ZONA separadamente: media mensal da zona -> regras
   fenologicas (classify.classificar) + veto DTW (milho x algodao).
   A mata e separada da pastagem pela media anual de NDVI.
4. VETORIZA cada classe com sua cor/rotulo: milho, algodao, mata, pastagem,
   soja unica, pico de verao, outros.

Grade de referencia: bounds da propriedade na UTM do centroide, 10 m
(reduzida automaticamente — teto de 1024 px/lado).
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

import calibration
import dtw
from classify import LIMIARES_DEFAULT, classificar
from stac_ndvi import search_scenes

SCL_KEEP = (4, 5)
MAX_SIDE = 1024
WORKERS = 6
MAX_FEATURES = 600

# rotulo e cor por classe final (cor usada no frontend)
CLASSES = {
    "milho_safrinha": ("Milho safrinha", "#fbbf24"),
    "algodao": ("Algodão", "#f472b6"),
    "mata": ("Mata / vegetação nativa", "#166534"),
    "pastagem": ("Pastagem", "#a3e635"),
    "soja_unica": ("Soja (safra única)", "#2dd4bf"),
    "pico_verao": ("Pico de verão (milho 1ª safra / soja)", "#fb923c"),
    "outras": ("Outros usos / sem dados", "#9ca3af"),
}


# ---------------------------------------------------------------- grade/cenas
def _ref_grid(geom4326, max_side=MAX_SIDE):
    shp = shape(geom4326)
    lon, lat = shp.centroid.x, shp.centroid.y
    zone = int((lon + 180) // 6) + 1
    epsg = (32700 if lat < 0 else 32600) + zone
    crs = CRS.from_epsg(epsg)
    minx, miny, maxx, maxy = transform_bounds(CRS.from_epsg(4326), crs,
                                              *shp.bounds)
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
    """Le B04/B08/SCL da cena (janela do envoltorio) e reprojeta p/ a grade."""
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


# ------------------------------------------------------------- segmentacao
def _kmeans(X, k, iters=25, seed=42):
    """K-means simples (numpy, em blocos) sobre vetores (N, 12)."""
    rng = np.random.default_rng(seed)
    n = len(X)
    # k-means++ numa amostra
    samp = X[rng.choice(n, size=min(n, 10000), replace=False)]
    idx = [int(rng.integers(len(samp)))]
    for _ in range(k - 1):
        d = np.min(((samp[:, None, :] - samp[np.array(idx)][None]) ** 2)
                   .sum(-1), axis=1)
        idx.append(int(rng.choice(len(samp), p=d / d.sum())))
    C = samp[idx].copy()
    for _ in range(iters):
        lab = np.empty(n, dtype=np.int32)
        for i in range(0, n, 50000):
            blk = X[i:i + 50000]
            lab[i:i + 50000] = ((blk[:, None, :] - C[None]) ** 2).sum(-1).argmin(1)
        for j in range(k):
            m = lab == j
            if m.any():
                C[j] = X[m].mean(0)
    return lab


def _segmentar(mensal, prop, res):
    """Agrupa pixels da propriedade em zonas por serie NDVI mensal.
    Retorna (labels HxW int, mascara_validos HxW bool)."""
    n_meses_ok = (~np.isnan(mensal)).sum(axis=0)
    valid = prop & (n_meses_ok >= 6)
    n = int(valid.sum())
    if n < 50:
        return np.full(prop.shape, -1, dtype=np.int32), valid
    area_ha = n * res * res / 10000
    k = 3 if area_ha < 100 else 4 if area_ha < 300 else 5 if area_ha < 800 \
        else 6 if area_ha < 2000 else 7
    k = min(k, max(2, n // 200))

    X = mensal[:, valid].T.copy()  # (N, 12)
    colmean = np.nanmean(X, axis=0)
    colmean = np.where(np.isnan(colmean), 0.3, colmean)
    nanmask = np.isnan(X)
    X[nanmask] = np.take(colmean, np.where(nanmask)[1])

    lab = _kmeans(X, k)
    labels = np.full(prop.shape, -1, dtype=np.int32)
    labels[valid] = lab
    return labels, valid


# ------------------------------------------------------- classificacao/zona
def _serie_da_zona(mensal, zona_mask, start):
    """Serie temporal media da zona (12 pontos, dia 15 de cada mes)."""
    ano0 = int(str(start)[:4])
    out = []
    for m in range(1, 13):
        vals = mensal[m - 1][zona_mask]
        vals = vals[~np.isnan(vals)]
        if vals.size:
            y = ano0 if m >= 9 else ano0 + 1
            out.append({"date": f"{y}-{m:02d}-15", "ndvi": round(float(vals.mean()), 4)})
    return out


def _classificar_zona(serie, end, lim):
    """Regras + veto DTW por zona. Retorna (classe_final, confianca, detalhe)."""
    if len(serie) < 6:
        return "outras", 0.0, {"motivo": "serie curta"}
    r = classificar(serie, fim_serie=end, limiares=lim)
    classe = r["classe"]
    conf = r["confianca"]
    detalhe = {"regras": classe, "conf_regras": conf}
    try:
        d = dtw.compare_curves(serie, calibration.get_reference_curves())
        if d.get("ok"):
            best = d["melhor"]["classe"]
            margem = d.get("margem") or 0
            detalhe["dtw"] = best
            detalhe["margem_dtw"] = margem
            # veto milho x algodao (o caso do CAR multiuso)
            if (classe in ("milho_safrinha", "provavel_safrinha")
                    and best == "algodao" and margem >= 0.04):
                classe = "algodao"
                detalhe["correcao_dtw"] = True
    except Exception:
        pass
    # separa mata de pastagem dentro de "perene"
    if classe == "perene":
        media = float(np.nanmean([s["ndvi"] for s in serie]))
        classe = "mata" if media >= 0.55 else "pastagem"
        detalhe["media_anual"] = round(media, 3)
    if classe in ("inconclusivo", "dados_insuficientes"):
        classe = "outras"
    if classe == "provavel_safrinha":
        classe = "milho_safrinha"
    return classe, conf, detalhe


# -------------------------------------------------------------- vetorizacao
def _round_coords(o, nd=6):
    if isinstance(o, (list, tuple)):
        if o and isinstance(o[0], (int, float)):
            return [round(float(v), nd) for v in o]
        return [_round_coords(v, nd) for v in o]
    return o


def _vetorizar(mask_classe, classe, transform, crs, res, min_area_ha):
    min_px = max(2, int(min_area_ha * 10000 / (res * res)))
    mask_classe = (sieve(mask_classe.astype("uint8"), size=min_px,
                         connectivity=8) == 1)
    feats = []
    for g, v in shapes(mask_classe.astype("uint8"), mask=mask_classe,
                       transform=transform):
        g4326 = transform_geom(crs.to_string(), "EPSG:4326", g)
        area_ha = shape(g).area / 10000
        if area_ha < min_area_ha:
            continue
        gj = mapping(shape(g4326).simplify(0.0001, preserve_topology=True))
        gj["coordinates"] = _round_coords(gj["coordinates"])
        rotulo, cor = CLASSES.get(classe, CLASSES["outras"])
        feats.append({"type": "Feature", "geometry": gj,
                      "properties": {"classe": classe, "rotulo": rotulo,
                                     "cor": cor,
                                     "area_ha": round(area_ha, 1)}})
    feats.sort(key=lambda f: -f["properties"]["area_ha"])
    return feats, int(mask_classe.sum())


# -------------------------------------------------------------------- main
def vectorizar_milho(geom, start, end, cloud_max=70, max_scenes=60,
                     threshold=0.72, min_area_ha=2.0, limiares=None,
                     refinar="off", on_progress=None):
    """Segmenta por zonas de uso, classifica cada zona e vetoriza TODAS as
    culturas identificadas. Retorna (geojson, stats, mask_milho, transform, crs)."""
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

    # 2) segmentacao em zonas de uso
    labels, valid = _segmentar(mensal, prop, res)
    n_zonas = len(set(labels[valid].tolist())) if valid.any() else 0

    # 3) classifica cada zona (regras + DTW)
    zone_class = {}
    zone_detail = {}
    for z in sorted(set(labels[valid].tolist())) if valid.any() else []:
        zm = labels == z
        serie = _serie_da_zona(mensal, zm, start)
        classe, conf, det = _classificar_zona(serie, end, lim)
        zone_class[z] = classe
        zone_detail[z] = {**det, "pixels": int(zm.sum()), "conf": conf}

    # 4) vetoriza cada classe (milho primeiro; refino de fronteiras so p/ milho)
    feats = []
    stats_classes = []
    masks = {}
    for classe in ("milho_safrinha", "algodao", "soja_unica", "pico_verao",
                   "pastagem", "mata", "outras"):
        zs = [z for z, c in zone_class.items() if c == classe]
        if not zs:
            continue
        m = np.isin(labels, zs) & valid
        masks[classe] = m

    # refino de fronteiras do milho (SLIC/SAM) sobre o RGB do mes de pico
    info_refine = {"metodo": "off"}
    if refinar != "off" and "milho_safrinha" in masks:
        try:
            import sam_refine
            m_milho, info_refine = sam_refine.refinar(
                masks["milho_safrinha"].astype("uint8"), prop, transform, crs,
                geom, start, end, cloud_max, metodo=refinar)
            masks["milho_safrinha"] = (m_milho == 1) & valid
        except Exception as e:
            info_refine = {"metodo": "off", "motivo": str(e)}
    # milho refinado tem prioridade: demais classes nao podem sobrepoe-lo
    if "milho_safrinha" in masks:
        for c, m in masks.items():
            if c != "milho_safrinha":
                masks[c] = m & ~masks["milho_safrinha"]

    for classe, m in masks.items():
        fs, px = _vetorizar(m, classe, transform, crs, res, min_area_ha)
        feats.extend(fs)
        stats_classes.append({
            "classe": classe, "rotulo": CLASSES[classe][0],
            "cor": CLASSES[classe][1],
            "area_ha": round(px * res * res / 10000, 1),
            "n_poligonos": len(fs),
        })
    feats = feats[:MAX_FEATURES]

    px_total = int(prop.sum())
    area_total = px_total * res * res / 10000
    area_milho = next((c["area_ha"] for c in stats_classes
                       if c["classe"] == "milho_safrinha"), 0.0)
    mask_milho = masks.get("milho_safrinha",
                           np.zeros_like(prop)).astype("uint8")

    geojson = {"type": "FeatureCollection", "features": feats}
    stats = {
        "area_total_ha": round(area_total, 1),
        "area_milho_ha": area_milho,
        "pct_milho": round(100 * area_milho / area_total, 1) if area_total else 0,
        "pixels_total": px_total,
        "resolucao_m": res,
        "n_poligonos": len(feats),
        "n_zonas": n_zonas,
        "classes": stats_classes,
        "zonas_detalhe": {str(z): d for z, d in zone_detail.items()},
        "cenas_encontradas": total,
        "cenas_usadas": usadas,
        "limiares": lim,
        "refinamento": info_refine,
        "modo": "zonas_multiuso",
    }
    return geojson, stats, mask_milho, transform, crs
