"""milho-ndvi API v0.6 — regras + DTW + Savitzky-Golay + calibracao + CAR.

FastAPI + STAC (Planetary Computer) + leitura parcial de COG (rasterio).
MapBiomas removido do produto (v0.6).
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
import highres
import pixel_vectorize
import smoothing
from classify import classificar
from stac_ndvi import serie_ndvi

log = logging.getLogger("milho")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title="Milho NDVI — Medio Norte MT", version="0.8.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["Content-Type", "Authorization"],
)


class AnalyzeReq(BaseModel):
    geometry: dict
    start: str | None = None
    end: str | None = None
    cloud_max: int = Field(70, ge=0, le=100)
    max_scenes: int = Field(140, ge=10, le=300)
    smooth: bool = True
    use_dtw: bool = True
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


class ClassifyReq(BaseModel):
    series: list[dict]  # [{date, ndvi}, ...]
    limiares: dict | None = None


class VectorizeReq(BaseModel):
    geometry: dict  # Polygon/MultiPolygon — tipicamente um imovel CAR
    start: str | None = None
    end: str | None = None
    cloud_max: int = Field(70, ge=0, le=100)
    max_scenes: int = Field(60, ge=6, le=200)
    threshold: float = Field(0.72, ge=0.4, le=0.95)
    min_area_ha: float = Field(2.0, ge=0.1, le=100)
    limiares: dict | None = None
    refinar: str = Field("slic", pattern="^(off|slic|sam|auto)$")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "milho-ndvi", "version": "0.8.0",
            "time": dt.datetime.utcnow().isoformat() + "Z"}


@app.get("/api/reference-curves")
def curves():
    """Curvas de referencia efetivas (calibradas quando ha amostras)."""
    return calibration.get_reference_curves()


def _run_analysis(req: AnalyzeReq):
    """Nucleo da analise (compartilhado entre /analyze e /analyze/stream)."""
    series, meta = req._series_meta  # preenchido pelo chamador
    smoothed = smoothing.regularize(series) if req.smooth else None
    result = classificar(series, fim_serie=req.end, limiares=req.limiares)
    dtw_result = None
    if req.use_dtw:
        refs = calibration.get_reference_curves()
        dtw_result = dtw.compare_curves(series, refs)
        if dtw_result.get("ok"):
            best = dtw_result["melhor"]["classe"]
            margem = dtw_result.get("margem") or 0
            # 1) desempate pico_verao (1a safra x soja unica)
            if result["classe"] == "pico_verao" and best in (
                    "milho_1a_safra", "soja_unica"):
                result["classe"] = best
                result["veredito"] = (dtw_result["melhor"]["rotulo"]
                                      + " (desempatado por DTW)")
                result["desempate_dtw"] = True
            # 2) ANTI-FALSO-POSITIVO milho x algodao: se as regras disseram
            #    milho mas o DTW aponta algodao com vantagem clara, o DTW vence.
            #    (regras sozinhas confundem as duas culturas — ambas tem pico
            #    no outono; a curva temporal e o discriminante real.)
            elif (result["classe"] in ("milho_safrinha", "provavel_safrinha")
                  and best == "algodao" and margem >= 0.04):
                result["classe"] = "algodao"
                result["emoji"] = "🌱"
                result["veredito"] = ("Algodão — ciclo alongado/atrasado "
                                      "(DTW superou as regras fenológicas)")
                result["confianca"] = round(min(0.95,
                    result["confianca"] * 0.7 + dtw_result["melhor"]["similaridade"] * 0.3), 3)
                result["correcao_dtw"] = True
                result["resumo"] = (result.get("resumo", "") +
                    f" DTW: algodao superou milho (margem {margem:.3f}).")
    return {"series": series, "meta": meta, "classification": result,
            "smoothed": smoothed, "dtw": dtw_result}


@app.post("/api/analyze")
def analyze(req: AnalyzeReq):
    end = req.end or dt.date.today().isoformat()
    start = req.start or (dt.date.today() - dt.timedelta(days=548)).isoformat()
    req.end = end
    if req.geometry.get("type") != "Polygon":
        raise HTTPException(400, "geometry deve ser um Polygon GeoJSON")
    try:
        series, meta = serie_ndvi(req.geometry, start, end,
                                  req.cloud_max, req.max_scenes)
    except Exception as e:
        log.exception("stac")
        raise HTTPException(502, f"falha na consulta STAC/COG: {e}")
    if len(series) < 6:
        raise HTTPException(422, f"serie muito curta ({len(series)} datas uteis)")
    req._series_meta = (series, meta)
    return _run_analysis(req)


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
        raise HTTPException(422, f"serie curta demais p/ calibrar ({len(series)} datas)")
    try:
        out = calibration.add_sample(
            classe=req.classe, geometry=req.geometry, series=series,
            safra=req.safra, municipio=req.municipio, observacao=req.observacao)
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


@app.post("/api/classify")
def classify_only(req: ClassifyReq):
    """Reclassifica uma serie ja calculada — usado pela UI de calibracao fina."""
    if len(req.series) < 6:
        raise HTTPException(422, "minimo de 6 observacoes para classificar")
    return classificar(req.series, fim_serie=None, limiares=req.limiares)


@app.get("/api/car/imoveis")
def car_imoveis(bbox: str | None = None, cod: str | None = None, count: int = 25):
    """Imoveis rurais do CAR (Sicar-MT). bbox=minx,miny,maxx,maxy ou cod=MT-..."""
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
    return {"type": "FeatureCollection", "features": feats, "total": len(feats),
            "fonte": "Sicar/CAR — geoserver.car.gov.br (sicar_imoveis_mt)"}


@app.post("/api/vectorize")
def vectorize(req: VectorizeReq):
    """Classifica milho pixel a pixel (10 m) dentro da propriedade e vetoriza."""
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
    return {"geojson": geojson, "stats": stats,
            "meta": {"periodo": [start, end], "threshold": req.threshold,
                     "min_area_ha": req.min_area_ha}}


# ---------- streaming com progresso real (NDJSON) ----------
import json as _json
import queue as _queue
import threading as _threading

from fastapi.responses import StreamingResponse


def _ndjson(obj):
    return _json.dumps(obj, ensure_ascii=False) + "\n"


@app.post("/api/analyze/stream")
def analyze_stream(req: AnalyzeReq):
    end = req.end or dt.date.today().isoformat()
    start = req.start or (dt.date.today() - dt.timedelta(days=548)).isoformat()
    req.end = end

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
        req._series_meta = (series, meta)
        yield _ndjson({"phase": "concluido", "data": _run_analysis(req)})

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/vectorize/stream")
def vectorize_stream(req: VectorizeReq):
    end = req.end or dt.date.today().isoformat()
    start = req.start or (dt.date.today() - dt.timedelta(days=335)).isoformat()

    def gen():
        if req.geometry.get("type") not in ("Polygon", "MultiPolygon"):
            yield _ndjson({"phase": "erro",
                           "mensagem": "geometry deve ser Polygon/MultiPolygon"})
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
                yield _ndjson({"phase": "processando",
                               "done": item[1], "total": item[2]})
            else:
                break
        if "error" in holder:
            yield _ndjson({"phase": "erro", "mensagem": holder["error"]})
            return
        geojson, stats, mask, transform, crs = holder["out"]
        yield _ndjson({"phase": "classificando"})
        payload = {"geojson": geojson, "stats": stats,
                   "meta": {"periodo": [start, end], "threshold": req.threshold,
                            "min_area_ha": req.min_area_ha}}
        yield _ndjson({"phase": "concluido", "data": payload})

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/api/providers")
def providers():
    """Fontes de imagem disponiveis (alta resolucao requer chave/contrato)."""
    return highres.listar()


# ---------- auth, gestao de usuarios e auditoria (v0.8) ----------
import auth as _auth
from fastapi import Header, Request

_auth.init_db()  # cria tabelas + semeia admin (senha via ADMIN_PASSWORD)


class LoginReq(BaseModel):
    username: str
    password: str


class UserReq(BaseModel):
    username: str
    password: str
    role: str = "user"


class TrackReq(BaseModel):
    event: str
    page: str | None = None
    target: str | None = None
    meta: str | None = None


def _admin_user(authorization: str | None):
    tok = (authorization or "").replace("Bearer ", "").strip()
    u = _auth.user_by_token(tok)
    if not u or u["role"] != "admin":
        raise HTTPException(401, "acesso restrito ao administrador")
    return u


@app.post("/api/auth/login")
def auth_login(req: LoginReq, request: Request):
    ip = request.client.host if request.client else ""
    s = _auth.login(req.username, req.password, ip)
    if not s:
        raise HTTPException(401, "usuário ou senha inválidos")
    return s


@app.get("/api/auth/me")
def auth_me(authorization: str | None = Header(None)):
    tok = (authorization or "").replace("Bearer ", "").strip()
    u = _auth.user_by_token(tok)
    if not u:
        raise HTTPException(401, "sessão inválida")
    return u


@app.post("/api/auth/logout")
def auth_logout(authorization: str | None = Header(None)):
    tok = (authorization or "").replace("Bearer ", "").strip()
    _auth.logout(tok)
    return {"ok": True}


@app.post("/api/track")
def track(req: TrackReq, request: Request,
          authorization: str | None = Header(None)):
    tok = (authorization or "").replace("Bearer ", "").strip()
    u = _auth.user_by_token(tok)
    ip = request.client.host if request.client else ""
    _auth.track(u["username"] if u else "anon", req.event, req.page,
                req.target, req.meta, ip)
    return {"ok": True}


@app.get("/api/admin/users")
def admin_users(authorization: str | None = Header(None)):
    _admin_user(authorization)
    return _auth.list_users()


@app.post("/api/admin/users")
def admin_create_user(req: UserReq, authorization: str | None = Header(None)):
    _admin_user(authorization)
    if req.role not in ("user", "admin"):
        raise HTTPException(400, "role deve ser user ou admin")
    try:
        return _auth.create_user(req.username, req.password, req.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(409, "usuário já existe")


@app.post("/api/admin/users/{username}/{action}")
def admin_toggle(username: str, action: str,
                 authorization: str | None = Header(None)):
    _admin_user(authorization)
    if action not in ("activate", "deactivate"):
        raise HTTPException(400, "acao invalida")
    if not _auth.set_active(username, action == "activate"):
        raise HTTPException(404, "usuário não encontrado")
    return {"ok": True}


@app.delete("/api/admin/users/{username}")
def admin_delete(username: str, authorization: str | None = Header(None)):
    _admin_user(authorization)
    try:
        if not _auth.delete_user(username):
            raise HTTPException(404, "usuário não encontrado")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/admin/analytics")
def admin_analytics(authorization: str | None = Header(None)):
    _admin_user(authorization)
    return _auth.analytics()
