const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const api = () => require(path.resolve(__dirname, '../ran_workbench/web/heatmap.js'));
const sampling = {min_m:[0,0,1.5],max_m:[20,20,21.5],spacing_m:[10,10,10]};
const point = (x,y,z,p) => ({position_m:[x,y,z],links:[{bs_id:'BS-01',incoherent_dbm:p}]});
const options = extra => ({aggregation:'slice',height:1.5,zMin:0,zMax:50,
  metric:'received',powerKey:'incoherent_dbm',bsId:'',threshold:-80,...extra});

test('slice uses one actual layer and reports it without mixing heights', () => {
  const result = api().project([point(0,0,1.5,-100),point(0,0,11.5,-40)],sampling,options({height:8}));
  assert.equal(result.actualHeight,11.5);
  assert.equal(result.cells.length,1);
  assert.equal(result.cells[0].value,-40);
  assert.equal(result.cells[0].total,1);
});
test('linear power mean differs from arithmetic dBm average', () => {
  const result = api().project([point(0,0,1.5,-100),point(0,0,11.5,-40)],sampling,options({aggregation:'mean'}));
  assert.ok(Math.abs(result.cells[0].value - (-43.0102956137)) < 1e-8);
});
test('P10 reports weak outdoor samples with nearest rank', () => {
  const s = {...sampling,max_m:[20,20,91.5]};
  const pts = Array.from({length:10},(_,i) => point(0,0,1.5+10*i,-100+10*i));
  assert.equal(api().project(pts,s,options({aggregation:'p10',zMax:100})).cells[0].value,-100);
});
test('unknown paths leave means gray and are not discarded as selection bias', () => {
  const pts = [point(0,0,1.5,null),point(0,0,11.5,-40)];
  const c = api().project(pts,sampling,options({aggregation:'mean'})).cells[0];
  assert.equal(c.value,null); assert.equal(c.missing,1); assert.equal(c.total,2);
  assert.equal(api().project(pts,sampling,options({aggregation:'coverage'})).cells[0].value,50);
});
test('exact coherent zero participates in power mean and is not unknown', () => {
  const a = point(0,0,1.5,null), b = point(0,0,11.5,null);
  a.links[0] = {bs_id:'BS-01',coherent_dbm:null,coherent_zero:true};
  b.links[0] = {bs_id:'BS-01',coherent_dbm:0};
  const c = api().project([a,b],sampling,options({aggregation:'mean',powerKey:'coherent_dbm'})).cells[0];
  assert.ok(Math.abs(c.value+3.01029995664) < 1e-10); assert.equal(c.missing,0);
});
test('cells reach requested outer bounds and clicks select original samples', () => {
  const r = api().project([point(0,0,1.5,-80),point(10,0,1.5,-70),point(20,0,1.5,-60)],sampling,options());
  assert.deepEqual(r.cells[0].bounds,[0,0,5,5]);
  assert.deepEqual(r.cells[2].bounds,[15,0,20,5]);
  assert.equal(api().cellAt(r,19,2).index,2);
  assert.equal(api().cellAt(r,-1,2),null);
  assert.equal(api().cellAt(r,10,12),null);
});
test('SINR selection uses the requested BS rather than recoloring its RSS', () => {
  const p = point(0,0,1.5,-60);
  p.network={sinr_db:2,per_bs:[{bs_id:'BS-01',sinr_db:-5}]};
  assert.equal(api().project([p],sampling,options({metric:'sinr_db',bsId:'BS-01'})).cells[0].value,-5);
});
test('non-power dB ratios average in linear ratio space', () => {
  const pts=[point(0,0,1.5,-60),point(0,0,11.5,-60)];
  pts[0].network={sinr_db:0};pts[1].network={sinr_db:20};
  const c=api().project(pts,sampling,options({aggregation:'mean',metric:'sinr_db'})).cells[0];
  assert.ok(Math.abs(c.value-10*Math.log10(50.5))<1e-10);
});
test('invalid ranges and spherical cells cannot be silently projected', () => {
  assert.throws(()=>api().project([],sampling,options({aggregation:'mean',zMin:50,zMax:0})),/高度|range/);
  assert.throws(()=>api().project([{...point(0,0,1.5,-60),sample_kind:'surface_cell'}],sampling,options()),/球面|surface/);
});
test('a fixed slice ignores inactive height-range and threshold drafts', () => {
  const r=api().project([point(0,0,1.5,-60)],sampling,options({zMin:60,zMax:50,threshold:null}));
  assert.equal(r.cells[0].value,-60);
});
