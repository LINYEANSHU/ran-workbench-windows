const test=require('node:test');
const assert=require('node:assert/strict');
const V=require('../ran_workbench/web/strategy_viewport.js');
function close(a,b){assert.ok(Math.abs(a-b)<1e-8,`${a} != ${b}`);}

test('wheel zoom preserves the world point under the mouse',()=>{
  const start={x:-30,y:80,zoom:.7},anchor={x:537,y:213};
  const before=V.screenToWorld(start,anchor),after=V.zoomAt(start,1.5,anchor);
  const world=V.screenToWorld(after,anchor);close(before.x,world.x);close(before.y,world.y);
  assert.deepEqual(start,{x:-30,y:80,zoom:.7});
});
test('canvas drag follows the pointer in screen pixels at every zoom',()=>{
  for(const zoom of [.2,1,2.5]){
    const start={x:100,y:50,zoom},next=V.panBy(start,120,-60);
    close((start.x-next.x)*zoom,120);close((start.y-next.y)*zoom,-60);
  }
});
test('fit centers all nodes in both dimensions including negative coordinates',()=>{
  const nodes=[{position:[-300,-200]},{position:[500,900]}],size={width:1200,height:800};
  const view=V.fit(nodes,size,40),top=V.screenToWorld(view,{x:0,y:0});
  assert.ok(top.x<=-300&&top.y<=-222);
  const bottom=V.screenToWorld(view,{x:size.width,y:size.height});
  assert.ok(bottom.x>=674&&bottom.y>=1006);
  close(view.x+size.width/(2*view.zoom),187);
  close(view.y+size.height/(2*view.zoom),392);
});
test('zoom remains bounded without moving its anchor when the bound is reached',()=>{
  const p={x:40,y:120},a={x:10,y:20,zoom:1};
  for(const value of [1e-6,1e6]){
    const b=V.zoomAt(a,value,p),before=V.screenToWorld(a,p),after=V.screenToWorld(b,p);
    assert.ok(b.zoom>=V.MIN_ZOOM&&b.zoom<=V.MAX_ZOOM);close(before.x,after.x);close(before.y,after.y);
  }
});
