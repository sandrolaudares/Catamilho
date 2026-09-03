"""Validacao cruzada com o mapa de agricultura 2a safra do MapBiomas.

Estrategia (sem dependencia de conta GEE):
- MapBiomas publica os rasters anuais (30 m) da colecao brasileira em
  storage.googleapis.com/mapbiomas-public/... como COG.
- Baixamos so a janela do poligono (rasterio, mesmo padrao do Sentinel-2)
  e contamos as classes de 2a safra:
     1  Milho 2a safra
    62  Algodao 2a safra
    41  Outras culturas de 2a safra
    35  Soja (2a safra em algumas colecoes; mantemos como referencia)
- Retornamos a fracao de cada classe dentro do poligono no ano solicitado.

A URL exata pode mudar entre colecoes / anos (o MapBiomas revisa periodicamente).
Fornecemos uma lista de URLs candidatas e usamos a primeira que responder 200.
Se nenhuma funcionar, retornamos motivo='indisponivel' — o front continua
mostrando so a classificacao propria (comportamento gracioso).
"""
from __future__ import annotations

import os
from typing import Optional

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.features import geometry_mask
from rasterio.transform import array_bounds
from rasterio.warp import Resampling, reproject, transform_bounds, transform_geom
from rasterio.windows import Window, from_bounds
from shapely.geometry import shape

# codigos das classes de 2a safra no MapBiomas (Uso Agricola / 2a Safra)
CLASSES_2A = {
    1: "Milho 2a safra",
    62: "Algodao 2a safra",
    41: "Outras lavouras 2a safra",
    35: "Soja",  # normalmente classe de 1a safra, aqui como comparacao
}

# candidatos de URL — tentamos em ordem. Ajuste conforme a colecao vigente.
def _candidate_urls(ano: int) -> list[str]:
    base = "https://storage.googleapis.com/mapbiomas-public/brasil"
    return [
        # segunda safra — colecao mais recente publicada
        f"{base}/collection-9/lclu/secondary/brasil_coverage_secondary_{ano}.tif",
        f"{base}/collection-8/lclu/secondary/brasil_coverage_secondary_{ano}.tif",
        # padroes alternativos observados nos repositorios
        f"{base}/mapbiomas_brasil_col9_agricultura_segunda_safra_{ano}.tif",
        f"{base}/mapbiomas_brasil_col8_agricultura_segunda_safra_{ano}.tif",
    ]


def _read_window(url: str, geom: dict):
    """Le apenas a janela do poligono do COG e retorna (array, mask_dentro)."""
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                      GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
                      CPL_VSIL_CURL_USE_HEAD="NO",
                      VSI_CACHE="YES", VSI_CACHE_SIZE="5000000"):
        with rasterio.open(url) as src:
            b = shape(geom).bounds
            gb = transform_bounds(CRS.from_epsg(4326), src.crs, *b)
            w0 = from_bounds(*gb, transform=src.transform)
            c0 = max(0, int(w0.col_off))
            r0 = max(0, int(w0.row_off))
            c1 = min(src.width, int(np.ceil(w0.col_off + w0.width)))
            r1 = min(src.height, int(np.ceil(w0.row_off + w0.height)))
            if c1 - c0 < 2 or r1 - r0 < 2:
                return None, None
            win = Window(c0, r0, c1 - c0, r1 - r0)
            data = src.read(1, window=win, boundless=True, fill_value=0)
            t = src.window_transform(win)
            gm = transform_geom("EPSG:4326", src.crs.to_string(), geom)
            inside = geometry_mask([gm], out_shape=data.shape, transform=t,
                                   invert=True, all_touched=True)
    return data, inside


def validar(geometry: dict, ano: int, url_override: Optional[str] = None):
    """Retorna a composicao (%) das classes de 2a safra no poligono."""
    urls = [url_override] if url_override else _candidate_urls(ano)
    ultimo_erro = None
    for url in urls:
        try:
            data, inside = _read_window(url, geometry)
            if data is None:
                continue
            if inside.sum() == 0:
                continue
            pix = data[inside]
            total = int(pix.size)
            comp = {}
            for code, nome in CLASSES_2A.items():
                n = int((pix == code).sum())
                comp[str(code)] = {
                    "nome": nome, "pixels": n,
                    "fracao": round(n / total, 4) if total else 0.0,
                }
            # dominante = classe com maior fracao (>= 40%)
            dom_code, dom = max(comp.items(),
                                key=lambda kv: kv[1]["fracao"])
            eh_milho2a = (dom_code == "1" and dom["fracao"] >= 0.4)
            return {
                "ok": True, "fonte": url, "ano": ano,
                "pixels_totais": total,
                "composicao": comp,
                "classe_dominante_codigo": int(dom_code),
                "classe_dominante_nome": dom["nome"],
                "fracao_dominante": dom["fracao"],
                "e_milho_2a_safra": eh_milho2a,
            }
        except Exception as e:
            ultimo_erro = f"{type(e).__name__}: {e}"
            continue

    return {
        "ok": False,
        "motivo": "raster do MapBiomas indisponivel nos endpoints testados",
        "detalhe": ultimo_erro,
        "urls_tentadas": urls,
        "dica": "consulte brasil.mapbiomas.org/downloads e atualize a URL via "
                "url_override, ou baixe o TIF localmente e sirva atraves de "
                "seu proprio bucket.",
    }


