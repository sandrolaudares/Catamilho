"""Registro de provedores de imagem de alta resolucao (Very High Resolution).

Sentinel-2 (10 m) segue como padrao gratuito. Os demais requerem chave/contrato:
- planetscope : Planet Labs, 3 m, DIARIO, 8 bandas (incl. NIR) — melhor custo/
  beneficio para confirmacao temporal de cultura. Requer PLANET_API_KEY.
- skysat      : Planet SkySat, 50 cm, 4-5 bandas (incl. NIR) — detalhe de talhao.
- blacksky3   : BlackSky Gen-3, 35 cm, pan + multiespectral (incl. SWIR),
  revisita ate 15x/dia, tasking via Spectra API — verificacao de fronteiras
  e validacao pontual de lavoura. Contrato comercial.
- pleiades    : Airbus Pleiades Neo, 30 cm — tasking/arquivo.
- maxar       : Maxar WorldView, 30 cm — tasking/arquivo.
"""
import os

PROVIDERS = {
    "sentinel2": {"rotulo": "Sentinel-2 (gratuito)", "res_m": 10, "revisita": "~5 dias",
                  "bandas": ["B02","B03","B04","B08","SCL"], "ativo": True,
                  "nota": "Padrao — serie temporal gratuita via STAC/Planetary Computer"},
    "planetscope": {"rotulo": "PlanetScope (Planet)", "res_m": 3, "revisita": "diario",
                    "bandas": ["red","green","blue","nir"],
                    "ativo": bool(os.getenv("PLANET_API_KEY")),
                    "nota": "Requer PLANET_API_KEY; diario + NIR = confirmacao fenologica fina"},
    "skysat": {"rotulo": "SkySat (Planet)", "res_m": 0.5, "revisita": "tasking/arquivo",
               "bandas": ["pan","red","green","blue","nir"], "ativo": False,
               "nota": "Requer contrato Planet; detalhe sub-metrico de talhao"},
    "blacksky3": {"rotulo": "BlackSky Gen-3", "res_m": 0.35, "revisita": "ate 15x/dia (tasking)",
                  "bandas": ["pan","multiespectral (incl. SWIR)"], "ativo": False,
                  "nota": "Requer contrato BlackSky Spectra; ideal p/ fronteiras e validacao pontual"},
    "pleiades": {"rotulo": "Airbus Pleiades Neo", "res_m": 0.30, "revisita": "tasking/arquivo",
                 "bandas": ["pan","4 bandas"], "ativo": False,
                 "nota": "Requer contrato Airbus OneAtlas"},
    "maxar": {"rotulo": "Maxar WorldView", "res_m": 0.30, "revisita": "tasking/arquivo",
              "bandas": ["pan","8 bandas"], "ativo": False,
              "nota": "Requer contrato Maxar"},
}

def listar():
    return PROVIDERS
