(function(){
  'use strict';
  const $=id=>document.getElementById(id),M=window.NRGraph,V=window.NRViewport,NS='http://www.w3.org/2000/svg';
  const I=window.RANI18n,tr=(key,...args)=>I?I.t(key,...args):String(key??'').replace(/\{(\d+)\}/g,(_,i)=>String(args[i]??`{${i}}`));
  const translatedMessage=value=>window.RANStrategyI18n?.message(value)??tr(value);
  // Update only each label's own text node. Rebuilding the inspector on a
  // language change would discard text that has not fired a change event yet.
  function refreshBinding(el){const b=el.__ranI18n;if(b){const value=b.key===null?b.args[0]():tr(b.key,...b.args.map(x=>typeof x==='function'?x():x));b.node.nodeValue=value;}for(const a of el.__ranI18nAttributes||[])el.setAttribute(a.attribute,tr(a.key,...a.args.map(x=>typeof x==='function'?x():x)));}
  function bind(el,key,...args){el.setAttribute('data-i18n-ignore','');el.setAttribute('data-ran-i18n','');el.textContent='';el.__ranI18n={key,args,node:document.createTextNode('')};el.append(el.__ranI18n.node);refreshBinding(el);return el;}
  function bindAttribute(el,attribute,key,...args){el.setAttribute('data-ran-i18n','');(el.__ranI18nAttributes||=[]).push({attribute,key,args});refreshBinding(el);return el;}
  function literal(el,value){el.setAttribute('data-i18n-ignore','');delete el.__ranI18n;el.textContent=value??'';return el;}
  function caption(id,key,...args){return bind($(id),key,...args);}
  function refreshLanguage(){document.querySelectorAll('[data-ran-i18n]').forEach(refreshBinding);I?.translatePage();}
  I?.onChange(refreshLanguage);I?.translatePage();
  const labels={round_robin:'轮询 · Round Robin',proportional_fair:'比例公平 · PF',max_cqi:'最大 CQI',cqi:'依据已到达 CSI',fixed:'固定 MCS',reported:'使用已到达的 RI / PMI',full_buffer:'满缓冲业务',constant_rate:'恒定速率',wideband:'宽带 CQI',subband:'研究子带差分 CQI',periodic:'周期报告',event:'CQI / RI / PMI 变化触发',reference:'NR 曲线参考模型',ideal:'理想无误差 · 对照'};
  // Compact graph captions fit the existing node geometry at normal type size.
  // The inspector, navigation, and accessible name retain the full description.
  const compactNodeLabels={power:'Tx power / Antenna',adaptation:'MCS / Tx plan',channel:'City channel',traffic:'Downlink traffic',measure:'CSI measurement',estimate:'Estimation error',decode:'Receive / Deliver',feedback:'Feedback / Delay'};
  let bridge,state,draft,draftRevision=0,registry={},pendingState=null,selected=null,selectedEdge=null,connection=null,dirty=false,history=[],future=[],camera={x:0,y:0,zoom:1},gesture=null,tab='summary',viewportSize=null,resizeObserver;
  function text(tag,value,cls){const el=typeof value==='function'?bind(document.createElement(tag),null,value):bind(document.createElement(tag),value);if(cls)el.className=cls;return el;}
  function svg(tag,attrs={},value){const el=document.createElementNS(NS,tag);for(const [k,v]of Object.entries(attrs))el.setAttribute(k,v);if(value!==undefined)typeof value==='function'?bind(el,null,value):bind(el,value);return el;}
  function message(value,kind='error'){$('message').hidden=!value;bind($('message'),null,()=>translatedMessage(value||''));$('message').dataset.kind=kind;}
  function options(select,values,current){select.replaceChildren(...values.map(([value,label])=>{const o=text('option',label);o.value=value;o.selected=String(value)===String(current);return o;}));}
  function notifyDraft(){request('draft',{experiment:draft,base_revision:draftRevision}).catch(error=>message(error.message));}
  function mutate(value){if(!dirty)draftRevision=state.revision;history.push(M.clone(draft));if(history.length>100)history.shift();future=[];draft=value;dirty=true;selectedEdge=null;render();notifyDraft();}
  function readLatestDraft(){
    draft=M.clone(state.draft_dirty?state.draft_experiment:state.experiment);
    draftRevision=state.draft_dirty?state.draft_base_revision:state.revision;dirty=Boolean(state.draft_dirty);
    if(!draft.graph.nodes.some(n=>n.id===selected))selected=draft.graph.nodes.find(n=>n.kind==='scheduler')?.id||draft.graph.nodes[0]?.id;
  }
  function adopt(next,force=false){
    // A get() emits state before its reply brings the module registry. Do not
    // draw ports until their definitions are available.
    if(!Object.keys(registry).length){pendingState=next;return;}
    if(state&&state.document_id===next.document_id&&next.revision<state.revision)return;
    const previous=state;
    state=next;
    const replaced=previous&&previous.document_id!==next.document_id;
    const synchronized=previous?.draft_dirty&&!next.draft_dirty;
    if(replaced){finishGesture(null,true);history=[];future=[];dirty=false;}
    if(force||replaced||synchronized){draft=M.clone(next.experiment);draftRevision=next.revision;dirty=false;}
    else if(!dirty&&gesture?.kind!=='node')readLatestDraft();
    if(!selected||!draft.graph.nodes.some(n=>n.id===selected))selected=draft.graph.nodes.find(n=>n.kind==='scheduler')?.id||draft.graph.nodes[0]?.id;
    render();
    if(replaced)requestAnimationFrame(fit);
    if(dirty&&draftRevision!==state.revision)message('外部配置已经更新，旧草稿不能覆盖它。请保留需要的内容，再选择“放弃草稿 / 读取最新”。');
  }
  function request(action,data={}){
    return new Promise((resolve,reject)=>bridge.command(JSON.stringify({action,data}),raw=>{
      try{const result=JSON.parse(raw);if(!result.ok){message(result.error);reject(Error(result.error));return;}
        if(result.registry)registry=result.registry;
        if(result.state){const next=pendingState||result.state;pendingState=null;adopt(next);}
        resolve(result);
      }catch(error){message(error.message);reject(error);}
    }));
  }
  async function synchronize(){
    if(!dirty)return;
    const response=await request('set',{experiment:draft,expected_revision:draftRevision,from_gui:true});
    dirty=false;adopt(response.state,true);message('策略已应用，下次运行使用当前流程。','success');
  }
  async function save(){if(dirty)await request('draft',{experiment:draft,base_revision:draftRevision});await request('save_dialog');}
  function guard(fn){return (...args)=>Promise.resolve().then(()=>fn(...args)).catch(error=>message(error.message));}
  function selectedNode(){return draft.graph.nodes.find(n=>n.id===selected);}
  function undo(){if(!history.length)return;if(!dirty)draftRevision=state.revision;future.push(M.clone(draft));draft=history.pop();dirty=true;render();notifyDraft();}
  function redo(){if(!future.length)return;if(!dirty)draftRevision=state.revision;history.push(M.clone(draft));draft=future.pop();dirty=true;render();notifyDraft();}
  function setSelection(id,center=false){selected=id;selectedEdge=null;renderGraph();renderInspector();renderNav();if(center){const n=selectedNode(),vp=$('flow-viewport');camera={...camera,x:n.position[0]+87-vp.clientWidth/(2*camera.zoom),y:n.position[1]+53-vp.clientHeight/(2*camera.zoom)};updateCamera();}}
  function entityNames(){return new Map([...draft.scene.bs,...draft.scene.ues].map(x=>[x.id,x.name]));}
  function visibleNodes(){const owner=$('entity').value;return draft.graph.nodes.filter(n=>owner==='all'||n.entity_id===owner||registry[n.kind]?.owner==='global');}
  function renderNav(){
    const current=$('entity').value||'all',names=entityNames();
    options($('entity'),[['all','整个网络'],...draft.scene.bs.map(x=>[x.id,`${x.id} · ${x.name}`]),...draft.scene.ues.map(x=>[x.id,`${x.id} · ${x.name}`])],current);
    const list=$('layer-list');list.replaceChildren();let last='';let group;
    for(const n of visibleNodes()){
      const spec=registry[n.kind];if(!spec)continue;
      const scope=spec.owner+'/'+n.entity_id;
      if(last!==scope){last=scope;group=text('section','', 'module-group');group.append(text('h3',spec.owner==='global'?'无线环境与观察':n.entity_id+' / '+names.get(n.entity_id)));list.append(group);}
      const b=text('button',spec.label,n.id===selected?'selected':'');b.type='button';b.addEventListener('click',()=>setSelection(n.id,true));group.append(b);
    }
  }
  function portPoint(node,port,isInput){const keys=Object.keys(registry[node.kind][isInput?'inputs':'outputs']);return [node.position[0]+(isInput?0:174),node.position[1]+57+keys.indexOf(port)*16];}
  function renderGraph(){
    const graph=$('graph');graph.replaceChildren();const nodes=visibleNodes(),ids=new Set(nodes.map(n=>n.id)),lookup=new Map(nodes.map(n=>[n.id,n]));
    updateCamera();
    const defs=svg('defs'),marker=svg('marker',{id:'arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'});marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#89a59a'}));defs.append(marker);graph.append(defs);
    draft.graph.edges.forEach((e,index)=>{
      if(!ids.has(e.source)||!ids.has(e.target))return;
      const a=lookup.get(e.source),b=lookup.get(e.target),p=portPoint(a,e.source_port,false),q=portPoint(b,e.target_port,true);
      const bend=Math.max(65,Math.abs(q[0]-p[0])*.45),wire=svg('path',{d:`M ${p[0]} ${p[1]} C ${p[0]+bend} ${p[1]}, ${q[0]-bend} ${q[1]}, ${q[0]} ${q[1]}`,class:`wire${a.kind==='feedback'?' temporal':''}${selectedEdge===index?' selected':''}`,'marker-end':'url(#arrow)','data-edge':index});
      wire.addEventListener('click',()=>{if(gesture?.moved)return;selectedEdge=index;renderGraph();$('disconnect').disabled=state.busy;});graph.append(wire);
    });
    const named=new Set();
    for(const n of nodes){
      const spec=registry[n.kind];if(!spec)continue;
      const scope=spec.owner+'/'+n.entity_id;
      if(!named.has(scope)){named.add(scope);graph.append(svg('text',{x:n.position[0],y:n.position[1]-12,class:'lane-label'},spec.owner==='global'?'无线信道 / 观察':()=>n.entity_id));}
      const g=bindAttribute(svg('g',{transform:`translate(${n.position[0]} ${n.position[1]})`,class:`node${selected===n.id?' selected':''}`,'data-node':n.id,tabindex:0,role:'button'}),'aria-label','{0} {1}',n.entity_id,()=>tr(spec.label));
      g.append(svg('rect',{width:174,height:106}),svg('text',{x:12,y:17,class:'layer'},spec.layer),svg('text',{x:12,y:35,class:'title'},()=>I?.language==='en'?(compactNodeLabels[n.kind]||tr(spec.label)):tr(spec.label)));
      const implementation=n.kind==='scheduler'?labels[n.parameters.algorithm]:n.kind==='feedback'?`${n.parameters.delay_slots} slot → ${n.parameters.serving_bs}`:n.kind==='power'?`${n.parameters.power_dbm} dBm`:n.kind==='report'?labels[n.parameters.mode]:n.kind==='traffic'?labels[n.parameters.mode]:'';
      g.append(svg('text',{x:12,y:49,class:'implementation'},implementation));
      for(const isInput of [true,false])Object.entries(spec[isInput?'inputs':'outputs']).forEach(([port,type],i)=>{
        const x=isInput?0:174,y=57+i*16,c=svg('circle',{cx:x,cy:y,r:5,class:'port'+(connection?.node===n.id&&connection?.port===port?' active':''),'data-port':port});
        c.append(bind(svg('title'),'{0} {1}: {2}',()=>tr(isInput?'输入':'输出'),port,type));
        c.addEventListener('pointerdown',e=>e.stopPropagation());
        c.addEventListener('click',e=>{e.stopPropagation();if(state.busy)return;
          if(!isInput){connection={node:n.id,port};caption('connection-status','已选 {0}.{1}；点击兼容的输入圆点。',()=>tr(spec.label),port);renderGraph();return;}
          if(!connection)return;
          try{mutate(M.connect(draft,registry,connection.node,connection.port,n.id,port));connection=null;caption('connection-status','连线已修改；运行前将校验类型、归属和时序。');}
          catch(error){message(error.message);}
        });
        g.append(c,svg('text',{x:isInput?10:164,y:y+3,'text-anchor':isInput?'start':'end',class:'port-label'},port));
      });
      g.addEventListener('keydown',e=>{if(e.key==='Enter'){setSelection(n.id);$('node-form').querySelector('input,select')?.focus();}});
      graph.append(g);
    }
    caption('graph-count','{0} 模块 / {1} 连线',nodes.length,draft.graph.edges.length);
    $('disconnect').disabled=selectedEdge===null||state.busy;
  }
  function localPoint(event){const r=$('flow-viewport').getBoundingClientRect();return {x:event.clientX-r.left,y:event.clientY-r.top};}
  function updateCamera(){
    const vp=$('flow-viewport'),width=Math.max(1,vp.clientWidth),height=Math.max(1,vp.clientHeight);
    $('graph').setAttribute('viewBox',`${camera.x} ${camera.y} ${width/camera.zoom} ${height/camera.zoom}`);
    $('zoom-label').textContent=Math.round(camera.zoom*100)+'%';
    vp.style.backgroundSize=`${20*camera.zoom}px ${20*camera.zoom}px`;
    vp.style.backgroundPosition=`${-camera.x*camera.zoom}px ${-camera.y*camera.zoom}px`;
  }
  function beginGesture(event){
    if(!draft||event.button!==0||gesture||event.target.closest('.port'))return;
    const node=event.target.closest('.node'),vp=$('flow-viewport');
    if(node){
      selected=node.dataset.node;selectedEdge=null;renderInspector();renderNav();
      if(state.busy){renderGraph();return;}
      const n=selectedNode();gesture={kind:'node',id:n.id,start:localPoint(event),origin:[...n.position],before:M.clone(draft),wasDirty:dirty,baseRevision:dirty?draftRevision:state.revision,moved:false,pointerId:event.pointerId};
    }else gesture={kind:'pan',start:localPoint(event),camera:{...camera},wireIndex:event.target.closest('.wire')?.getAttribute('data-edge'),moved:false,pointerId:event.pointerId};
    event.preventDefault();vp.dataset.gesture=gesture.kind;
    try{vp.setPointerCapture(event.pointerId);}catch(error){if(error.name!=='NotFoundError')throw error;}
  }
  function moveGesture(event){
    if(!gesture||event.pointerId!==gesture.pointerId)return;
    const p=localPoint(event),dx=p.x-gesture.start.x,dy=p.y-gesture.start.y;
    if(!gesture.moved&&Math.hypot(dx,dy)<3)return;
    gesture.moved=true;event.preventDefault();
    if(gesture.kind==='pan'){camera=V.panBy(gesture.camera,dx,dy);updateCamera();}
    else{const n=draft.graph.nodes.find(n=>n.id===gesture.id);n.position=[gesture.origin[0]+dx/camera.zoom,gesture.origin[1]+dy/camera.zoom];renderGraph();}
  }
  function finishGesture(event,cancelled=false){
    if(!gesture||(event&&event.pointerId!==gesture.pointerId))return;
    const g=gesture;gesture=null;const vp=$('flow-viewport');delete vp.dataset.gesture;
    if(vp.hasPointerCapture(g.pointerId))vp.releasePointerCapture(g.pointerId);
    if(cancelled){
      if(g.kind==='node'){
        if(g.wasDirty){draft=g.before;dirty=true;draftRevision=g.baseRevision;}
        else readLatestDraft();
      }else camera=g.camera;
      render();return;
    }
    if(g.kind==='node'){
      const n=draft.graph.nodes.find(n=>n.id===g.id),changed=g.moved&&n&&n.position.some((p,i)=>Math.abs(p-g.origin[i])>1e-9);
      if(changed){if(!dirty)draftRevision=g.baseRevision;history.push(g.before);if(history.length>100)history.shift();future=[];dirty=true;}
      else if(!g.wasDirty)readLatestDraft();
      render();if(changed)notifyDraft();
    }else if(!g.moved){selectedEdge=g.wireIndex===undefined?null:Number(g.wireIndex);renderGraph();}
  }
  function zoomTo(value,anchor){if(!draft||gesture)return;camera=V.zoomAt(camera,value,anchor);updateCamera();}
  function renderInspector(){
    const n=selectedNode(),form=$('node-form');form.replaceChildren();if(!n)return;const spec=registry[n.kind];if(!spec)return;
    caption('node-layer',spec.layer);caption('node-title',spec.label);literal($('node-owner'),n.entity_id);caption('node-standard',spec.standard);
    for(const [key,rule]of Object.entries(spec.parameters)){
      const label=text('label',''),caption=text('span',rule.label);if(rule.unit)caption.append(text('span',rule.unit,'unit'));label.append(caption);
      const input=document.createElement(rule.options||key==='serving_bs'?'select':'input');input.id='param-'+key;input.disabled=state.busy;
      if(rule.options)options(input,rule.options.map(v=>[v,labels[v]||v]),n.parameters[key]);
      else if(key==='serving_bs')options(input,draft.scene.bs.map(x=>[x.id,x.id+' · '+x.name]),n.parameters[key]);
      else{input.type=rule.type==='integer'||rule.type==='number'?'number':'text';input.value=n.parameters[key];if(rule.minimum!==undefined)input.min=rule.minimum;if(rule.maximum!==undefined)input.max=rule.maximum;if(rule.type==='number')input.step='any';}
      input.addEventListener('change',()=>{const value=['integer','number'].includes(rule.type)?Number(input.value):input.value;
        try{mutate(key==='serving_bs'?M.serving(draft,n.entity_id,value):M.parameter(draft,n.id,key,value));}catch(error){message(error.message);}
      });label.append(input);form.append(label);
    }
    if(!Object.keys(spec.parameters).length)form.append(text('p','此模块汇总实际结果，没有独立策略参数。','empty-copy'));
  }
  function table(headers,rows,onSelect){const wrap=text('div','','table-scroll'),t=text('table','','data-table'),head=document.createElement('thead'),tr=document.createElement('tr');headers.forEach(h=>tr.append(text('th',h)));head.append(tr);t.append(head);const body=document.createElement('tbody');rows.forEach((r,i)=>{const tr=document.createElement('tr');r.forEach(v=>tr.append(text('td',v)));if(onSelect)tr.addEventListener('click',()=>onSelect(i));body.append(tr);});t.append(body);wrap.append(t);return wrap;}
  const fmt=(n,d=2)=>n===null||n===undefined?'—':Number(n).toFixed(d);
  function renderResults(){
    const r=state.result;
    $('export').disabled=!r||state.busy;$('pin').disabled=!r||state.busy||dirty;$('compare').disabled=!r||!state.pinned||state.busy||dirty;
    caption('result-scope',r?`${r.slots} slot · ${fmt(r.elapsed_s)} s · ${r.backend.backend}`:state.busy?'实验进行中…':'尚未运行');
    if(r){
      const summary=$('result-summary');summary.replaceChildren();const layout=text('div','','summary-layout'),left=text('div',''),right=text('div','');
      left.append(table(['UE / BS','净吞吐 Mbps','时延 P95 ms','首次 BLER','CSI 年龄 slot','反馈 bit'],r.ues.map(u=>[`${u.ue_id} / ${u.bs_id}`,fmt(u.goodput_mbps,3),fmt(u.delay_p95_ms),fmt(u.first_bler,3),fmt(u.mean_csi_age_slots),u.feedback_bits])));
      const note=bind(text('p','','result-note'),'交付 {0} 个完整包；重传 {1} 次。曲线范围外取值：{2}。EESM 与当前场景 / 接收机尚未校准。',r.ues.reduce((n,u)=>n+u.delivered_packets,0),r.ues.reduce((n,u)=>n+u.retransmissions,0),()=>tr(r.assumptions.curve_extrapolation_used?'已使用声明的边界策略':'未使用'));left.append(note);
      right.append(text('h3','累计成功交付 / kB'));const chart=svg('svg',{id:'delivery-chart',viewBox:'0 0 400 150'}),timeline=r.timeline;
      const maximum=Math.max(1,...timeline.map(p=>p.ues.reduce((n,u)=>n+u.delivered_bytes,0))),end=Math.max(1,timeline.at(-1)?.time_ms||1);
      chart.append(svg('path',{d:'M 35 10 V 120 H 385',fill:'none',stroke:'#bbcfc4'}));
      let path='';timeline.forEach((p,i)=>{const sum=p.ues.reduce((n,u)=>n+u.delivered_bytes,0);path+=`${i?'L':'M'} ${35+p.time_ms/end*350} ${120-sum/maximum*105} `;});
      chart.append(svg('path',{d:path,fill:'none',stroke:'#116859','stroke-width':2}),svg('text',{x:5,y:16},fmt(maximum/1000,1)),svg('text',{x:35,y:140},'0'),svg('text',{x:320,y:140},fmt(end,1)+' ms'));right.append(chart);layout.append(left,right);summary.append(layout);
    }else{
      const empty=text('div','','empty-state');empty.append(text('strong',state.busy?'实验进行中':'当前配置尚无有效结果'),text('p','完成本次实验后显示包交付、时延、反馈成本与资源使用。'));$('result-summary').replaceChildren(empty);$('trace-detail').hidden=true;
    }
    renderEvents();const comparison=state.comparison;if(comparison){$('result-comparison').replaceChildren(table(['UE','A / Mbps','B / Mbps','差值 / Mbps'],comparison.ues.map(u=>[()=>u.ue_id,fmt(u.a_goodput_mbps,3),fmt(u.b_goodput_mbps,3),fmt(u.delta_goodput_mbps,3)])),bind(text('p','','result-note'),null,()=>translatedMessage(comparison.note)));}
    else $('result-comparison').replaceChildren(text('p','完成实验后保存为 A，修改策略运行 B，再进行对比。','empty-copy'));
  }
  const eventLabels={tx:'传输 / 重传',csi_arrival:'CSI 到达',feedback_drop:'反馈丢弃',ack_arrival:'ACK/NACK 到达',harq_timeout:'HARQ 超时',csi_generated:'CSI 已生成',feedback_sent:'反馈已发送'},eventReasons={stale_on_arrival:'到达时已过期',queue_expired:'排队等待过期',transport_loss:'反馈传输丢失'};
  function renderEvents(){
    const r=state.result;if(!r){$('event-table').replaceChildren();$('event-count').textContent='';return;}const type=$('event-type').value,slot=$('event-slot').value;
    const all=r.trace.filter(e=>(type==='all'||e.type===type)&&(slot===''||e.slot===Number(slot))),rows=all.slice(0,200);
    caption('event-count','{0} 条匹配，显示前 {1} 条{2}',all.length,rows.length,()=>r.trace_omitted?tr('；完整记录已按预算截断'):'');
    $('event-table').replaceChildren(table(['slot','事件','设备','CSI / MCS','TB / bit','状态'],rows.map(e=>[e.slot,eventLabels[e.type]||e.type,()=>e.ue_id||e.bs_id||'—',e.cqi!==undefined?`CQI ${e.cqi}`:e.mcs!==undefined?`MCS ${e.mcs}`:'—',e.tb_bits||e.bits||'—',()=>tr(eventReasons[e.reason]||e.reason)||(e.ack===undefined?'':e.ack?'ACK':'NACK')]),i=>{
      const event=rows[i];$('trace-detail').hidden=false;literal($('trace-json'),JSON.stringify(event,null,2));
      const kind=event.type==='csi_arrival'?'feedback':event.type==='tx'?'adaptation':event.type==='ack_arrival'?'harq':'report';
      const owner=['adaptation','harq'].includes(kind)?event.bs_id||r.ues.find(u=>u.ue_id===event.ue_id)?.bs_id:event.ue_id;
      const n=draft.graph.nodes.find(n=>n.entity_id===owner&&n.kind===kind);if(n)setSelection(n.id);
    }));
  }
  function renderSettings(){
    for(const key of ['slots','mu','n_prb','seed','channel_backend']){$('setting-'+key).value=draft.settings[key];$('setting-'+key).disabled=state.busy;}
    const extra=$('extra-settings');extra.replaceChildren();
    for(const [key,label]of [['pdsch_symbols','PDSCH 符号'],['dmrs_re','DMRS RE / PRB'],['overhead_re','其他 RE / PRB'],['pilot_symbols','参考符号'],['feedback_bits_per_slot','共享反馈 bit / slot'],['trace_stride','事件采样间隔'],['trace_limit','事件上限'],['decoder','解码模型']]){
      const l=text('label',label),input=document.createElement(key==='decoder'?'select':'input');input.disabled=state.busy;
      if(key==='decoder')options(input,[['reference',labels.reference],['ideal',labels.ideal]],draft.settings[key]);else{input.type='number';input.value=draft.settings[key];input.min=key==='overhead_re'||key==='dmrs_re'?0:1;}
      input.addEventListener('change',()=>{const e=M.clone(draft);e.settings[key]=key==='decoder'?input.value:Number(input.value);mutate(e);});l.append(input);extra.append(l);
    }
  }
  function render(){
    if(!draft||!state)return;document.body.dataset.busy=String(state.busy);
    literal($('experiment-name'),draft.name);state.path?literal($('document-path'),state.path):caption('document-path','尚未保存 · 实验包含独立场景副本');$('document-path').title=state.path;
    caption('document-status',dirty?'有未应用更改':state.dirty?'未保存':'');caption('draft-status',dirty?'待校验并应用':'已同步');
    caption('backend-badge',draft.settings.channel_backend==='rt'?'CUDA 城市信道':'合成演示 · 非城市传播');
    const p=state.progress;bind($('engine-status'),null,()=>state.busy?`${translatedMessage(p.stage||'正在运行')}${p.total?` · ${p.completed}/${p.total}`:''}`:tr(state.error?'实验失败':state.job.status==='completed'?'实验已完成':state.job.status==='cancelled'?'已取消':'本机工作台就绪'));
    $('run').disabled=state.busy;$('cancel').disabled=!state.busy;$('apply').disabled=!dirty||state.busy;
    for(const id of ['new','open','save','validate','entity'])$(id).disabled=state.busy;
    $('undo').disabled=!history.length||state.busy;$('redo').disabled=!future.length||state.busy;
    $('reload').disabled=!dirty||state.busy;
    renderSettings();renderNav();renderGraph();renderInspector();renderResults();if(state.error)message(state.error);
  }
  function setTab(next){tab=next;for(const t of ['summary','events','comparison']){$('tab-'+t).setAttribute('aria-selected',String(t===tab));$('result-'+t).hidden=t!==tab;}}
  function fit(){if(!draft||gesture)return;const vp=$('flow-viewport');camera=V.fit(visibleNodes(),{width:vp.clientWidth,height:vp.clientHeight});updateCamera();}
  function ready(){
    $('save').addEventListener('click',guard(save));$('apply').addEventListener('click',guard(synchronize));
    $('run').addEventListener('click',guard(async()=>{await synchronize();message('');await request('run');}));
    $('cancel').addEventListener('click',guard(()=>request('cancel')));
    $('validate').addEventListener('click',guard(async()=>{await request('validate',{experiment:draft});message('流程校验通过：端口、设备归属、参数与时序一致。','success');}));
    $('export').addEventListener('click',guard(()=>request('export_dialog')));
    for(const id of ['new','open'])$(id).addEventListener('click',guard(async()=>{if(dirty&&!window.confirm(tr('放弃尚未应用的流程修改？')))return;await request(id+'_dialog');}));
    $('undo').addEventListener('click',undo);$('redo').addEventListener('click',redo);
    $('reload').addEventListener('click',guard(async()=>{if(dirty&&!window.confirm(tr('放弃当前草稿并读取最新配置？')))return;const r=await request('discard_draft');dirty=false;history=[];future=[];adopt(r.state,true);message('已读取最新配置。','success');}));
    $('entity').addEventListener('change',()=>{renderNav();renderGraph();fit();});
    for(const id of ['zoom-in','zoom-out'])$(id).addEventListener('click',()=>{const vp=$('flow-viewport');zoomTo(camera.zoom*(id==='zoom-in'?1.2:1/1.2),{x:vp.clientWidth/2,y:vp.clientHeight/2});});$('fit').addEventListener('click',fit);
    $('disconnect').addEventListener('click',()=>{if(selectedEdge!==null)mutate(M.disconnect(draft,selectedEdge));});
    $('settings-detail').addEventListener('click',()=>{$('extra-settings').hidden=!$('extra-settings').hidden;$('settings-detail').setAttribute('aria-expanded',String(!$('extra-settings').hidden));});
    for(const key of ['slots','mu','n_prb','seed','channel_backend'])$('setting-'+key).addEventListener('change',()=>{const e=M.clone(draft);e.settings[key]=key==='channel_backend'?$('setting-'+key).value:Number($('setting-'+key).value);mutate(e);});
    $('pin').addEventListener('click',guard(async()=>{await request('pin');message('当前结果已保存为基准 A。','success');}));
    $('compare').addEventListener('click',guard(async()=>{await request('compare');setTab('comparison');}));
    for(const t of ['summary','events','comparison'])$('tab-'+t).addEventListener('click',()=>setTab(t));
    $('event-type').addEventListener('change',renderEvents);$('event-slot').addEventListener('input',renderEvents);
    const viewport=$('flow-viewport');
    viewport.addEventListener('pointerdown',beginGesture);viewport.addEventListener('pointermove',moveGesture);
    viewport.addEventListener('pointerup',event=>finishGesture(event));viewport.addEventListener('pointercancel',event=>finishGesture(event,true));
    viewport.addEventListener('lostpointercapture',event=>finishGesture(event,true));
    viewport.addEventListener('wheel',event=>{event.preventDefault();const unit=event.deltaMode===1?16:event.deltaMode===2?viewport.clientHeight:1;zoomTo(camera.zoom*Math.exp(-event.deltaY*unit*.0015),localPoint(event));},{passive:false});
    resizeObserver=new ResizeObserver(()=>{
      const size={width:viewport.clientWidth,height:viewport.clientHeight};
      if(viewportSize){camera.x+=(viewportSize.width-size.width)/(2*camera.zoom);camera.y+=(viewportSize.height-size.height)/(2*camera.zoom);}
      viewportSize=size;updateCamera();
    });resizeObserver.observe(viewport);
    document.addEventListener('keydown',e=>{const typing=['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName);if(e.key==='Escape'){connection=null;renderGraph();}if(typing||state.busy)return;if((e.ctrlKey||e.metaKey)&&e.key==='z'){e.preventDefault();e.shiftKey?redo():undo();}if(e.key==='Delete'&&selectedEdge!==null){e.preventDefault();mutate(M.disconnect(draft,selectedEdge));}});
    $('node-form').addEventListener('submit',e=>{e.preventDefault();guard(synchronize)();});
    bridge.event.connect(raw=>{try{const e=JSON.parse(raw);if(e.type==='state')adopt(e.state);}catch(error){message(error.message);}});
    request('get').then(()=>{document.body.dataset.ready='true';fit();}).catch(error=>message(error.message));
    window.nrStrategy={save,request,getDraft:()=>M.clone(draft),getState:()=>M.clone(state),getView:()=>({...camera}),select:setSelection,apply:synchronize};
  }
  if(window.qt&&window.QWebChannel)new QWebChannel(qt.webChannelTransport,channel=>{bridge=channel.objects.strategy;ready();});
  else message('此工作区需要从 RAN Workbench 桌面程序打开。');
})();
