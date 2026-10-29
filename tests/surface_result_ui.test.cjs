const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../ran_workbench/web/app.js'), 'utf8');

// Exercise the production result renderer in a VM with DOM/scene boundaries
// supplied here; no WebGL or Qt startup is needed for metadata formatting.
const renderer = source.slice(source.indexOf('  function renderResult() {'), source.indexOf('  function renderProbeResultSection() {'));
function render(result) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {options: [{}, {}, {}], textContent: '', hidden: false});
    return elements.get(id);
  };
  const metadata = new Map();
  const context = {
    tr: (text, ...args) => text.replace(/\{(\d+)\}/g, (_, index) => String(args[index])),
    localizedText: (element, factory) => { element.textContent = factory(); },
    localizedAttribute: (element, name, factory) => { element[name] = factory(); },
    appState: {result}, $: element,
    selectedPath: null, selectedPointIndex: null, selectedUeId: null, selectedLinkId: null,
    selectedDeviceId: null, activeDeviceKind: 'bs', currentRadius: 'all', viewer: null,
    finite: Number.isFinite, format: (value, digits = 2) => Number.isFinite(value) ? value.toLocaleString('zh-CN', {maximumFractionDigits: digits}) : '—',
    coord: value => value.join(','), project: () => ({bs: [{id: 'BS-1'}], ues: []}),
    values: (id, entries) => metadata.set(id, Object.fromEntries(typeof entries === 'function' ? entries() : entries)),
    fillOptions() {}, setRadiusFilter() {}, renderProbeResultSection() {}, renderUeResult() {},
    warningText: value => value,
  };
  vm.runInNewContext(renderer + '\nrenderResult();', context);
  return {metadata, elements};
}

for (const [computed, requested, excluded] of [[3, 20, 17], [0, 20, 20]]) {
  test(`spherical metadata shows requested/excluded from sampling with ${computed} valid cells`, () => {
    const result = {
      mode: 'coverage', points: Array.from({length: computed}, () => ({})), ues: [],
      sampling: {requested, excluded, computed},
      surface: {center_m: [0, 0, 25], subdivisions: 0, radii_m: [10], below_ground_cells: 10, building_cells: excluded - 10},
      surface_meshes: [{radius_m: 10, vertices_m: [], triangles: [], point_indices: []}],
    };
    const {metadata, elements} = render(result);
    assert.equal(metadata.get('surface-metadata')['请求 / 排除面片'], `${requested} / 排除 ${excluded} (地下 10，建筑 ${excluded - 10})`);
    assert.equal(elements.get('surface-result-section').hidden, false);
    assert.match(elements.get('result-summary').textContent, new RegExp(`返回 ${computed} 个有效测量面片`));
  });
}
