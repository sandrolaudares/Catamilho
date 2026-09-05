"""milho-ndvi API v0.2 — regras + DTW + Savitzky-Golay + calibracao + MapBiomas.

FastAPI + STAC (Planetary Computer) + leitura parcial de COG (rasterio).
"""
import datetime as dt
import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import calibration
import car
import dtw
import mapbiomas
import pixel_vectorize
import smoothing
from classify import classificar
from stac_ndvi import serie_ndvi

log = logging.getLogger("milho")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title="Milho NDVI — Medio Norte MT", version="0.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"], allow_headers=["*"],
)


class AnalyzeReq(BaseModel):
    geometry: dict
    start: str | None = None
    end: str | None = None
    cloud_max: int = Field(70, ge=0, le=100)
    max_scenes: int = Field(140, ge=10, le=300)
    smooth: bool = True
    use_dtw: bool = True
    validar_mapbiomas: bool = False
    ano_validacao: int | None = None
    mapbiomas_url: str | None = None
    limiares: dict | None = None


class CalibReq(BaseModel):
    classe: str  # milho_safrinha | soja_unica | milho_1a_safra | pastagem | algodao
    geometry: dict
    start: str
    end: str
    cloud_max: int = Field(70, ge=0, le=100)
    max_scenes: int = Field(140, ge=10, le=300)
    safra: str | None = None
    municipio: str | None = None
    observacao: str | None = None


class ValidarReq(BaseModel):
    geometry: dict
    ano: int
    classe_propria: str | None = None
    url_override: str | None = None


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "milho-ndvi", "version": "0.5.0",
            "time": dt.datetime.utcnow().isoformat() + "Z"}


@app.get("/api/reference-curves")
def curves():
    """Curvas de referencia efetivas (calibradas quando ha amostras)."""
    return calibration.get_reference_curves()


@app.post("/api/analyze")
def analyze(req: AnalyzeReq):
    end = req.end or dt.date.today().isoformat()
    start = req.start or (dt.date.today() - dt.timedelta(days=548)).isoformat()
    if req.geometry.get("type") != "Polygon":
        raise HTTPException(400, "geometry deve ser um Polygon GeoJSON")
    try:
        series, meta = serie_ndvi(req.geometry, start, end,
                                  req.cloud_max, req.max_scenes)
    except Exception as e:
        log.exception("stac")
        raise HTTPException(502, f"falha na consulta STAC/COG: {e}")
    if len(series) < 6:
        raise HTTPException(
            422, f"serie muito curta ({len(series)} datas uteis)")

    # 1) suavizacao / interpolacao
    smoothed = smoothing.regularize(series) if req.smooth else None

    # 2) regras (usa serie original — as regras ja fazem media mensal robusta)
    result = classificar(series, fim_serie=end, limiares=req.limiares)

    # 3) DTW contra curvas de referencia (calibradas se houver)
    dtw_result = None
    if req.use_dtw:
        refs = calibration.get_reference_curves()
        dtw_result = dtw.compare_curves(series, refs)
        # se DTW diverge fortemente das regras, marca ambiguidade
        if dtw_result.get("ok"):
            best = dtw_result["melhor"]["classe"]
            if result["classe"] == "pico_verao" and best in (
                    "milho_1a_safra", "soja_unica"):
                # DTW desempata o pico_verao
                result["classe"] = best
                result["veredito"] = (
                    dtw_result["melhor"]["rotulo"] + " (desempatado por DTW)")
                result["desempate_dtw"] = True

    # 4) validacao cruzada MapBiomas (opcional)
    validacao = None
    confronto = None
    if req.validar_mapbiomas:
        ano = req.ano_validacao or (dt.date.today().year - 1)
        validacao = mapbiomas.validar(req.geometry, ano,
                                      url_override=req.mapbiomas_url)
        confronto = mapbiomas.confrontar(result["classe"], validacao)

    return {
        "series": series, "meta": meta,
        "classification": result,
        "smoothed": smoothed,
        "dtw": dtw_result,
        "mapbiomas": validacao, "confronto": confronto,
    }


@app.post("/api/calibrate")
def calibrate(req: CalibReq):
    """Registra uma amostra rotulada e atualiza a curva media da classe."""
    if req.geometry.get("type") != "Polygon":
        raise HTTPException(400, "geometry deve ser um Polygon GeoJSON")
    try:
        series, meta = serie_ndvi(req.geometry, req.start, req.end,
                                  req.cloud_max, req.max_scenes)
    except Exception as e:
        raise HTTPException(502, f"falha na consulta STAC/COG: {e}")
    if len(series) < 8:
        raise HTTPException(
            422, f"serie curta demais p/ calibrar ({len(series)} datas)")
    try:
        out = calibration.add_sample(
            classe=req.classe, geometry=req.geometry, series=series,
            safra=req.safra, municipio=req.municipio,
            observacao=req.observacao)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "meta_ndvi": meta, **out}


@app.get("/api/calibrate/samples")
def list_calib():
    return {"amostras": calibration.list_samples(),
            "curvas": calibration.get_reference_curves()}


