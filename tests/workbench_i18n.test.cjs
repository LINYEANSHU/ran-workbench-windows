const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const web = path.join(__dirname, '../ran_workbench/web');
function load(preference, injected) {
  const storage = new Map(preference ? [['ran-language', preference]] : []);
  const document = {documentElement: {lang: ''}, readyState: 'loading', addEventListener() {}};
  const window = {document, RAN_LANGUAGE: injected, localStorage: {getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v)}};
  const context = vm.createContext({window, document, console});
  const filename = path.join(web, 'i18n.js');
  assert.ok(fs.existsSync(filename), 'the shared language runtime must exist');
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context);
  return {i18n: window.RANI18n, window, document, storage, context};
}

test('language preference survives changes and native injection takes priority', () => {
  const {i18n, storage, document} = load('en', 'zh-CN');
  i18n.register({'新建': 'New'});
  assert.equal(i18n.t('新建'), '新建');
  i18n.setLanguage('en');
  assert.equal(i18n.t('新建'), 'New');
  assert.equal(storage.get('ran-language'), 'en');
  assert.equal(document.documentElement.lang, 'en');
  assert.equal(load('en').i18n.language, 'en');
  assert.throws(() => i18n.setLanguage('unknown'), /language/i);
});

test('template substitution preserves user strings and numeric/schema values', () => {
  const {i18n} = load();
  i18n.register({'项目 {0} · {1} 个基站': 'Project {0} · {1} base stations', '新建': 'New'});
  const name = '新建 {1} <script>你好</script>';
  i18n.setLanguage('en');
  assert.equal(i18n.t('项目 {0} · {1} 个基站', name, 5), 'Project 新建 {1} <script>你好</script> · 5 base stations');
  assert.equal(i18n.t(name), name);
  assert.equal(i18n.t('sinr_db'), 'sinr_db');
  i18n.setLanguage('zh-CN');
  assert.equal(i18n.t('项目 {0} · {1} 个基站', name, 5), '项目 新建 {1} <script>你好</script> · 5 个基站');
});

test('listeners can update existing view bindings without mutating the model', () => {
  const {i18n} = load();
  i18n.register({'已绘制 {0} 个顶点': '{0} vertices drawn'});
  const model = {name: '新建', points: [1, 2, 3], result: {sinr_db: 17}};
  const original = JSON.stringify(model);
  let caption;
  const stop = i18n.onChange(() => { caption = i18n.t('已绘制 {0} 个顶点', model.points.length); });
  for (let i = 0; i < 3; i++) {
    i18n.setLanguage('en'); assert.equal(caption, '3 vertices drawn');
    i18n.setLanguage('zh-CN'); assert.equal(caption, '已绘制 3 个顶点');
  }
  assert.equal(JSON.stringify(model), original);
  stop(); i18n.setLanguage('en'); assert.equal(caption, '已绘制 3 个顶点');
});

test('workbench dictionary covers labels, help, chart text and result templates', () => {
  const {i18n, context} = load(undefined, 'en');
  const filename = path.join(web, 'i18n_workbench.js');
  assert.ok(fs.existsSync(filename), 'the workbench dictionary must exist');
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context);
  assert.equal(i18n.t('城市无线传播'), 'Urban Radio Propagation');
  assert.equal(i18n.t('频偏 / MHz'), 'Frequency offset / MHz');
  assert.equal(i18n.t('项目名称'), 'Project name');
  assert.equal(i18n.t('已绘制 {0} 个顶点', 4), '4 vertices drawn');
  assert.equal(i18n.t('请修正表单中标出的值。'), 'Please correct the highlighted form values.');
});

test('static labels switch repeatedly, while user text and edited form values stay intact', () => {
  const {i18n, document} = load();
  i18n.register({'新建': 'New', '项目名称': 'Project name'});
  const element = (tag, text, ignore = false) => {
    const attrs = new Map();
    const el = {nodeType: 1, tagName: tag, childNodes: [], value: '用户未保存的输入',
      closest: () => ignore ? el : null, hasAttribute: key => attrs.has(key),
      getAttribute: key => attrs.get(key), setAttribute: (key,v) => attrs.set(key,v)};
    el.childNodes = [{nodeType: 3, nodeValue: text, parentElement: el}];
    return el;
  };
  const button = element('BUTTON', '新建'), name = element('SPAN', '新建', true), input = element('INPUT', '');
  input.setAttribute('aria-label', '项目名称');
  document.childNodes = [button, name, input];
  for (let i = 0; i < 3; i++) {
    i18n.setLanguage('en'); assert.equal(button.childNodes[0].nodeValue, 'New');
    assert.equal(input.getAttribute('aria-label'), 'Project name');
    assert.equal(name.childNodes[0].nodeValue, '新建'); assert.equal(input.value, '用户未保存的输入');
    i18n.setLanguage('zh-CN'); assert.equal(button.childNodes[0].nodeValue, '新建');
  }
  button.childNodes[0].nodeValue = '项目名称';
  i18n.setLanguage('en'); assert.equal(button.childNodes[0].nodeValue, 'Project name');
  i18n.setLanguage('zh-CN'); assert.equal(button.childNodes[0].nodeValue, '项目名称');
});

