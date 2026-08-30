"""Proxy WFS para imoveis rurais do CAR (Sicar) — camada Mato Grosso.

O GeoServer nacional do Sicar usa TLS legado e rejeita o handshake de
clientes Python modernos (SSLV3_ALERT_HANDSHAKE_FAILURE), enquanto aceita
libcurl normalmente. Estrategia de robustez, em ordem:
  1. urllib com contexto SSL relaxado (SECLEVEL=1, minimo TLSv1);
  2. retries com backoff;
  3. fallback para `curl` via subprocess (libcurl negocia com o servidor).

Tambem normaliza a resposta: bbox filter (independe do nome da coluna de
geometria), CQL_FILTER por codigo do imovel e ordem de eixos (garante lon/lat).
"""
from __future__ import annotations

import json
import ssl
import subprocess
import time
import urllib.parse
import urllib.request

CAR_WFS = "https://geoserver.car.gov.br/geoserver/sicar/wfs"
CAR_LAYER = "sicar:sicar_imoveis_mt"
TIMEOUT = 60
COD_ATTRS = ["cod_imovel", "COD_IMOVEL", "codigo_imovel"]

_SSL_CTX = ssl.create_default_context()
try:
    _SSL_CTX.set_ciphers("DEFAULT:@SECLEVEL=1")
except Exception:
    pass
try:
    _SSL_CTX.minimum_version = ssl.TLSVersion.TLSv1
except Exception:
    pass


def _via_curl(url: str) -> dict:
    r = subprocess.run(
        ["curl", "-sS", "--retry", "3", "--retry-all-errors",
         "-m", str(TIMEOUT), "-A", "milho-ndvi/0.3", url],
        capture_output=True, timeout=TIMEOUT + 20)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode()[:300] or f"curl rc={r.returncode}")
    return json.loads(r.stdout.decode("utf-8"))


def _request(params: dict) -> dict:
    qs = urllib.parse.urlencode(params, safe="()")
    url = f"{CAR_WFS}?{qs}"
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "milho-ndvi/0.3"})
            with urllib.request.urlopen(req, timeout=TIMEOUT,
                                        context=_SSL_CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    try:
        return _via_curl(url)
    except Exception as e:
        raise RuntimeError(f"urllib: {last}; curl: {e}")


def _looks_swapped(coords) -> bool:
    """Detecta par (lat, lon) quando esperamos (lon, lat) para o MT."""
    try:
        c = coords
        while isinstance(c[0], list):
            c = c[0]
        x, y = float(c[0]), float(c[1])
        # MT: lon ~ -61..-50, lat ~ -18..-7. Se x parece latitude, esta trocado.
        return abs(x) < 25 and abs(y) > 40
    except Exception:
        return False


def _swap(coords):
    if isinstance(coords, list) and coords and isinstance(coords[0], (int, float)):
        return [coords[1], coords[0]]
    return [_swap(c) for c in coords]


def _normalize(features: list) -> list:
    out = []
    for f in features:
        g = f.get("geometry")
        if not g:
            continue
        if _looks_swapped(g["coordinates"]):
            g["coordinates"] = _swap(g["coordinates"])
        out.append({
            "type": "Feature",
            "geometry": g,
            "properties": f.get("properties", {}),
        })
    return out


def _get(params_base: dict, **extra) -> dict:
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeName": CAR_LAYER, "outputFormat": "application/json",
        **params_base, **extra,
    }
    return _request(params)


def por_bbox(minx: float, miny: float, maxx: float, maxy: float,
             count: int = 25) -> list:
    d = _get({"count": count},
             bbox=f"{minx},{miny},{maxx},{maxy},EPSG:4326")
    return _normalize(d.get("features", []))


def por_codigo(cod: str, count: int = 3) -> list:
    last_err = None
    for attr in COD_ATTRS:
        try:
            d = _get({"count": count}, CQL_FILTER=f"{attr}='{cod}'")
            return _normalize(d.get("features", []))
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"busca por codigo CAR falhou: {last_err}")
