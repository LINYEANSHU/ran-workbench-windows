/* Pure grid projection. No interpolation, native scene, or invented RF data. */
(function (root) {
  'use strict';
  const finite = v => typeof v === 'number' && Number.isFinite(v);
  function valueAt(point, options) {
    if (options.metric === 'total_received_dbm') {
      const total=point.network?.total_received_dbm;
      return finite(total) ? total : null;
    }
    if (options.metric !== 'received') {
      const n = options.bsId ? point.network?.per_bs?.find(r => r.bs_id === options.bsId) : point.network;
      const value = n?.[options.metric];
      if (finite(value)) return value;
      if (options.metric === 'carrier_joint_dbm' && n?.carrier_joint_zero) return -Infinity;
      return null;
    }
    const links = options.bsId ? (point.links || []).filter(l => l.bs_id === options.bsId) : (point.links || []);
    let best = null;
    for (const link of links) {
      const v = link[options.powerKey];
      if (finite(v)) best = best === null ? v : Math.max(best, v);
      else if ((options.powerKey === 'coherent_dbm' && link.coherent_zero) ||
               (options.powerKey === 'wideband_dbm' && link.wideband_zero)) best ??= -Infinity;
    }
    return best;
  }
  function project(points, sampling, input) {
    const o = {aggregation:'slice',height:1.5,zMin:0,zMax:50,metric:'received',
      powerKey:'incoherent_dbm',bsId:'',threshold:-80,...input};
    if (!['slice','mean','p10','coverage'].includes(o.aggregation)) throw new Error('未知高度统计方式。');
    // Inactive drafts are not inputs to the selected statistic.
    if (o.aggregation === 'slice') {o.zMin=0;o.zMax=0;}
    else o.height=1.5;
    if (o.aggregation !== 'coverage') o.threshold=-80;
    if (![o.height,o.zMin,o.zMax,o.threshold].every(finite) || o.height < 0 || o.zMin < 0 || o.zMax < o.zMin) throw new Error('高度范围和门限需要有效数字，下界不能大于上界。');
    if (points.some(p => p.sample_kind === 'surface_cell')) throw new Error('球面面积平均不能压缩为体网格高度统计；请计算平面热力图。');
    const lo = sampling.min_m, hi = sampling.max_m, step = sampling.spacing_m;
    if (![lo,hi,step].every(a => Array.isArray(a) && a.length === 3 && a.every(finite)) || step.some(v => v <= 0) || hi.some((v,i) => v < lo[i])) throw new Error('无效的体网格采样范围。');
    const count = lo.map((v,i) => Math.floor((hi[i]-v)/step[i] + 1e-9)+1);
    const zIndex = Math.max(0,Math.min(count[2]-1,Math.round((o.height-lo[2])/step[2])));
    const actualHeight = lo[2]+zIndex*step[2], byKey = new Map();
    const edges = (i,axis) => [i === 0 ? lo[axis] : lo[axis]+(i-.5)*step[axis],
      i === count[axis]-1 ? hi[axis] : lo[axis]+(i+.5)*step[axis]];
    for (const [index,p] of points.entries()) {
      const [x,y,z] = p.position_m;
      if (o.aggregation === 'slice' ? Math.abs(z-actualHeight) > Math.max(1e-6,step[2]*1e-7) : z < o.zMin-1e-6 || z > o.zMax+1e-6) continue;
      const ix = Math.round((x-lo[0])/step[0]), iy = Math.round((y-lo[1])/step[1]);
      if (ix < 0 || ix >= count[0] || iy < 0 || iy >= count[1]) continue;
      const key = `${ix},${iy}`;
      if (!byKey.has(key)) {
        const [x0,x1] = edges(ix,0), [y0,y1] = edges(iy,1);
        byKey.set(key,{ix,iy,index,bounds:[x0,y0,x1,y1],total:0,missing:0,valid:0,values:[],zMin:z,zMax:z});
      }
      const cell = byKey.get(key), value = valueAt(p,o);
      cell.total++; cell.zMin = Math.min(cell.zMin,z); cell.zMax = Math.max(cell.zMax,z);
      if (value === null) cell.missing++;
      else {cell.values.push(value);cell.valid++;}
    }
    const cells = [...byKey.values()];
    for (const c of cells) {
      const v = c.values;
      let result = null;
      if (o.aggregation === 'coverage') result = 100*v.filter(x => x >= o.threshold).length/c.total;
      else if (!c.missing && v.length) {
        if (o.aggregation === 'slice') result = v[0];
        else if (o.aggregation === 'p10') result = v.sort((a,b)=>a-b)[Math.max(0,Math.ceil(v.length*.1)-1)];
        else {
          const reference = Math.max(...v);
          result = reference === -Infinity ? -Infinity : reference+10*Math.log10(v.reduce((s,x)=>s+10**((x-reference)/10),0)/v.length);
        }
      }
      c.zero = result === -Infinity; c.value = finite(result) ? result : null; delete c.values;
    }
    return {cells,byKey,actualHeight,options:o,sampling,count,
      finiteCells:cells.filter(c=>finite(c.value)).length,
      missingSamples:cells.reduce((s,c)=>s+c.missing,0)};
  }
  function cellAt(map,e,n) {
    const {min_m:lo,max_m:hi,spacing_m:step} = map.sampling;
    if (!finite(e) || !finite(n) || e < lo[0] || e > hi[0] || n < lo[1] || n > hi[1]) return null;
    const ix = Math.min(map.count[0]-1,Math.max(0,Math.round((e-lo[0])/step[0])));
    const iy = Math.min(map.count[1]-1,Math.max(0,Math.round((n-lo[1])/step[1])));
    return map.byKey.get(`${ix},${iy}`) || null;
  }
  const api = Object.freeze({project,cellAt,valueAt});
  if (typeof module !== 'undefined' && module.exports) module.exports=api;
  else root.RANHeatmap=api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
