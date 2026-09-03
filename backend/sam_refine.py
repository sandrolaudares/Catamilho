"""Refinamento de fronteiras da mascara de milho.

Metodos:
- "slic"  : superpixels SLIC sobre o RGB do mes de pico (mar-mai) + voto de
            maioria por segmento. Leve, sem modelo, roda na VM de 1 GB.
            Funciona como um "SAM geometrico": as fronteiras da mascara passam
            a seguir os contornos visiveis da imagem (object-based refinement).
- "sam"   : Segment Anything (ONNX) com prompts de ponto amostrados da mascara.
            Ativa so se existirem backend/models/sam_encoder.onnx +
            sam_decoder.onnx — requer VM com >= 2 GB (onnxruntime).
            Mesmo padrao ONNX do projeto bauxita-sam.
- "auto"  : tenta SAM; se indisponivel, cai para SLIC.
- "off"   : sem refinamento.

O RGB de referencia e a cena com menos nuvens no pico do milho (mar-mai),
reprojetada para a mesma grade da classificacao (10 m).
"""
from __future__ import annotations

import os

import numpy as np
import planetary_computer
import rasterio
from affine import Affine
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import Window, from_bounds as win_from_bounds

from stac_ndvi import search_scenes

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")


def _rgb_peak_scene(geom, start, end, cloud_max, crs, transform, out_hw):
    """RGB (H,W,3 uint8) da cena menos nublada no pico do milho (mar-mai)."""
    H, W = out_hw
    items = [it for it in search_scenes(geom, start, end, cloud_max)
             if it.datetime.month in (3, 4, 5)]
    if not items:
        items = search_scenes(geom, start, end, cloud_max)
    if not items:
        raise RuntimeError("nenhuma cena disponivel para o RGB de referencia")
    items.sort(key=lambda it: it.properties.get("eo:cloud_cover", 100))
    item = planetary_computer.sign(items[0])
    from rasterio.transform import array_bounds
    gb = array_bounds(H, W, transform)
    rgb = np.zeros((H, W, 3), dtype="uint8")
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                      GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
                      CPL_VSIL_CURL_USE_HEAD="NO",
                      VSI_CACHE="YES", VSI_CACHE_SIZE="5000000"):
        for i, key in enumerate(("B04", "B03", "B02")):
            with rasterio.open(item.assets[key].href) as src:
                sb = transform_bounds(crs, src.crs, *gb)
                win = win_from_bounds(*sb, transform=src.transform)
                win = win.round_offsets().round_lengths()
                c0 = max(0, int(win.col_off)); r0 = max(0, int(win.row_off))
                c1 = min(src.width, c0 + int(win.width))
                r1 = min(src.height, r0 + int(win.height))
                if c1 - c0 < 2 or r1 - r0 < 2:
                    raise RuntimeError("janela RGB vazia")
                factor = max(1, int(np.ceil(max(c1 - c0, r1 - r0)
                                            / (max(W, H) * 1.5))))
                sw = max(2, int(np.ceil((c1 - c0) / factor)))
                sh = max(2, int(np.ceil((r1 - r0) / factor)))
                wsel = Window(c0, r0, c1 - c0, r1 - r0)
                data = src.read(1, window=wsel, out_shape=(sh, sw),
                                resampling=Resampling.bilinear).astype("float32")
                src_t = src.window_transform(wsel) * Affine.scale(
                    (c1 - c0) / sw, (r1 - r0) / sh)
                dst = np.zeros((H, W), dtype="float32")
                reproject(data, dst, src_transform=src_t, src_crs=src.crs,
                          dst_transform=transform, dst_crs=crs,
                          resampling=Resampling.bilinear,
                          src_nodata=0, dst_nodata=0)
                # estiramento 2-98% p/ 0..255
                vals = dst[dst > 0]
                if vals.size < 100:
                    continue
                lo, hi = np.percentile(vals, (2, 98))
                band = np.clip((dst - lo) / max(hi - lo, 1e-6), 0, 1)
                rgb[:, :, i] = (band * 255).astype("uint8")
    data_date = items[0].datetime.date().isoformat()
    return rgb, data_date


