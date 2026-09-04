/* map.js — Leaflet + desenho de poligono + presets dos municipios foco */
let map;

function initMap() {
  map = L.map('map', { zoomControl: true }).setView([-12.9, -55.6], 7);

  const esri = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    { maxZoom: 18, attribution: 'Esri World Imagery' });
  const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    { maxZoom: 19, attribution: '© OpenStreetMap' });
  esri.addTo(map);
  const baseLayers = { 'Satélite (Esri)': esri, 'Ruas (OSM)': osm };
  const layersCtl = L.control.layers(baseLayers).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);
  loadEsriLatestImagery(baseLayers, layersCtl);

/* Busca a release MAIS RECENTE do mosaico global da Esri (Wayback Imagery)
   e a torna o basemap padrao. Fallback silencioso p/ World Imagery classico. */
async function loadEsriLatestImagery(baseLayers, layersCtl) {
  try {
    const cfg = await (await fetch(
      'https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json'
    )).json();
    const releases = Object.keys(cfg).map(Number).filter(n => !isNaN(n));
    if (!releases.length) return;
    const latest = Math.max(...releases);
    const meta = cfg[latest];
    const url = meta.itemURL
      .replace('{level}', '{z}').replace('{row}', '{y}').replace('{col}', '{x}');
    const data = meta.releaseDate ? new Date(meta.releaseDate)
      .toLocaleDateString('pt-BR', { month: '2-digit', year: 'numeric' }) : '';
    const latestLayer = L.tileLayer(url, {
      maxZoom: 19,
      attribution: `Esri World Imagery · Wayback (release mais recente${data ? ' · ' + data : ''})`,
    });
    // sobe como padrao: remove o classico e adiciona o mais atualizado
    map.removeLayer(esri);
    latestLayer.addTo(map);
    layersCtl.removeLayer(esri);
    layersCtl.addBaseLayer(latestLayer, `🛰️ Satélite Esri — mais atualizado${data ? ' (' + data + ')' : ''}`);
    layersCtl.addBaseLayer(esri, 'Satélite (Esri, clássico)');
    console.log('[basemap] Esri Wayback release', latest, data);
  } catch (e) {
    console.warn('[basemap] Wayback indisponível, mantendo World Imagery clássico', e);
  }
}

  const drawn = new L.FeatureGroup();
  map.addLayer(drawn);
  state.drawn = drawn;

  map.addControl(new L.Control.Draw({
    draw: {
      polygon: { allowIntersection: false, showArea: true, metric: true,
                 shapeOptions: { color: '#4ade80', weight: 2 } },
      polyline: false, rectangle: false, circle: false,
      marker: false, circlemarker: false,
    },
    edit: { featureGroup: drawn },
  }));

  map.on(L.Draw.Event.CREATED, (e) => {
    drawn.clearLayers();
    drawn.addLayer(e.layer);
    onPolygon(e.layer);
  });
  map.on(L.Draw.Event.DELETED, () => {
    state.layer = null; state.geometry = null; state.valid = false;
    setStatus('Polígono removido.');
    updateAnalyzeBtn();
  });
}

function onPolygon(layer) {
  state.layer = layer;
  state.geometry = layer.toGeoJSON().geometry;
  const res = validatePolygon(state.geometry);
  state.valid = res.ok;
  layer.setStyle({ color: res.ok ? '#22c55e' : '#ef4444', weight: 2.5 });
  setStatus(res.msg, res.ok ? 'ok' : 'err');
  updateAnalyzeBtn();
}

function flyToMunicipio(slug) {
  fetch(`data/${slug}.geojson`).then(r => r.json()).then(g => {
    const l = L.geoJSON(g, { style: { color: '#f59e0b', weight: 2, fill: false, dashArray: '6 5' } }).addTo(map);
    map.flyToBounds(l.getBounds(), { padding: [30, 30] });
    setTimeout(() => map.removeLayer(l), 5000);
  }).catch(() => setStatus(`Não achei data/${slug}.geojson`, 'err'));
}
