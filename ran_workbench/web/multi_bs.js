/* All-station view contracts; never sum independent complex BS waveforms. */
(function(root){
  'use strict';
  const colors=['#9fc7b8','#d3b47a','#89b4d1','#d495ae','#aab98a'];
  function linksFor(ue,selection){
    const links=ue?.links || [];
    return selection==null || selection==='' ? links : links.filter(link=>link.bs_id===selection);
  }
  function pathEntries(links){
    return links.flatMap(link=>(link.paths || []).slice(0,50).map((path,path_index)=>({...path,bs_id:link.bs_id,path_index})));
  }
  function initialMetric(count,current,explicit){return !explicit && count>1 ? 'sinr_db' : current;}
  function cfrSeries(links){
    return links.map((link,index)=>({bs_id:link.bs_id,offsets:link.cfr?.offsets_hz || [],
      gains:link.cfr?.gain_db || [],color:colors[index%colors.length]}));
  }
  const api=Object.freeze({linksFor,pathEntries,initialMetric,cfrSeries,colors});
  if(typeof module!=='undefined' && module.exports)module.exports=api;
  else root.RANMultiBS=api;
})(typeof globalThis!=='undefined'?globalThis:this);