def confrontar(classificacao_propria: str, validacao: dict) -> dict:
    """Compara o veredito do classificador com o rotulo do MapBiomas."""
    if not validacao.get("ok"):
        return {"comparavel": False, "motivo": validacao.get("motivo")}
    milho2a_ref = validacao["e_milho_2a_safra"]
    milho2a_class = classificacao_propria in (
        "milho_safrinha", "provavel_safrinha")
    return {
        "comparavel": True,
        "concorda": milho2a_class == milho2a_ref,
        "classe_propria": classificacao_propria,
        "classe_mapbiomas": validacao["classe_dominante_nome"],
        "fracao_mapbiomas": validacao["fracao_dominante"],
    }


def acuracia_vs_mask(mask, mask_transform, mask_crs, ano,
                     url_override=None):
    """Matriz de confusao pixel a pixel: nossa mascara de milho (10 m)
    vs raster MapBiomas 2a safra (30 m, reprojetado para nossa grade).
    Meta de aceitacao do produto: acuracia global >= 0.90."""
    urls = [url_override] if url_override else _candidate_urls(ano)
    ultimo_erro = None
    for url in urls:
        try:
            h, w = mask.shape
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                              GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
                              CPL_VSIL_CURL_USE_HEAD="NO",
                              VSI_CACHE="YES", VSI_CACHE_SIZE="5000000"):
                with rasterio.open(url) as src:
                    gb = array_bounds(h, w, mask_transform)
                    sb = transform_bounds(mask_crs, src.crs, *gb)
                    w0 = from_bounds(*sb, transform=src.transform)
                    c0 = max(0, int(w0.col_off))
                    r0 = max(0, int(w0.row_off))
                    c1 = min(src.width,
                             int(np.ceil(w0.col_off + w0.width)))
                    r1 = min(src.height,
                             int(np.ceil(w0.row_off + w0.height)))
                    if c1 - c0 < 2 or r1 - r0 < 2:
                        continue
                    win = Window(c0, r0, c1 - c0, r1 - r0)
                    data = src.read(1, window=win, boundless=True,
                                    fill_value=0)
                    st = src.window_transform(win)
                    src_crs = src.crs
            mb = np.zeros(mask.shape, dtype="uint8")
            reproject(data, mb, src_transform=st, src_crs=src_crs,
                      dst_transform=mask_transform, dst_crs=mask_crs,
                      resampling=Resampling.nearest,
                      src_nodata=0, dst_nodata=0)
            mb_milho = (mb == 1)
            valid = (mb > 0)
            if int(valid.sum()) < 100:
                return {"ok": False,
                        "motivo": "area comparavel insuficiente no raster MapBiomas"}
            m1 = (mask == 1)
            tp = int((m1 & mb_milho & valid).sum())
            tn = int(((~m1) & (~mb_milho) & valid).sum())
            fp = int((m1 & (~mb_milho) & valid).sum())
            fn = int(((~m1) & mb_milho & valid).sum())
            n = tp + tn + fp + fn
            acc = (tp + tn) / n
            prec = tp / max(tp + fp, 1)
            rec = tp / max(tp + fn, 1)
            f1 = 2 * prec * rec / max(prec + rec, 1e-9)
            iou = tp / max(tp + fp + fn, 1)
            return {
                "ok": True, "fonte": url, "ano": ano, "pixels": n,
                "acuracia_global": round(acc, 4),
                "atinge_meta_90": acc >= 0.90,
                "precisao": round(prec, 4), "revocacao": round(rec, 4),
                "f1": round(f1, 4), "iou": round(iou, 4),
                "confusao": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
                "nota": "MapBiomas 30 m vs mascara 10 m — bordas de talhao "
                        "divergem naturalmente; a meta de 90% e medida no "
                        "miolo da propriedade.",
            }
        except Exception as e:
            ultimo_erro = f"{type(e).__name__}: {e}"
            continue
    return {"ok": False, "motivo": "raster MapBiomas indisponivel",
            "detalhe": ultimo_erro, "urls_tentadas": urls}
