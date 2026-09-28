function renderDiag(d){
  var m=d.metrics||{};
  txt('t-req',m.requests_total||0);txt('t-err',m.errors_total||0);
  txt('t-stream',m.stream_requests_total||0);
  txt('t-tools',(d.tools||[]).length);
  var svcs=d.services||{};
  var order=['router','general','coding','reasoning','vision','speech','image_gen','memory'];
  set('healthtbl','<table class="tbl"><tr><th>EXPERT</th><th>MODEL</th><th>STATE</th><th>LAT</th></tr>'+Object.keys(svcs).map(function(n){
    var s=svcs[n]||{},st=stateOf(s);return '<tr><td>'+esc(n)+'</td><td>'+esc(s.model||s.backend||'')+'</td><td>'+pill(st.txt)+'</td><td>'+(s.latency_ms!=null?s.latency_ms+' ms':'—')+'</td></tr>'
  }).join('')+'</table>');
  var ok=order.filter(function(n){return (svcs[n]||{}).status=='ok'}).length,total=order.length||1;
  var health=Math.round(ok/total*100),hex=document.getElementById('healthscore');
  if(hex){hex.textContent=health;hex.style.background=health===100?'linear-gradient(135deg,#0fa968,#39d98a)':health>60?'linear-gradient(135deg,var(--violet),var(--mag))':'linear-gradient(135deg,#c0392b,#ff5c7a)'}
  var g=d.gateway||{};
  set('hubstats','<div class="stat"><b>'+fmtUp(g.uptime_s)+'</b><span>UPTIME</span></div><div class="stat"><b>'+(g.req_per_min||0)+'</b><span>REQ/MIN</span></div><div class="stat"><b>'+(g.latency_p50_ms!=null?g.latency_p50_ms+' ms':'—')+'</b><span>P50</span></div><div class="stat"><b>'+(g.success_rate==null?'—':g.success_rate+'%')+'</b><span>SUCC</span></div><div class="stat"><b>'+(g.stream_requests_total||0)+'</b><span>STREAMS</span></div><div class="stat"><b>'+(g.errors_total||0)+'</b><span>ERRORS</span></div>');
  set('sysbars','<div class="rowline"><span class="k">CPU</span><b>'+Math.round((d.system||{}).cpu_pct||0)+'%</b> <span class="dimtxt">'+(d.system||{}).cores||'? cores</span></div><div class="rowline"><span class="k">MEM</span><b>'+((d.system||{}).mem_used_gb||0).toFixed(1)+' / '+((d.system||{}).mem_total_gb||0).toFixed(1)+' GB</span></div>');
  txt('p95',(g.latency_p95_ms!=null?g.latency_p95_ms+' ms':'—'));
  txt('p50',(g.latency_p50_ms!=null?g.latency_p50_ms+' ms':'—'));
  txt('reqmin',g.req_per_min||0);
  txt('qdepth',g.inflight!=null?g.inflight:0);
  txt('latavg',(g.latency_avg_ms!=null?g.latency_avg_ms+' ms':'—'));
  txt('succ',g.success_rate==null?'—':g.success_rate+'%');
  txt('streams',g.stream_requests_total||0);
  txt('uptime',fmtUp(g.uptime_s));
  txt('stackpct',health+'%');
  var be=d.backend||{};var inst=be.installed||[],res=be.resident||[];
  set('backend','<div class="rowline"><span class="k">url</span><span class="mono" style="color:var(--muted)">'+esc(be.url||'—')+'</span></div><div class="rowline"><span class="k">reachable</span>'+pill(be.reachable?'ok':'off')+'</div><div class="rowline"><span class="k">installed</span><b>'+inst.length+'</b></div>'+(be.reachable?'':'<div class="rowline"><span class="k">tip</span><span class="dimtxt">pull models to enable real inference</span></div>'));
  var rm=document.getElementById('residentmeter');
  if(rm){rm.hidden=!be.reachable;var rn=document.getElementById('residentnum');if(rn)rn.textContent=res.length+'/'+Math.max(1,inst.length);var rf=document.getElementById('residentfill');if(rf)rf.style.width=inst.length?Math.round(res.length/inst.length*100)+'%':0}
  set('railbackend','<div class="rowline"><span class="k">backend</span>'+pill(be.reachable?'ok':'off')+'</div><div class="rowline"><span class="k">installed</span><span class="mono">'+inst.length+'</span></div><div class="rowline"><span class="k">resident</span><span class="mono">'+res.length+'</span></div>');
  set('reslist',order.map(function(n){var s=svcs[n]||{};return '<div class="rowline"><span class="k">'+esc(n)+'</span><span>'+pill(s.status||'down')+' <span class="mono" style="color:var(--muted)">'+(s.model||'')+'</span> <span style="color:var(--muted);font-size:11px">'+(s.latency_ms!=null?s.latency_ms+'ms':'')+'</span></span></div>'}).join('')+'<div class="rowline"><span class="k">healthy</span><b>'+ok+'/'+total+'</b></div>');
  var mem=d.memory||{};
  txt('mstats',(mem.documents||0)+' documents in FAISS · backend '+(mem.backend||'faiss'));
  set('chipdocs','<span class="chip on">'+(mem.documents||0)+' DOCS</span>');
  set('chipev','<span class="chip">'+(d.events||[]).length+' EVENTS</span>');
  set('cogrow',order.map(function(n){var s=svcs[n]||{};return '<div class="cell"><b>'+(s.status=='ok'?'●':'○')+'</b><span>'+esc(n.toUpperCase())+'</span><span style="display:block;font-size:9px;color:var(--muted)">'+esc(s.model||'')+'</span></div>'}).join(''));
}
