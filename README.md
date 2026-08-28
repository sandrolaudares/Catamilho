# Milho NDVI — Médio Norte MT 🌽

Web app (PWA) para **identificação de milho safrinha por série temporal de NDVI**
sobre um polígono desenhado no mapa, com **trava de desenho dentro do limite de
Mato Grosso** e presets para o Médio Norte: **Sorriso, Lucas do Rio Verde e
Primavera do Leste**.

Mesma espinha dorsal do [bauxita-sam](https://github.com/geografiatie-a/Bauxita):
PWA + backend Python (FastAPI) no Fly.io + exportação georreferenciada (JSON).
O que muda é o paradigma: em vez de classificar **uma foto** com índices RGB,
classificamos **uma série temporal** de NDVI (Sentinel-2, 10 m) com regras
fenológicas explicáveis.

## Por que série temporal?

O NDVI de uma data única não separa milho de soja, algodão ou pastagem — tudo é
"verde". O que separa é a **assinatura fenológica** da dupla safra soja → milho
safrinha do Médio Norte do MT (o "W invertido"):

| Período | Fenologia | NDVI típico (Sentinel-2) |
| --- | --- | --- |
| set–out | entressafra / preparo | 0.25 – 0.35 |
| nov–jan | pico da **soja** | ≥ 0.70 |
| jan–fev | **vale**: colheita da soja + plantio do milho | queda ≥ 0.15 e ≤ 0.55 |
| mar–mai | pico do **milho safrinha** | ≥ 0.70 (0.80–0.90) |
| jun–jul | senescência / colheita do milho | queda ≥ 0.25 do pico |

O classificador (`backend/classify.py`) aplica **6 regras ponderadas** sobre a
curva média mensal — auditáveis como os 3 índices espectrais do bauxita — e
retorna veredito + confiança + checklist das regras + tabela de estágios.
Classes: `milho_safrinha`, `provavel_safrinha`, `soja_unica`, `pico_verao`,
`perene`, `inconclusivo`, `dados_insuficientes`.

## Estrutura

```
milho-ndvi/
├── frontend/                # PWA (HTML/CSS/JS) — deploy estático
│   ├── index.html  styles.css  manifest.webmanifest  sw.js
│   ├── js/
│   │   ├── app.js           # orquestração + chamadas à API
│   │   ├── map.js           # Leaflet + Leaflet.draw + presets
│   │   ├── boundary.js      # trava: polígono 100% dentro de MT (Turf.js)
│   │   ├── timeseries.js    # gráfico NDVI × referência (Chart.js)
│   │   ├── classify.js      # veredito, regras, estágios
│   │   └── export.js        # JSON georreferenciado + PNG do gráfico
│   ├── data/                # GeoJSONs IBGE (gerados por scripts/shrink_geojson.py)
│   │   ├── mt.geojson
│   │   ├── sorriso.geojson / lucas-do-rio-verde.geojson / primavera-do-leste.geojson
│   └── icons/
├── backend/                 # FastAPI — deploy Fly.io
│   ├── server.py            # /api/health · /api/analyze · /api/classify · /api/reference-curves
│   ├── stac_ndvi.py         # STAC (Planetary Computer) + leitura parcial de COG (B04/B08/SCL)
│   ├── classify.py          # regras fenológicas ponderadas
│   ├── requirements.txt  Dockerfile  fly.toml
└── scripts/
    ├── shrink_geojson.py    # baixa/compacta limites IBGE -> frontend/data/
    ├── test_stac.py         # smoke test do catálogo STAC
    ├── build_icons.py       # ícones PWA (Pillow)
    └── serve_pwa.py         # serve o frontend local
```

## Passo a passo

### 1) Rodar local

```bash
# backend
cd backend
pip install -r requirements.txt
uvicorn server:app --port 8080

# frontend (outro terminal)
python scripts/serve_pwa.py 5173    # http://localhost:5173
```

Sem config extra, o PWA chama a API no mesmo host (`window.MILHO_API_URL=''`).
Para desenvolvimento com frontend em 5173 e API em 8080, abra
`http://localhost:5173` e adicione antes dos scripts no `index.html`:

```html
<script>window.MILHO_API_URL = 'http://localhost:8080';</script>
```

### 2) Deploy no Fly.io

```bash
cd backend
flyctl auth login
flyctl launch --copy-config --no-deploy   # cria o app (ex.: milho-ndvi-api)
flyctl deploy
```

Região `gru` (São Paulo). VM `shared-cpu-1x / 1 GB` com auto-stop para zero
quando ocioso — **mais leve que o bauxita-sam** (não há modelo de 375 MB;
processamento é NumPy/rasterio sobre recortes COG). Custo típico: US$ 1–3/mês
em uso esporádico.

Depois do deploy, no `frontend/index.html`:

```html
<script>window.MILHO_API_URL = 'https://milho-ndvi-api.fly.dev';</script>
```

### 3) Gerar limites e ícones

```bash
python scripts/shrink_geojson.py   # data IBGE -> frontend/data/
python scripts/build_icons.py      # icons 192/512
```

## API

| Endpoint | Método | Descrição |
| --- | --- | --- |
| `/api/health` | GET | status |
| `/api/reference-curves` | GET | curvas NDVI mensais de referência (safrinha/soja/pastagem) |
| `/api/analyze` | POST | `{geometry, start?, end?, cloud_max?, max_scenes?}` → série NDVI + classificação |
| `/api/classify` | POST | reclassifica uma série já calculada `{series: [{date, ndvi}…]}` |

Detalhes do pipeline (`backend/stac_ndvi.py`):

- catálogo **STAC do Planetary Computer**, coleção `sentinel-2-l2a`;
- **uma cena por dia** (a de menor nuvem), teto de cenas configurável;
- leitura **parcial** dos COGs (janela do polígono, `out_shape ≤ 256²`) —
  nunca baixa a cena inteira; 8 threads;
- máscara de nuvens pela banda **SCL** (mantém só vegetação + solo descoberto);
- correção do **offset harmonizado** (+1000) do Sentinel-2 baseline ≥ 4.0 antes
  do NDVI (a razão não é invariante a offset);
- mínimo de 8 pixels válidos para aceitar uma data.

## Exportação

Botão **JSON** gera `milho_AAAAMMDD_hhmmss.json` com GeoJSON do polígono, área
(ha), série NDVI completa (data, ndvi, pixels, nuvem, tile), veredito, regras e
estágios — ingerível direto em QGIS/GeoPandas, com rastreabilidade da decisão.
Botão **PNG** exporta o gráfico.

## Limitações conhecidas (MVP)

- jan–abr é época chuvosa no MT: 30–50% das cenas são inutilizáveis. O gráfico
  mostra os pontos brutos; interpolação (Savitzky-Golay) entra na v2.
- Análise depende de rede (baixa imagens): o offline do PWA cobre só o shell.
- Regras não separam **milho 1ª safra** de soja única com total segurança
  (classe `pico_verao`) — DTW/ML na v2 resolve.
- Latência de 1–3 min por polígono (dezenas de cenas × 3 bandas). Cache de
  série por polígono entra na v2.

## Roadmap

1. **DTW** contra biblioteca de curvas de referência calibradas por município
   (features fenológicas + Random Forest na sequência).
2. **Validação cruzada** com a camada anual de milho 2ª safra do MapBiomas
   (ground truth gratuito) para medir acurácia.
3. Suporte a **PlanetScope 3 m/diário** (a arquitetura STAC já comporta outro
   catálogo) e a CBERS-4A.
4. Interpolação temporal + métricas fenológicas (data do pico, taxa de
   senescência, área sob a curva) como features explícitas.
5. Cache de séries por polígono (hash da geometria) e modo batelada (CSV de
   polígonos → relatório).

## Segurança / Licenciamento

- Sentinel-2: dados abertos (Copernicus), via Planetary Computer (Microsoft).
- Limites IBGE: uso público com atribuição.
- Código: proprietário — mesmo regime do bauxita-sam.
