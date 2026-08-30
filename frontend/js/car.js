/* car.js — integracao CAR/Sicar (MT): camada WMS, clique-para-selecionar,
   busca por codigo do imovel rural. Depende de: map, state, onPolygon,
   setStatus, updateAnalyzeBtn (globais do app). */
let carLayer = null;      // camada WMS no mapa
let carPicking = false;   // modo "clique para selecionar" ativo
let carHighlight = null;  // destaque do imovel clicado

function initCAR() {
  const toggle = document.getElementById('car-toggle');
  const pickBtn = document.getElementById('car-pick');
  const searchBtn = document.getElementById('car-search');
  const searchInput = document.getElementById('car-cod');

  toggle.addEventListener('change', () => {
    if (toggle.checked) {
      carLayer = L.tileLayer.wms(
        'https://geoserver.car.gov.br/geoserver/sicar/wms', {
          layers: 'sicar:sicar_imoveis_mt',
          format: 'image/png', transparent: true,
          version: '1.1.1', opacity: 0.85,
          attribution: 'Sicar/CAR · Min. Meio Ambiente',
        });
      carLayer.addTo(map);
      setStatus('Camada CAR visível — imóveis rurais em contorno laranja.', 'ok');
    } else if (carLayer) {
      map.removeLayer(carLayer);
      carLayer = null;
    }
  });

  pickBtn.addEventListener('click', () => {
    carPicking = !carPicking;
    pickBtn.classList.toggle('active', carPicking);
    pickBtn.textContent = carPicking ? '🖱️ Clique no imóvel no mapa…' : '🖱️ Selecionar imóvel por clique';
    map.getContainer().style.cursor = carPicking ? 'crosshair' : '';
  });

  map.on('click', (e) => { if (carPicking) carPickAt(e.latlng); });

  searchBtn.addEventListener('click', () => {
    const cod = searchInput.value.trim();
    if (cod) carSearchByCod(cod);
  });
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { const c = searchInput.value.trim(); if (c) carSearchByCod(c); }
  });
}

function _carPropsInfo(props) {
  const get = (...keys) => {
    for (const k of keys) if (props[k] != null) return props[k];
    return null;
  };
  return {
    cod: get('cod_imovel', 'COD_IMOVEL', 'codigo_imovel'),
    municipio: get('municipio', 'nom_munici'),
    uf: get('uf', 'cod_estado'),
    areaHa: get('area', 'num_area'),
    status: get('status_imovel', 'situacao'),
    tipo: get('tipo_imovel', 'tipo_imove'),
    modulos: get('m_fiscal', 'num_modulo'),
    condicao: get('condicao'),
  };
}

function _largestPolygon(geom) {
  /* CAR pode vir MultiPolygon — pega o anel de maior area */
  if (geom.type === 'Polygon') return geom;
  if (geom.type === 'MultiPolygon') {
    let best = null, bestA = -1;
    geom.coordinates.forEach(poly => {
      const a = turf.area(turf.polygon(poly));
      if (a > bestA) { bestA = a; best = poly; }
    });
    if (best) return { type: 'Polygon', coordinates: best };
  }
  return null;
}

function _selectCarFeature(feature, origem) {
  const geom = _largestPolygon(feature.geometry);
  if (!geom) { setStatus('✖ Geometria do imóvel não é poligonal.', 'err'); return; }

  if (carHighlight) { map.removeLayer(carHighlight); carHighlight = null; }
  state.drawn.clearLayers();
  const layer = L.geoJSON({ type: 'Feature', geometry: geom, properties: feature.properties }, {
    style: { color: '#f59e0b', weight: 3, fillOpacity: 0.08 },
  });
  layer.eachLayer(l => state.drawn.addLayer(l));
  const p = _carPropsInfo(feature.properties);
  const alvo = layer.getLayers()[0];
  alvo.bindPopup(
    `<b>Imóvel CAR</b><br>` +
    (p.cod ? `Código: <code>${p.cod}</code><br>` : '') +
    (p.municipio ? `Município: ${p.municipio}${p.uf ? '/' + p.uf : ''}<br>` : '') +
    (p.areaHa ? `Área CAR: ${(+p.areaHa).toLocaleString('pt-BR')} ha<br>` : '') +
    (p.modulos ? `Módulos fiscais: ${p.modulos}<br>` : '') +
    (p.status ? `Situação: ${p.status}<br>` : '') +
    (p.tipo ? `Tipo: ${p.tipo}<br>` : '') +
    (p.condicao ? `Condição: ${p.condicao}` : ''),
    { maxWidth: 320 });

  map.flyToBounds(alvo.getBounds(), { padding: [40, 40] });
  onPolygon(alvo);
  const st = document.getElementById('status');
  if (state.valid) {
    st.textContent += ` · Imóvel CAR ${origem}${p.cod ? ' (' + p.cod.slice(-6) + ')' : ''}`;
    alvo.openPopup();
  }
}

async function carPickAt(latlng) {
  setStatus('Consultando imóveis CAR no ponto…');
  const d = 0.003; // ~330 m de tolerancia ao clique
  try {
    const r = await fetch(
      `${API}/api/car/imoveis?bbox=${latlng.lng - d},${latlng.lat - d},${latlng.lng + d},${latlng.lat + d}`);
    if (!r.ok) throw new Error('falha na consulta CAR');
    const data = await r.json();
    const feats = data.features || [];
    if (!feats.length) {
      setStatus('Nenhum imóvel CAR nesse ponto — tente mais perto do contorno laranja.', 'err');
      return;
    }
    // escolhe a feicao que realmente contem o ponto clicado
    const pt = turf.point([latlng.lng, latlng.lat]);
    const hit = feats.find(f => {
      try { return turf.booleanPointInPolygon(pt, f); } catch { return false; }
    }) || feats[0];
    _selectCarFeature(hit, 'por clique');
  } catch (err) {
    setStatus('✖ ' + err.message, 'err');
  }
}

async function carSearchByCod(cod) {
  setStatus(`Buscando imóvel ${cod}…`);
  try {
    const r = await fetch(`${API}/api/car/imoveis?cod=${encodeURIComponent(cod)}`);
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || 'imóvel não encontrado');
    }
    const data = await r.json();
    if (!data.features || !data.features.length) {
      setStatus('✖ Código CAR não encontrado em MT.', 'err');
      return;
    }
    _selectCarFeature(data.features[0], 'por código');
  } catch (err) {
    setStatus('✖ ' + err.message, 'err');
  }
}
