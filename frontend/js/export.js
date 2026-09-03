/* export.js — JSON georreferenciado + PNG do grafico (padrao bauxita-sam) */
function _fileName(ext) {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `milho_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_` +
         `${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.${ext}`;
}

function _download(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

function exportJSON() {
  if (!state.geometry || !state.series) return;
  const areaHa = turf.area(turf.polygon(state.geometry.coordinates)) / 10000;
  const payload = {
    tipo: 'milho-ndvi-analise',
    versao_app: '0.4.0',
    gerado_em: new Date().toISOString(),
    fonte: 'Sentinel-2 L2A via Planetary Computer (STAC/COG) · 10 m',
    poligono: {
      type: 'Feature',
      geometry: state.geometry,
      properties: { area_ha: Math.round(areaHa * 10) / 10 },
    },
    serie_ndvi: state.series,
    classificacao: state.cls,
    car_imovel: state.carInfo || null,
    vetorizado: state.vectorized
      ? { geojson: state.vectorized.geojson, stats: state.vectorized.stats,
          acuracia: state.vectorized.acuracia }
      : null,
  };
  _download(new Blob([JSON.stringify(payload, null, 2)],
    { type: 'application/json' }), _fileName('json'));
}

function exportPNG() {
  if (state.chart) state.chart.canvas.toBlob(b => _download(b, _fileName('png')));
}
