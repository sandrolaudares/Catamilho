/* boundary.js — trava de desenho dentro do limite de Mato Grosso (IBGE) */
let mtFeature = null;

async function loadBoundary() {
  try {
    const g = await (await fetch('data/mt.geojson')).json();
    mtFeature = g.features[0];
    L.geoJSON(g, {
      style: { color: '#fbbf24', weight: 1.5, fill: false, dashArray: '4 4' },
      interactive: false,
    }).addTo(map);
  } catch {
    setStatus('Não foi possível carregar o limite de MT (data/mt.geojson).', 'err');
  }
}

/* Regras: poligono 100% dentro de MT; area entre 10 ha e 200 mil ha */
function validatePolygon(geom) {
  if (!mtFeature) return { ok: false, msg: 'Limite de MT ainda carregando…' };
  const poly = turf.polygon(geom.coordinates);
  const areaHa = turf.area(poly) / 10000;
  const fmt = (v) => v.toLocaleString('pt-BR', { maximumFractionDigits: 0 });

  if (areaHa < 10)
    return { ok: false, msg: `✖ Área muito pequena (${fmt(areaHa)} ha) — com pixels de 10 m, desenhe pelo menos ~10 ha.` };
  if (areaHa > 200000)
    return { ok: false, msg: `✖ Área muito grande (${fmt(areaHa)} ha) — desenhe um talhão ou fazenda.` };
  if (turf.booleanWithin(poly, mtFeature))
    return { ok: true, msg: `✔ Polígono dentro de MT · ${fmt(areaHa)} ha` };
  if (turf.booleanIntersects(poly, mtFeature))
    return { ok: false, msg: '✖ O polígono ultrapassa a fronteira de Mato Grosso — ajuste os vértices.' };
  return { ok: false, msg: '✖ O polígono está fora de Mato Grosso.' };
}