@app.delete("/api/calibrate/samples/{sample_id}")
def del_calib(sample_id: str):
    if not calibration.delete_sample(sample_id):
        raise HTTPException(404, "amostra nao encontrada")
    return {"ok": True}


@app.post("/api/validate")
def validate(req: ValidarReq):
    """Valida um poligono contra o MapBiomas 2a safra do ano informado."""
    v = mapbiomas.validar(req.geometry, req.ano, url_override=req.url_override)
    c = mapbiomas.confrontar(req.classe_propria or "", v) if req.classe_propria else None
    return {"mapbiomas": v, "confronto": c}


@app.get("/api/car/imoveis")
def car_imoveis(bbox: str | None = None, cod: str | None = None,
                count: int = 25):
    """Imoveis rurais do CAR (Sicar-MT).
    - bbox=minx,miny,maxx,maxy  -> imoveis que intersectam a caixa
    - cod=MT-5107925-XXXX...    -> imovel pelo codigo CAR
    """
    try:
        if cod:
            feats = car.por_codigo(cod.strip(), count=3)
        elif bbox:
            parts = [float(v) for v in bbox.split(",")]
            if len(parts) != 4:
                raise ValueError("bbox precisa de 4 valores")
            feats = car.por_bbox(*parts, count=min(count, 50))
        else:
            raise HTTPException(400, "informe bbox= ou cod=")
    except ValueError as e:
        raise HTTPException(400, f"parametro invalido: {e}")
    except Exception as e:
        log.exception("car")
        raise HTTPException(502, f"falha na consulta ao CAR/Sicar: {e}")
    return {"type": "FeatureCollection", "features": feats,
            "total": len(feats),
            "fonte": "Sicar/CAR — geoserver.car.gov.br (sicar_imoveis_mt)"}


class VectorizeReq(BaseModel):
    geometry: dict  # Polygon/MultiPolygon — tipicamente um imovel CAR
    start: str | None = None
    end: str | None = None
    cloud_max: int = Field(70, ge=0, le=100)
    max_scenes: int = Field(60, ge=6, le=200)
    threshold: float = Field(0.72, ge=0.4, le=0.95)
    min_area_ha: float = Field(2.0, ge=0.1, le=100)
    validar_mapbiomas: bool = True
    ano_mapbiomas: int | None = None
    mapbiomas_url: str | None = None
    limiares: dict | None = None
    refinar: str = Field("slic", pattern="^(off|slic|sam|auto)$")


@app.post("/api/vectorize")
def vectorize(req: VectorizeReq):
    """Classifica milho pixel a pixel (10 m) dentro da propriedade e
    vetoriza os talhoes; mede acuracia contra MapBiomas 2a safra."""
    end = req.end or dt.date.today().isoformat()
    start = req.start or (dt.date.today() - dt.timedelta(days=335)).isoformat()
    if req.geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise HTTPException(400, "geometry deve ser Polygon ou MultiPolygon")
    try:
        geojson, stats, mask, transform, crs = pixel_vectorize.vectorizar_milho(
            req.geometry, start, end, req.cloud_max, req.max_scenes,
            req.threshold, req.min_area_ha, limiares=req.limiares,
            refinar=req.refinar)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        log.exception("vectorize")
        raise HTTPException(502, f"falha na vetorizacao: {e}")
    acuracia = None
    if req.validar_mapbiomas:
        ano = req.ano_mapbiomas or (dt.date.today().year - 2)
        try:
            acuracia = mapbiomas.acuracia_vs_mask(
                mask, transform, crs, ano, url_override=req.mapbiomas_url)
        except Exception as e:
            acuracia = {"ok": False, "motivo": f"erro na validacao: {e}"}
    return {"geojson": geojson, "stats": stats, "acuracia": acuracia,
            "meta": {"periodo": [start, end], "threshold": req.threshold,
                     "min_area_ha": req.min_area_ha}}


class ClassifyReq(BaseModel):
    series: list[dict]  # [{date, ndvi}, ...]
    limiares: dict | None = None


@app.post("/api/classify")
def classify_only(req: ClassifyReq):
    """Reclassifica uma serie ja calculada — usado pela UI de calibracao fina."""
    if len(req.series) < 6:
        raise HTTPException(422, "minimo de 6 observacoes para classificar")
    return classificar(req.series, fim_serie=None, limiares=req.limiares)


# ---------- analise com progresso real (streaming NDJSON) ----------
import json as _json
import queue as _queue
import threading as _threading

from fastapi.responses import StreamingResponse


def _ndjson(obj):
    return _json.dumps(obj, ensure_ascii=False) + "\n"


