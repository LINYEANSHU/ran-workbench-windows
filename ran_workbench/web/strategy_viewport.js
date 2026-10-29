/* View-only coordinates. None of these operations mutate the experiment. */
(function(root){
  'use strict';
  const MIN_ZOOM=.05,MAX_ZOOM=4;
  const clamp=value=>Math.max(MIN_ZOOM,Math.min(MAX_ZOOM,value));
  function screenToWorld(view,point){return {x:view.x+point.x/view.zoom,y:view.y+point.y/view.zoom};}
  function zoomAt(view,value,anchor){
    const world=screenToWorld(view,anchor),zoom=clamp(value);
    return {x:world.x-anchor.x/zoom,y:world.y-anchor.y/zoom,zoom};
  }
  function panBy(view,dx,dy){return {...view,x:view.x-dx/view.zoom,y:view.y-dy/view.zoom};}
  function fit(nodes,size,padding=48){
    if(!nodes.length)return {x:0,y:0,zoom:1};
    const left=Math.min(...nodes.map(n=>n.position[0])),top=Math.min(...nodes.map(n=>n.position[1]-22));
    const right=Math.max(...nodes.map(n=>n.position[0]+174)),bottom=Math.max(...nodes.map(n=>n.position[1]+106));
    const zoom=clamp(Math.min(Math.max(1,size.width-2*padding)/Math.max(1,right-left),Math.max(1,size.height-2*padding)/Math.max(1,bottom-top),1.5));
    return {x:(left+right)/2-size.width/(2*zoom),y:(top+bottom)/2-size.height/(2*zoom),zoom};
  }
  const api={MIN_ZOOM,MAX_ZOOM,screenToWorld,zoomAt,panBy,fit};
  if(typeof module==='object'&&module.exports)module.exports=api;else root.NRViewport=api;
})(globalThis);