test('workbench live caption bindings preserve result data and edit buffers on repeated switches', () => {
  const {i18n, context, document} = load();
  i18n.register({'已绘制 {0} 个顶点': '{0} vertices drawn'});
  const source = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
  const start = source.indexOf('  const uiBindings =');
  assert.ok(start >= 0, 'the app must keep live presentation bindings');
  const helpers = source.slice(start, source.indexOf('  const C =', start));
  const element = {textContent: '', isConnected: true, setAttribute() {}};
  const result = {points: [1,2,3], name: '新建'};
  document.contains = node => node === element;
  context.element = element; context.result = result;
  vm.runInContext(helpers + '\nlocalizedText(element, () => tr("已绘制 {0} 个顶点", result.points.length));', context);
  const original = JSON.stringify(result);
  for (let i = 0; i < 3; i++) {
    i18n.setLanguage('en'); assert.equal(element.textContent, '3 vertices drawn');
    i18n.setLanguage('zh-CN'); assert.equal(element.textContent, '已绘制 3 个顶点');
  }
  assert.equal(JSON.stringify(result), original);
});

test('language changes do not replace selected structured path details with a stale empty-state caption', () => {
  const {i18n, context} = load();
  i18n.register({'无有效路径': 'No valid paths'});
  const source = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
  const start = source.indexOf('  const uiBindings =');
  const helpers = source.slice(start, source.indexOf('  const C =', start));
  let content = '';
  const element = {isConnected: true, firstChild: null, setAttribute() {},
    get textContent() { return content; },
    set textContent(value) { content = value; this.firstChild = {text: value}; }};
  context.element = element;
  vm.runInContext(helpers + '\nlocalizedText(element, () => tr("无有效路径"));', context);
  const selectedDetails = {kind: 'selected-path', coefficient: [0.2, 0.1]};
  // The result renderer replaced the previous scalar caption with a detail tree.
  element.firstChild = selectedDetails;
  i18n.setLanguage('en');
  assert.equal(element.firstChild, selectedDetails);
  i18n.setLanguage('zh-CN');
  assert.equal(element.firstChild, selectedDetails);
});

function loadBindings() {
  const loaded = load(undefined, 'en');
  const source = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
  const start = source.indexOf('  const uiBindings =');
  vm.runInContext(source.slice(start, source.indexOf('  const C =', start)), loaded.context);
  return {...loaded, source};
}

test('literal user IDs and names never become UI translations, including caption-cache collisions', () => {
  const {i18n, context, document, source} = loadBindings();
  i18n.register({'新建': 'New', '{0} · {1} · 电磁材质为假定值': '{0} · {1} · Assumed electromagnetic material'});
  i18n.t('{0} · {1} · 电磁材质为假定值', 'B-01', '手工建模');
  document.createElement = () => ({textContent: '', isConnected: true, setAttribute() {}});
  context.element = document.createElement();
  context.row = {append() {}};
  vm.runInContext(source.slice(source.indexOf('  function option('), source.indexOf('  function fillOptions(')), context);
  vm.runInContext(source.slice(source.indexOf('  function resultCell('), source.indexOf('  function renderNetwork(')), context);
  vm.runInContext('localizedText(element, () => "新建");', context);
  assert.equal(context.element.textContent, '新建');
  assert.equal(vm.runInContext('resultCell(row, () => "新建").textContent', context), '新建');
  assert.equal(vm.runInContext('option("B-01", "B-01 · 手工建模 · 电磁材质为假定值").textContent', context), 'B-01 · 手工建模 · 电磁材质为假定值');
  vm.runInContext('globalThis.uiOption = option("ui", () => tr("新建"));', context);
  assert.equal(context.uiOption.textContent, 'New');
  i18n.setLanguage('zh-CN'); assert.equal(context.uiOption.textContent, '新建');
  i18n.setLanguage('zh-CN'); i18n.setLanguage('en');
  assert.equal(context.element.textContent, '新建');
  assert.equal(context.uiOption.textContent, 'New');
});

test('render cleanup releases detached caption bindings without waiting for a language switch', async () => {
  const {context} = loadBindings();
  context.element = {textContent: '', isConnected: true, setAttribute() {}};
  vm.runInContext('localizedText(element, () => "caption");', context);
  context.element.isConnected = false;
  await Promise.resolve();
  assert.equal(vm.runInContext('uiBindings.has(element)', context), false);
});

test('rendered-caption cache evicts both inserted aliases when its capacity is reached', () => {
  const {i18n} = load(undefined, 'en');
  i18n.register({'样本 {0}': 'Sample {0}'});
  for (let i = 0; i < 15000; i++) i18n.t('样本 {0}', i);
  i18n.setLanguage('zh-CN');
  assert.equal(i18n.rendered('Sample 3000'), 'Sample 3000');
  assert.equal(i18n.rendered('Sample 14999'), '样本 14999');
});
