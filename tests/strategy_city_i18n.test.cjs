const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const web = path.join(__dirname, '../ran_workbench/web');
// Qt and WebGL are external boundaries. Keep the real presentation functions,
// dictionaries, and editing model; this DOM keeps live input identity observable.
function environment(language = 'en') {
  const elements = new Map(), nodes = [];
  class Element {
    constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.value = ''; this.style = {}; this.clientWidth = 900; this.clientHeight = 500; nodes.push(this); }
    set textContent(value) { this.content = String(value ?? ''); this.children = []; }
    get textContent() { return (this.content || '') + this.children.map(c => c.textContent).join(''); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.content = ''; this.children = children; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    getAttribute(key) { return this.attributes[key]; }
    addEventListener(key, fn) { (this.listeners[key] ||= []).push(fn); }
    querySelector() { return this.children.find(n => ['INPUT', 'SELECT'].includes(n.tagName)); }
    querySelectorAll() { return []; }
  }
  const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const document = {
    getElementById: element, createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag), createTextNode: value => ({nodeValue: value, get textContent() { return this.nodeValue; }}),
    body: new Element('body'), documentElement: new Element('html'), activeElement: null,
    addEventListener() {}, querySelectorAll: selector => selector === '[data-ran-i18n]' ? nodes.filter(n => n.attributes['data-ran-i18n'] !== undefined) : [],
  };
  const window = {RAN_LANGUAGE: language, NRGraph: require('../ran_workbench/web/strategy_model.js'), NRViewport: require('../ran_workbench/web/strategy_viewport.js')};
  const context = vm.createContext({window, document, console, ResizeObserver: class {observe() {}}, requestAnimationFrame: fn => fn(), qt: {}, innerWidth: 1200, innerHeight: 800});
  const load = file => { const name = path.join(web, file); if (fs.existsSync(name)) vm.runInContext(fs.readFileSync(name, 'utf8'), context, {filename: file}); };
  load('i18n.js');
  return {context, window, document, element, get i18n() { return window.RANI18n; }, load, nodes};
}

test('Strategy localizes registry labels and switches language without replacing an unsaved input or altering the draft', async () => {
  const env = environment();
  env.load('i18n_strategy.js');
  const experiment = {name: '资源调度', settings: {slots: 12, mu: 0, n_prb: 10, seed: 1, channel_backend: 'rt'}, scene: {bs: [{id: 'BS-1', name: '业务缓存'}], ues: []}, graph: {nodes: [{id: 'power', kind: 'power', entity_id: 'BS-1', position: [20, 20], parameters: {power_dbm: 30}}], edges: []}};
  const result = {slots: 12, elapsed_s: 0.2, backend: {backend: 'reference_fixture'}, assumptions: {curve_extrapolation_used: false}, ues: [{ue_id: '资源调度', bs_id: 'BS-1', goodput_mbps: 1.5, delay_p95_ms: 2, first_bler: 0.1, mean_csi_age_slots: 2, feedback_bits: 12, delivered_packets: 5, retransmissions: 1}], timeline: [{time_ms: 1, ues: [{delivered_bytes: 100}]}], trace: [{slot: 0, type: 'feedback_drop', reason: 'queue_expired', ue_id: '资源调度'}]};
  const state = {experiment, revision: 1, document_id: 'same', job: {status: 'idle'}, progress: {}, busy: false, result};
  const registry = {power: {label: '发射功率与天线', layer: 'RF', owner: 'bs', inputs: {}, outputs: {radio: 'Radio'}, standard: '系统级策略', parameters: {power_dbm: {label: '总发射功率上限', type: 'number', unit: 'dBm'}}}};
  env.element('entity').value = 'all';
  env.element('event-type').value = 'all';
  env.context.qt = env.window.qt = {webChannelTransport: {}};
  env.context.QWebChannel = env.window.QWebChannel = class {constructor(_, callback) { callback({objects: {strategy: {event: {connect() {}}, command(raw, done) {const action = JSON.parse(raw).action; assert.ok(['get', 'draft'].includes(action)); done(JSON.stringify(action === 'get' ? {ok: true, registry, state} : {ok: true})); }}}}); }};
  env.load('strategy.js');
  await Promise.resolve();
  assert.equal(env.element('node-title').textContent, 'Transmit power and antenna');
  assert.match(env.element('node-form').textContent, /Total transmit power limit/);
  assert.equal(env.element('experiment-name').textContent, '资源调度');
  assert.match(env.element('result-summary').textContent, /Delivered 5 complete packets; 1 retransmissions/);
  assert.match(env.element('event-table').textContent, /Queue wait expired/);
  let input = env.element('node-form').children[0].children.find(n => n.tagName === 'INPUT');
  input.value = '42.5'; input.listeners.change[0]();
  input = env.element('node-form').children[0].children.find(n => n.tagName === 'INPUT');
  input.value = '51.0';
  const draftBefore = JSON.stringify(env.window.nrStrategy.getDraft());
  const resultsBefore = JSON.stringify(env.window.nrStrategy.getState().result), viewBefore = JSON.stringify(env.window.nrStrategy.getView());
  assert.equal(env.window.nrStrategy.getDraft().graph.nodes[0].parameters.power_dbm, 42.5);
  assert.equal(env.element('undo').disabled, false);
  env.i18n.setLanguage('zh-CN');
  assert.equal(env.element('node-title').textContent, '发射功率与天线');
  assert.equal(env.element('node-form').children[0].children.find(n => n.tagName === 'INPUT'), input);
  assert.equal(input.value, '51.0');
  assert.equal(JSON.stringify(env.window.nrStrategy.getDraft()), draftBefore);
  assert.equal(JSON.stringify(env.window.nrStrategy.getState().result), resultsBefore);
  assert.equal(JSON.stringify(env.window.nrStrategy.getView()), viewBefore);
  assert.match(env.element('result-summary').textContent, /交付 5 个完整包；重传 1 次/);
  assert.match(env.element('event-table').textContent, /排队等待过期/);
  env.i18n.setLanguage('en');
  assert.equal(env.element('node-title').textContent, 'Transmit power and antenna');
  assert.match(env.element('graph-count').textContent, /1 modules \/ 0 connections/);
  assert.equal(env.element('undo').disabled, false);
});