def _slic_refine(mask, prop, rgb, compactness=10.0):
    from skimage.segmentation import slic
    valid = rgb.sum(axis=2) > 0
    n_pix = int((mask == 1).sum())
    n_seg = int(np.clip(n_pix // 40, 60, 800))
    seg = slic(rgb, n_segments=n_seg, compactness=compactness,
               start_label=0, channel_axis=-1,
               mask=valid, enforce_connectivity=True)
    out = np.zeros_like(mask)
    for s in np.unique(seg):
        m = (seg == s) & prop
        if not m.any():
            continue
        frac = float(mask[m].mean())
        out[seg == s] = 1 if frac >= 0.5 else 0
    return (out & prop).astype("uint8"), {"n_segmentos": int(len(np.unique(seg))),
                                          "compactness": compactness}


def _sam_refine(mask, prop, rgb):
    enc = os.path.join(MODELS_DIR, "sam_encoder.onnx")
    dec = os.path.join(MODELS_DIR, "sam_decoder.onnx")
    if not (os.path.exists(enc) and os.path.exists(dec)):
        raise RuntimeError("modelos SAM ONNX ausentes em backend/models/")
    import onnxruntime as ort
    from rasterio.features import sieve  # noqa: F401  (garantia)
    H, W = mask.shape
    img = np.ascontiguousarray(rgb.transpose(2, 0, 1)).astype("float32")
    mean = np.array([123.675, 116.28, 103.53])[:, None, None]
    std = np.array([58.395, 57.12, 57.375])[:, None, None]
    x = (img - mean) / std
    x = x[None]  # 1x3xHxW — o encoder espera 1024x1024; redimensionamos
    from skimage.transform import resize as _rz
    x = _rz(x[0].transpose(1, 2, 0), (1024, 1024), anti_aliasing=True)
    x = x.transpose(2, 0, 1)[None].astype("float32")
    sess_e = ort.InferenceSession(enc, providers=["CPUExecutionProvider"])
    emb = sess_e.run(None, {sess_e.get_inputs()[0].name: x})[0]
    sess_d = ort.InferenceSession(dec, providers=["CPUExecutionProvider"])

    # prompts: grade de pontos dentro da mascara
    ys, xs = np.where(mask == 1)
    if len(ys) == 0:
        return mask, {"prompts": 0}
    step = max(1, len(ys) // 64)
    pts = np.stack([xs[::step], ys[::step]], axis=1).astype("float32")
    pts = pts * np.array([1024 / W, 1024 / H], dtype="float32")
    labels = np.ones((len(pts),), dtype="float32")

    acc = np.zeros((1024, 1024), dtype="float32")
    for i in range(0, len(pts), 16):  # lotes de 16 pontos
        p = pts[i:i + 16]
        lb = labels[i:i + 16]
        inputs = {
            "image_embeddings": emb,
            "point_coords": p[None],
            "point_labels": lb[None],
            "mask_input": np.zeros((1, 1, 256, 256), dtype="float32"),
            "has_mask_input": np.zeros((1,), dtype="float32"),
            "orig_im_size": np.array([1024, 1024], dtype="float32"),
        }
        names = {i_.name for i_ in sess_d.get_inputs()}
        inputs = {k: v for k, v in inputs.items() if k in names}
        masks_out, ious = sess_d.run(None, inputs)[0], sess_d.run(None, inputs)[1]
        # best-of por ponto ponderado pelo IoU previsto
        m = masks_out[0]  # (n, h, w) ou (n, 3, h, w) conforme exportacao
        if m.ndim == 4:
            best = m[np.arange(len(p)), ious[0].argmax(axis=1)]
        else:
            best = m
        acc += best.astype("float32").sum(axis=0)
    thr = max(1, len(pts) // 3)
    sam_mask = (acc >= thr).astype("uint8")
    sam_mask = _rz(sam_mask, (H, W), order=0, preserve_range=True,
                   anti_aliasing=False).astype("uint8")
    # combina: mantem o miolo da mascara original + expansao SAM limitada
    from scipy import ndimage as ndi
    halo = ndi.binary_dilation(mask, iterations=3)
    out = ((mask == 1) | ((sam_mask == 1) & halo)).astype("uint8")
    return (out & prop).astype("uint8"), {"prompts": int(len(pts))}


def refinar(mask, prop, transform, crs, geom, start, end, cloud_max,
            metodo="auto"):
    """Retorna (mask_refinada, info). Em qualquer falha: mascara original."""
    if metodo == "off":
        return mask, {"metodo": "off"}
    try:
        rgb, data_ref = _rgb_peak_scene(geom, start, end, cloud_max,
                                        crs, transform, mask.shape)
    except Exception as e:
        return mask, {"metodo": "off",
                      "motivo": f"sem RGB de referencia: {e}"}
    base = {"rgb_ref_data": data_ref}
    if metodo in ("sam", "auto"):
        try:
            m2, info = _sam_refine(mask, prop, rgb)
            return m2, {"metodo": "sam", **base, **info}
        except Exception as e:
            base["sam_indisponivel"] = str(e)
            if metodo == "sam":
                return mask, {"metodo": "off", **base}
    try:
        m2, info = _slic_refine(mask, prop, rgb)
        return m2, {"metodo": "slic", **base, **info}
    except Exception as e:
        return mask, {"metodo": "off", "motivo": f"SLIC falhou: {e}", **base}
