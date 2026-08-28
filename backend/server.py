"""milho-ndvi API — identificacao de milho safrinha por serie temporal NDVI.

FastAPI + STAC (Planetary Computer) + leitura parcial de COG (rasterio).
Padrao de deploy identico ao bauxita-sam (Fly.io, Dockerfile na raiz).
"""
import datetime as dt
import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from classify import classificar
from stac_ndvi import REFERENCE_CURVES, serie_ndvi

log = logging.getLogger("milho")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title="Milho NDVI — Medio Norte MT", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeReq(BaseModel):
    geometry: dict  # GeoJSON Polygon (EPSG:4326), validado no frontend como dentro de MT
    start: str | None = None  # ISO date; default = hoje - 18 meses
    end: str | None = None
    cloud_max: int = Field(70, ge=0, le=100)
    max_scenes: int = Field(140, ge=10, le=300)


class ClassifyReq(BaseModel):
    series: list[dict]  # [{date, ndvi}, ...]


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "milho-ndvi",
            "time": dt.datetime.utcnow().isoformat() + "Z"}


@app.get("/api/reference-curves")
def curves():
    """Curvas NDVI de referencia (mensal, jan..dez) para sobrepor no grafico."""
    return REFERENCE_CURVES


@app.post("/api/analyze")
def analyze(req: AnalyzeReq):
    end = req.end or dt.date.today().isoformat()
    start = req.start or (dt.date.today() - dt.timedelta(days=548)).isoformat()
    if req.geometry.get("type") != "Polygon":
        raise HTTPException(400, "geometry deve ser um Polygon GeoJSON")
    try:
        series, meta = serie_ndvi(req.geometry, start, end,
                                  req.cloud_max, req.max_scenes)
    except Exception as e:  # falha de catalogo/rede/COS
        log.exception("stac")
        raise HTTPException(502, f"falha na consulta STAC/COG: {e}")
    if len(series) < 6:
        raise HTTPException(
            422,
            f"serie muito curta ({len(series)} datas uteis) — amplie o periodo "
            "ou eleve o limite de nuvens")
    result = classificar(series, fim_serie=end)
    return {"series": series, "meta": meta, "classification": result}


@app.post("/api/classify")
def classify_only(req: ClassifyReq):
    if len(req.series) < 6:
        raise HTTPException(422, "minimo de 6 observacoes para classificar")
    return classificar(req.series, fim_serie=None)