test('Strategy translates validation errors with IDs intact in the chosen language', () => {
  const env = environment(); env.load('i18n_strategy.js');
  const translate = value => env.window.RANStrategyI18n?.message(value) ?? value;
  assert.equal(translate('节点 BS-01/power: 参数名称不完整或未知'), 'Node BS-01/power: Parameter names are incomplete or unknown');
  assert.equal(translate('满缓存包大小超过服务 BS 缓存上限'), 'Full-buffer packet size exceeds the serving BS queue limit');
  env.i18n.setLanguage('zh-CN');
  assert.equal(translate('节点 BS-01/power: 参数名称不完整或未知'), '节点 BS-01/power: 参数名称不完整或未知');
});

test('City startup failures update immediately on language changes without restarting the viewer', async () => {
  const env = environment(); env.load('i18n_city.js'); env.load('city.js');
  await Promise.resolve(); await Promise.resolve();
  assert.equal(env.element('city-status').textContent, 'Model loading failed');
  assert.match(env.element('city-error').textContent, /Local Cesium resources are unavailable/);
  env.i18n.setLanguage('zh-CN');
  assert.equal(env.element('city-status').textContent, '模型加载失败');
  assert.match(env.element('city-error').textContent, /本地Cesium资源不可用/);
});

test('City model metadata switches language while preserving original model IDs and current DOM selection', () => {
  const env = environment(); env.load('i18n_city.js');
  env.context.cityFixture = {id: '模型编号', tile: 'T-1', level_code: 'L0', lod: null, vertices: 10, triangles: 3, bounds_native_m: [[0, 0, 0], [10, 10, 20]]};
  const source = fs.readFileSync(path.join(web, 'city.js'), 'utf8');
  // Exercise modelInfo with the same native manifest shape, bypassing only the
  // Cesium/Qt startup boundary. The renderer and language listeners are real.
  vm.runInContext(source.replace('start().catch(fail);', 'selected = cityFixture; modelInfo();'), env.context);
  const metadata = env.element('city-model-info');
  assert.equal(metadata.children[0].textContent, 'Model ID');
  assert.equal(metadata.children[1].textContent, '模型编号');
  assert.match(metadata.textContent, /10 vertices · 3 triangles/);
  const selectedDetail = metadata.children[1];
  env.i18n.setLanguage('zh-CN');
  assert.equal(metadata.children[0].textContent, '模型编号');
  assert.equal(metadata.children[1], selectedDetail);
  assert.match(metadata.textContent, /10 顶点 · 3 三角形/);
  assert.equal(env.window.cityAutomation.getState().selected_asset, '模型编号');
});