@app.post("/api/analyze/stream")
def analyze_stream(req: AnalyzeReq):
    """Mesma analise do /api/analyze, mas transmite o progresso real:
    cenas_encontradas -> processando (done/total) -> classificando -> concluido."""
    end = req.end or dt.date.today().isoformat()
    start = req.start or (dt.date.today() - dt.timedelta(days=548)).isoformat()

    def gen():
        from stac_ndvi import search_scenes
        if req.geometry.get("type") != "Polygon":
            yield _ndjson({"phase": "erro", "mensagem": "geometry deve ser Polygon"})
            return
        try:
            items = search_scenes(req.geometry, start, end, req.cloud_max)
        except Exception as e:
            yield _ndjson({"phase": "erro", "mensagem": f"falha na busca STAC: {e}"})
            return
        yield _ndjson({"phase": "cenas_encontradas", "total": len(items)})

        q = _queue.Queue()
        holder = {}

        def work():
            try:
                series, meta = serie_ndvi(
                    req.geometry, start, end, req.cloud_max, req.max_scenes,
                    items=items,
                    on_progress=lambda d, t: q.put(("p", d, t)))
                holder["series"] = series
                holder["meta"] = meta
            except Exception as e:
                holder["error"] = str(e)
            finally:
                q.put(("fim",))

        _threading.Thread(target=work, daemon=True).start()
        while True:
            item = q.get()
            if item[0] == "p":
                yield _ndjson({"phase": "processando",
                               "done": item[1], "total": item[2]})
            else:
                break
        if "error" in holder:
            yield _ndjson({"phase": "erro", "mensagem": holder["error"]})
            return
        series, meta = holder["series"], holder["meta"]
        if len(series) < 6:
            yield _ndjson({"phase": "erro",
                           "mensagem": f"serie muito curta ({len(series)} datas uteis)"})
            return

        yield _ndjson({"phase": "classificando"})
        smoothed = smoothing.regularize(series) if req.smooth else None
        result = classificar(series, fim_serie=end, limiares=req.limiares)
        dtw_result = None
        if req.use_dtw:
            refs = calibration.get_reference_curves()
            dtw_result = dtw.compare_curves(series, refs)
            if dtw_result.get("ok"):
                best = dtw_result["melhor"]["classe"]
                if result["classe"] == "pico_verao" and best in (
                        "milho_1a_safra", "soja_unica"):
                    result["classe"] = best
                    result["veredito"] = (dtw_result["melhor"]["rotulo"]
                                          + " (desempatado por DTW)")
                    result["desempate_dtw"] = True
        validacao = confronto = None
        if req.validar_mapbiomas:
            ano = req.ano_validacao or (dt.date.today().year - 1)
            try:
                validacao = mapbiomas.validar(req.geometry, ano,
                                              url_override=req.mapbiomas_url)
                confronto = mapbiomas.confrontar(result["classe"], validacao)
            except Exception:
                validacao = None
        payload = {"series": series, "meta": meta, "classification": result,
                   "smoothed": smoothed, "dtw": dtw_result,
                   "mapbiomas": validacao, "confronto": confronto}
        yield _ndjson({"phase": "concluido", "data": payload})

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ---------- vetorizacao com progresso real (streaming NDJSON) ----------
@app.post("/api/vectorize/stream")
def vectorize_stream(req: VectorizeReq):
    end = req.end or dt.date.today().isoformat()
    start = req.start or (dt.date.today() - dt.timedelta(days=335)).isoformat()

    def gen():
        if req.geometry.get("type") not in ("Polygon", "MultiPolygon"):
            yield _ndjson({"phase": "erro", "mensagem": "geometry deve ser Polygon/MultiPolygon"})
            return
        yield _ndjson({"phase": "cenas_encontradas", "total": req.max_scenes})
        q = _queue.Queue()
        holder = {}

        def work():
            try:
                out = pixel_vectorize.vectorizar_milho(
                    req.geometry, start, end, req.cloud_max, req.max_scenes,
                    req.threshold, req.min_area_ha, limiares=req.limiares,
                    refinar=req.refinar,
                    on_progress=lambda d, t: q.put(("p", d, t)))
                holder["out"] = out
            except Exception as e:
                holder["error"] = str(e)
            finally:
                q.put(("fim",))

        _threading.Thread(target=work, daemon=True).start()
        while True:
            item = q.get()
            if item[0] == "p":
                yield _ndjson({"phase": "processando", "done": item[1], "total": item[2]})
            else:
                break
        if "error" in holder:
            yield _ndjson({"phase": "erro", "mensagem": holder["error"]})
            return
        geojson, stats, mask, transform, crs = holder["out"]
        yield _ndjson({"phase": "classificando"})
        acuracia = None
        if req.validar_mapbiomas:
            ano = req.ano_mapbiomas or (dt.date.today().year - 2)
            try:
                acuracia = mapbiomas.acuracia_vs_mask(
                    mask, transform, crs, ano, url_override=req.mapbiomas_url)
            except Exception as e:
                acuracia = {"ok": False, "motivo": f"erro na validacao: {e}"}
        payload = {"geojson": geojson, "stats": stats, "acuracia": acuracia,
                   "meta": {"periodo": [start, end], "threshold": req.threshold,
                            "min_area_ha": req.min_area_ha}}
        yield _ndjson({"phase": "concluido", "data": payload})

    return StreamingResponse(gen(), media_type="application/x-ndjson")
