(() => {
  'use strict';
  const C = window.Cesium, $ = id => document.getElementById(id);
  const I = window.RANI18n, tr = (key, ...args) => I ? I.t(key, ...args) : String(key ?? '').replace(/\{(\d+)\}/g, (_, i) => String(args[i] ?? `{${i}}`));
  function refreshBinding(el) { const b = el.__ranI18n; if (b) b.node.nodeValue = tr(b.key, ...b.args.map(value => typeof value === 'function' ? value() : value)); }
  function bind(el, key, ...args) { el.setAttribute('data-i18n-ignore', ''); el.setAttribute('data-ran-i18n', ''); el.textContent = ''; el.__ranI18n = {key, args, node: document.createTextNode('')}; el.append(el.__ranI18n.node); refreshBinding(el); return el; }
  function caption(id, key, ...args) { return bind($(id), key, ...args); }
  function literal(el, value) { el.setAttribute('data-i18n-ignore', ''); delete el.__ranI18n; el.textContent = value; }
  I?.onChange(() => { document.querySelectorAll('[data-ran-i18n]').forEach(refreshBinding); I.translatePage(); });
  I?.translatePage();
  let bridge, viewer, manifest, nativeFrame, currentOriginal = null, selected = null, bank = 'all', mode = 'overview';

  let workspaceActive = true;
  window.ranSetWorkspaceActive = active => {
    workspaceActive = Boolean(active);
    if (viewer && !viewer.isDestroyed()) {
      viewer.useDefaultRenderLoop = workspaceActive;
      if (workspaceActive) { viewer.resize(); viewer.scene.requestRender(); }
    }
  };
  const models = new Map();
  let ready = false, error = '', loaded = 0;
  const count = value => Number(value).toLocaleString(I?.language === 'en' ? 'en-US' : 'zh-CN');

  function fail(problem) {
    error = problem?.message || String(problem);
    $('city-error').hidden = false;
    caption('city-error', '模型未能显示：{0}。请检查本地数据完整性，或返回几何总览。', () => tr(error));
    caption('city-status', '模型加载失败');
  }
  function nativeWorld(point) { return C.Matrix4.multiplyByPoint(nativeFrame, C.Cartesian3.fromArray(point), new C.Cartesian3()); }
  function boundsFor(items) {
    return [0, 1].map(side => [0, 1, 2].map(axis => Math[side ? 'max' : 'min'](...items.map(item => item.bounds_native_m[side][axis]))));
  }
  function focus(bounds, scale = 1) {
    const center = bounds[0].map((value, axis) => (value + bounds[1][axis]) / 2);
    const radius = Math.max(30, Math.hypot(...bounds[0].map((value, axis) => bounds[1][axis] - value)) / 2);
    viewer.camera.lookAtTransform(C.Matrix4.IDENTITY);
    viewer.camera.flyToBoundingSphere(new C.BoundingSphere(nativeWorld(center), radius), {
      duration: 0, offset: new C.HeadingPitchRange(C.Math.toRadians(-15), C.Math.toRadians(-35), radius * 2.4 * scale)
    });
    viewer.scene.requestRender();
  }
  function filterModels() {
    for (const tile of manifest.tiles) {
      const model = models.get(tile.id);
      if (model) model.show = mode === 'overview' && (bank === 'all' || tile.bank === bank);
    }
    document.querySelectorAll('[data-bank]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.bank === bank)));
    viewer.scene.requestRender();
  }
  function assetOptions() {
    const term = $('city-search').value.trim().toUpperCase(), tile = $('city-tile').value;
    const choices = manifest.assets.filter(asset => asset.kind === 'B' && (!tile || asset.tile === tile)
      && (bank === 'all' || asset.bank === bank) && asset.id.includes(term));
    const list = $('city-asset'); list.replaceChildren();
    for (const asset of choices.slice(0, 160)) {
      const option = document.createElement('option'); option.value = asset.id;
      option.textContent = `${asset.id} · ${asset.lod ? 'LOD' + asset.lod : asset.level_code}`;
      list.append(option);
    }
    caption('city-asset-count', choices.length > 160 ? '共 {0} 个模型，显示前160项；可按图幅或编号缩小范围。' : '{0} 个建筑／裙楼模型', () => count(choices.length));
    selected = choices[0] || null;
    if (selected) list.value = selected.id;
    modelInfo();
  }
  function modelInfo() {
    $('city-original').disabled = !selected;
    const target = $('city-model-info'); target.replaceChildren();
    if (!selected) return;
    const middle = selected.bounds_native_m[0].map((value, axis) => (value + selected.bounds_native_m[1][axis]) / 2);
    const rows = [['模型编号', selected.id], ['源图幅／细节等级', () => `${selected.tile} ／ ${selected.lod ? 'LOD' + selected.lod : tr('地形')} · ${selected.level_code}`],
      ['原始几何', () => tr('{0} 顶点 · {1} 三角形', count(selected.vertices), count(selected.triangles))],
      ['平面中心 E / N', `${middle[0].toFixed(3)} / ${middle[1].toFixed(3)} m`],
      ['HKPD高程范围', `${selected.bounds_native_m[0][2].toFixed(3)} — ${selected.bounds_native_m[1][2].toFixed(3)} m`]];
    for (const [label, value] of rows) {
      const term = document.createElement('dt'), detail = document.createElement('dd');
      bind(term, label); if (typeof value === 'function') bind(detail, '{0}', value); else literal(detail, value); target.append(term, detail);
    }
  }
  async function inspectOriginal(id) {
    const asset = manifest.assets.find(item => item.id === id);
    if (!asset) throw new Error('未找到所选模型');
    selected = asset; modelInfo();
    caption('city-status', '正在读取 {0} 的原始贴图…', asset.id);
    $('city-original').disabled = true;
    const original = await C.Model.fromGltfAsync({url: asset.url, modelMatrix: nativeFrame,
      upAxis:C.Axis.Y,forwardAxis:C.Axis.X,
      id: {kind: 'original', id: asset.id}, incrementallyLoadTextures: false,
      lightColor: new C.Cartesian3(2, 2, 2)});
    if (currentOriginal) viewer.scene.primitives.remove(currentOriginal);
    currentOriginal = viewer.scene.primitives.add(original);
    mode = 'original'; filterModels(); focus(asset.bounds_native_m, 1.05);
    caption('city-view-title', '单栋原始模型与贴图');
    caption('city-view-description', '{0} · {1} · 官方原始资源', asset.id, asset.lod ? 'LOD' + asset.lod : asset.level_code);
    caption('city-status', '原始贴图已载入');
    $('city-original').disabled = false;
    viewer.scene.requestRender();
  }
  function overview(fit = true) {
    if (currentOriginal) { viewer.scene.primitives.remove(currentOriginal); currentOriginal = null; }
    mode = 'overview'; filterModels();
    caption('city-view-title', '精细几何总览');
    caption('city-view-description', '原始网格未简化 · 总览使用材质着色 · 原始贴图可单栋检查');
    caption('city-status', '{0} / {1} 个图幅已载入', loaded, manifest.tiles.length);
    if (fit) {
      const visible = manifest.tiles.filter(tile => bank === 'all' || tile.bank === bank);
      if (visible.length) focus(boundsFor(visible));
    }
  }
  function state() {
    let originalBounds = null;
    if (currentOriginal?.ready && selected) {
      const sphere = currentOriginal.boundingSphere;
      const center = selected.bounds_native_m[0].map((value, axis) => (value + selected.bounds_native_m[1][axis]) / 2);
      originalBounds = {radius_m:sphere.radius, center_error_m:C.Cartesian3.distance(sphere.center,nativeWorld(center))};
    }
    return {ready, error, mode, bank, tiles_loaded: loaded, tiles_total: manifest?.tiles.length || 0,
      complete: manifest?.complete || false, statistics: manifest?.statistics || null,
      selected_asset: selected?.id || null, original_loaded: !!currentOriginal,
      original_ready: !!currentOriginal?.ready, original_bounds:originalBounds, canvas: !!viewer?.scene.canvas,
      overflow: document.documentElement.scrollWidth > innerWidth,
      width: innerWidth, height: innerHeight, rf_supported: false};
  }
  async function action(data) {
    if (data.method === 'get') return state();
    if (data.method === 'asset') { await inspectOriginal(data.id); return state(); }
    if (data.method === 'overview') { overview(); return state(); }
    if (data.method === 'camera') {
      if (data.preset === 'north' || data.preset === 'south') bank = data.preset;
      else if (data.preset === 'overview') bank = 'all';
      overview(false); assetOptions();
      if (data.tile) {
        const tile = manifest.tiles.find(item => item.id === data.tile);
        if (!tile) throw new Error('未找到图幅');
        $('city-tile').value = tile.id; assetOptions(); focus(tile.bounds_native_m);
      } else {
        const visible = manifest.tiles.filter(tile => bank === 'all' || tile.bank === bank);
        focus(boundsFor(visible));
      }
      return state();
    }
    throw new Error('未知城市视图操作');
  }
  window.cityAutomation = {execute(ticket, data) {
    Promise.resolve().then(() => action(data)).then(view => bridge.automationReply(ticket, JSON.stringify({ok:true,view})),
      problem => bridge.automationReply(ticket, JSON.stringify({ok:false,error:String(problem.message || problem)})));
    return true;
  }, getState: state};

  async function start() {
    if (!C) throw new Error('本地Cesium资源不可用');
    await new Promise(resolve => new QWebChannel(qt.webChannelTransport, channel => {bridge = channel.objects.bridge; resolve();}));
    const config = await new Promise(resolve => bridge.command(JSON.stringify({action:'get_config'}), raw => resolve(JSON.parse(raw))));
    if (!config.ok) throw new Error(config.error);
    const response = await fetch(config.manifest_url);
    if (!response.ok) throw new Error('无法读取城市清单');
    manifest = await response.json();
    C.Ion.defaultAccessToken = '';
    viewer = new C.Viewer('city-canvas', {baseLayer:false, terrainProvider:new C.EllipsoidTerrainProvider(),
      animation:false,timeline:false,baseLayerPicker:false,geocoder:false,homeButton:false,sceneModePicker:false,
      navigationHelpButton:false,fullscreenButton:false,infoBox:false,selectionIndicator:false,
      skyBox:false,skyAtmosphere:false,sun:false,moon:false,requestRenderMode:true,
      maximumRenderTimeChange:Infinity,contextOptions:{webgl:{preserveDrawingBuffer:true,alpha:false}}});
    window.ranSetWorkspaceActive(workspaceActive);
    viewer.scene.globe.show = false; viewer.scene.fog.enabled = false;
    viewer.scene.backgroundColor = C.Color.fromCssColorString('#161c1a');
    viewer.scene.screenSpaceCameraController.minimumZoomDistance = 1;
    viewer.scene.screenSpaceCameraController.maximumZoomDistance = 100000;
    viewer.scene.screenSpaceCameraController.enableCollisionDetection = false;
    viewer.scene.renderError.addEventListener((_scene, problem) => fail(problem));
    const coordinate = manifest.coordinate_system, anchor = coordinate.anchor_native_m;
    const frame = C.Transforms.eastNorthUpToFixedFrame(C.Cartesian3.fromDegrees(...coordinate.render_anchor_deg, 0));
    nativeFrame = C.Matrix4.multiplyByTranslation(frame, new C.Cartesian3(-anchor[0],-anchor[1],-anchor[2]),new C.Matrix4());
    const lightDirection = C.Matrix4.multiplyByPointAsVector(frame,new C.Cartesian3(-0.6,0.4,-1),new C.Cartesian3());
    viewer.scene.light = new C.DirectionalLight({direction:C.Cartesian3.normalize(lightDirection,lightDirection),intensity:2});
    caption('city-summary', '{0} 个建筑／裙楼模型 · {1} 个三角形', () => count(manifest.statistics.building_models), () => count(manifest.statistics.triangles));
    $('city-partial').hidden = manifest.complete;
    literal($('city-attribution'), manifest.source.attribution);
    literal($('city-source'), `${manifest.source.provider} · 3D Visualisation Map (Individualised models)`);
    for (const tile of manifest.tiles) {
      const option = document.createElement('option'); option.value = tile.id;
      bind(option, '{0} · {1} · {2} 模型', tile.id, () => tr(tile.bank === 'north' ? '九龙岸' : '香港岛岸'), tile.buildings);
      $('city-tile').append(option);
    }
    $('city-tile').addEventListener('change', () => {
      assetOptions(); const tile = manifest.tiles.find(item => item.id === $('city-tile').value);
      if (tile) {overview(false); focus(tile.bounds_native_m); caption('city-tile-info', '{0} 个原始模型 · {1} 三角形', () => count(tile.models), () => count(tile.triangles));}
      else overview();
    });
    $('city-search').addEventListener('input', assetOptions);
    $('city-asset').addEventListener('change', () => {selected = manifest.assets.find(item => item.id === $('city-asset').value); modelInfo();});
    $('city-original').addEventListener('click', () => inspectOriginal(selected.id).catch(fail));
    $('city-overview').addEventListener('click', () => overview());
    $('city-capture').addEventListener('click', () => bridge.command(JSON.stringify({action:'save_screenshot'}), raw => {const result=JSON.parse(raw);if(!result.ok)fail(result.error);}));
    document.querySelectorAll('[data-bank]').forEach(button => button.addEventListener('click', () => {
      bank=button.dataset.bank; $('city-tile').value=''; assetOptions(); overview();
    }));
    assetOptions(); focus(manifest.bounds_native_m);
    // One combined mesh per official tile keeps the overview bounded; no
    // thousands-of-textures request burst is made by a whole-harbour camera.
    for (const tile of manifest.tiles) {
      const translation = C.Matrix4.fromTranslation(C.Cartesian3.fromArray(tile.origin_native_m));
      const matrix = C.Matrix4.multiply(nativeFrame, translation, new C.Matrix4());
      const model = await C.Model.fromGltfAsync({url:tile.url,modelMatrix:matrix,
        upAxis:C.Axis.Y,forwardAxis:C.Axis.X,
        id:{kind:'tile',id:tile.id},lightColor:new C.Cartesian3(1.6,1.6,1.6)});
      models.set(tile.id,viewer.scene.primitives.add(model)); loaded += 1;
      caption('city-status', '正在载入图幅 {0} / {1}', loaded, manifest.tiles.length);
      caption('city-render-count', '{0} / {1} 图幅 · 原始几何', loaded, manifest.tiles.length);
      viewer.scene.requestRender();
    }
    viewer.scene.postRender.addEventListener(() => {
      if (!ready && loaded === manifest.tiles.length && [...models.values()].every(model => model.ready)) {
        ready = true; document.body.dataset.ready='true';
        caption('city-status', '{0} 个图幅已载入 · 本地数据', loaded);
      }
    });
    overview();
  }
  start().catch(fail);
})();
