const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const implementation=()=>{const file=path.join(__dirname,'../ran_workbench/web/multi_bs.js');assert.ok(fs.existsSync(file),'All-BS view model is required');return require(file);};

test('all station selection returns every link while station drill-down remains available',()=>{
  const m=implementation(),ue={links:[{bs_id:'A'},{bs_id:'B'}]};
  assert.equal(m.linksFor(ue,'').length,2);
  assert.deepEqual(m.linksFor(ue,'B'),[ue.links[1]]);
});
test('a legal station ID named all is individually selectable',()=>{
  const m=implementation(),ue={links:[{bs_id:'all'},{bs_id:'B'}]};
  assert.deepEqual(m.linksFor(ue,'all'),[ue.links[0]]);
  assert.equal(m.linksFor(ue,null).length,2);
});
test('all station paths preserve station identity and per-station display cap',()=>{
  const m=implementation(),links=['A','B'].map(bs_id=>({bs_id,paths:Array.from({length:60},(_,i)=>({delay_ns:i}))}));
  const rows=m.pathEntries(links);
  assert.equal(rows.length,100);assert.equal(rows[0].bs_id,'A');assert.equal(rows[50].bs_id,'B');
  assert.equal(rows[99].path_index,49);
});
test('network default uses SINR for multiple stations and preserves explicit choice',()=>{
  const m=implementation();assert.equal(m.initialMetric(3,'received',false),'sinr_db');
  assert.equal(m.initialMetric(3,'received',true),'received');
  assert.equal(m.initialMetric(1,'received',false),'received');
});
test('all-station CFR comparison keeps independent curves and missing channels',()=>{
  const m=implementation(),links=[{bs_id:'A',cfr:{offsets_hz:[0,1],gain_db:[-50,-51]}},{bs_id:'B',cfr:{offsets_hz:[0,1],gain_db:[-60,-61]}}];
  assert.deepEqual(m.cfrSeries(links).map(s=>[s.bs_id,s.gains]),[['A',[-50,-51]],['B',[-60,-61]]]);
});
test('heatmap all-station total power stays global when inspecting one serving BS',()=>{
  const {valueAt}=require('../ran_workbench/web/heatmap.js');
  const point={network:{total_received_dbm:-56.9897,per_bs:[{bs_id:'A',signal_dbm:-60}]}};
  assert.equal(valueAt(point,{metric:'total_received_dbm',bsId:'A'}),-56.9897);
});
