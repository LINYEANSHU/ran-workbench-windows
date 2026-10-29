(function(root){
  'use strict';
  const clone=v=>JSON.parse(JSON.stringify(v));
  function parameter(exp,id,key,value){const e=clone(exp),n=e.graph.nodes.find(n=>n.id===id);if(!n)throw Error('节点不存在');n.parameters[key]=value;return e;}
  function connect(exp,registry,source,sourcePort,target,targetPort){
    const e=clone(exp),a=e.graph.nodes.find(n=>n.id===source),b=e.graph.nodes.find(n=>n.id===target);
    if(!a||!b||a===b)throw Error('连线需要两个不同的节点');
    const output=registry[a.kind].outputs[sourcePort],input=registry[b.kind].inputs[targetPort];
    if(!output||!input||output!==input.replace(/\*$/,''))throw Error('端口类型不兼容');
    if(!input.endsWith('*'))e.graph.edges=e.graph.edges.filter(x=>x.target!==target||x.target_port!==targetPort);
    if(!e.graph.edges.some(x=>x.source===source&&x.source_port===sourcePort&&x.target===target&&x.target_port===targetPort))e.graph.edges.push({source,source_port:sourcePort,target,target_port:targetPort});
    return e;
  }
  function disconnect(exp,index){const e=clone(exp);e.graph.edges.splice(index,1);return e;}
  function serving(exp,uid,bid){
    const e=clone(exp),nodes=e.graph.nodes;
    const f=nodes.find(n=>n.entity_id===uid&&n.kind==='feedback');if(!f)throw Error('反馈节点不存在');f.parameters.serving_bs=bid;
    const targets={traffic:'queue',report:'csi_store',ack:'harq'};
    for(const edge of e.graph.edges){
      const source=nodes.find(n=>n.id===edge.source);
      if(source?.entity_id===uid&&targets[edge.target_port]&&(source.kind==='feedback'||source.kind==='traffic')){
        const n=nodes.find(n=>n.entity_id===bid&&n.kind===targets[edge.target_port]);if(!n)throw Error('目标 BS 功能不完整');edge.target=n.id;
      }
    }
    return e;
  }
  const api={clone,parameter,connect,disconnect,serving};
  if(typeof module==='object'&&module.exports)module.exports=api;else root.NRGraph=api;
})(globalThis);
