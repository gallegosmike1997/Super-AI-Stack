
/* Canvas panel renderer — fills every panel from /api/diagnostics (live only). */
(function(){
// Stub elements the legacy render loop still writes to, so it can never throw.
['livedot','livetxt','railmemory'].forEach(function(id){
  if(!document.getElementById(id)){var s=document.createElement('span');s.id=id;s.hidden=true;document.body.appendChild(s);}
});
// Take over the polling loop from the legacy script.
for(var i=1;i<9999;i++)clearInterval(i);

var $=function(s){return document.querySelector(s)};
var esc=function(t){return String(t).replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})};
function set(id,html){var el=document.getElementById(id);if(el)el.innerHTML=html}
function txt(id,v){var el=document.getElementById(id);if(el)el.textContent=v}
function fmtUp(s){var h=Math.floor(s/3600),m=Math.floor((s%3600)/60);return h?h+'h '+m+'m':m+'m '+(s%60)+'s'}

function meterRow(lab,pct,sub){
  return '<div class="meter"><div class="lab"><span>'+lab+'</span><span>'+sub+'</span></div><div class="track"><div class="fill" style="width:'+Math.max(0,Math.min(100,pct||0))+'%"></div></div></div>';
}

function render(d){
  var svcs=d.services||{},names=Object.keys(svcs);
  var ok=names.filter(function(n){return svcs[n].status==='ok'}).length;
  var total=names.length||1;
  var health=Math.round(ok/total*100);

  // Dashboard: health score hex + hub stats
  var hex=document.getElementById('healthscore');
  if(hex){hex.textContent=health;hex.style.background=health===100?'linear-gradient(135deg,#0fa968,#39d98a)':health>60?'linear-gradient(135deg,var(--violet),var(--mag))':'linear-gradient(135deg,#c0392b,#ff5c7a)'}
  set('hubstats',
    '<div class="stat"><b>'+fmtUp(d.gateway.uptime_s)+'</b><span>UPTIME</span></div>'+
    '<div class="stat"><b>'+(d.gateway.req_per_min??0)+'</b><span>REQ / MIN</span></div>'+
    '<div class="stat"><b>'+(d.gateway.latency_p50_ms??'—')+' ms</b><span>P50 LATENCY</span></div>'+
    '<div class="stat"><b>'+(d.gateway.success_rate==null?'—':d.gateway.success_rate+'%')+'</b><span>SUCCESS</span></div>'+
    '<div class="stat"><b>'+(d.gateway.stream_requests_total||0)+'</b><span>STREAMS</span></div>'+
    '<div class="stat"><b>'+(d.gateway.errors_total||0)+'</b><span>ERRORS</span></div>');

  // Expert roster with live latency
  set('reslist',names.map(function(n){
    var s=svcs[n];
    var lat=(s.latency_ms!=null)?s.latency_ms+' ms':'—';
    return '<div class="rowline"><span class="k">'+esc(n)+'</span><span><span class="chip'+(s.status==='ok'?' on':'')+'">'+esc(s.status)+'</span> <span style="font-size:12px">'+esc(s.model||s.backend||'')+'</span> <span style="color:var(--muted);font-size:11px">'+lat+'</span></span></div>';
  }).join(''));

  // System meters
  var sys=d.system||{};
  set('sysbars',
    meterRow('CPU',sys.cpu_pct,(sys.cores||'?')+' cores')+
    meterRow('MEMORY',sys.mem_total_gb?sys.mem_used_gb/sys.mem_total_gb*100:0,(sys.mem_used_gb||0).toFixed(1)+' / '+(sys.mem_total_gb||0).toFixed(1)+' GB'));

  // Inference backend card
  var be=d.backend||{};
  set('backend','<div class="rowline"><span class="k">Backend</span><span style="font-size:12px">'+esc(be.url||'—')+'</span></div>'+
    '<div style="margin-top:6px"><span class="chip'+(be.reachable?' on':'')+'">'+(be.reachable?'REACHABLE':'UNREACHABLE')+'</span>'+
    '<span class="chip">'+((be.installed||[]).length)+' installed</span>'+
    '<span class="chip'+((be.resident||[]).length?' on':'')+'">'+((be.resident||[]).length)+' resident</span></div>');
  var inst=(be.installed||[]).length||1;
  var rf=document.getElementById('residentfill');
  if(rf)rf.style.width=((be.resident||[]).length/inst*100)+'%';
  txt('residentnum',(be.resident||[]).length+' / '+inst);

  // Stack coverage + cognitive row
  txt('stackpct',health+'%');
  var order=['router','general','coding','reasoning','vision','speech','image_gen','memory'];
  set('cogrow',order.map(function(n){
    var s=svcs[n]||{status:'offline'};
    return '<div class="cell"><b>'+(s.status==='ok'?'●':'○')+'</b><span>'+esc(n.toUpperCase())+'</span></div>';
  }).join(''));

  // Totals strip
  txt('t-req',d.gateway.requests_total||0);
  txt('t-stream',d.gateway.stream_requests_total||0);
  txt('t-err',d.gateway.errors_total||0);
  txt('t-tools',(d.tools||[]).length);
  txt('qdepth',d.gateway.inflight??0);
  txt('p95',(d.gateway.latency_p95_ms??'—')+' ms');
  txt('streams',d.gateway.stream_requests_total||0);
  txt('uptime',fmtUp(d.gateway.uptime_s));
  txt('reqmin',(d.gateway.req_per_min??0));
  txt('p50',(d.gateway.latency_p50_ms??'—')+' ms');
  txt('latavg',(d.gateway.latency_avg_ms??'—')+' ms');
  txt('succ',d.gateway.success_rate==null?'—':d.gateway.success_rate+'%');

  // Model table + health table + core tiers + concept layers (from stack catalog)
  fetch('/api/stack').then(function(r){return r.json()}).then(function(st){
    var cat=st.catalog||[];
    set('catalog','<table class="tbl"><tr><th>LAYER</th><th>MODEL</th><th>PURPOSE</th></tr>'+
      cat.map(function(c){var live=svcs[c.expert];
        return '<tr><td>'+esc(c.layer)+'</td><td><span class="chip'+(live&&live.status==='ok'?' on':'')+'">'+esc(c.model)+'</span></td><td style="color:var(--muted)">'+esc(c.purpose)+'</td></tr>'}).join('')+'</table>');
    set('healthtbl','<table class="tbl"><tr><th>EXPERT</th><th>STATUS</th><th>LATENCY</th></tr>'+
      order.map(function(n){var s=svcs[n]||{status:'offline'};
        return '<tr><td>'+esc(n)+'</td><td><span class="chip'+(s.status==='ok'?' on':'')+'">'+esc(s.status)+'</span></td><td>'+(s.latency_ms!=null?s.latency_ms+' ms':'—')+'</td></tr>'}).join('')+'</table>');
    set('corelist',cat.map(function(c){var live=svcs[c.expert];
      return '<div class="tier"><em>0'+(cat.indexOf(c)+1)+'</em><span>'+esc(c.layer).toUpperCase()+'</span><small>'+esc(c.model)+(live&&live.status==='ok'?' · live':' · idle')+'</small></div>'}).join(''));
    set('layerstatus',cat.map(function(c){var live=svcs[c.expert];
      return '<span class="chip'+(live&&live.status==='ok'?' on':'')+'">'+esc(c.layer).toUpperCase()+'</span>'}).join(''));
    set('concepts',cat.map(function(c){var live=svcs[c.expert];
      return '<div class="tier"><em>'+esc(c.layer).slice(0,2).toUpperCase()+'</em><span>'+esc(c.layer).toUpperCase()+'</span><small>'+esc(c.purpose)+' · '+esc(c.model)+'</small></div>'}).join(''));
  }).catch(function(){});

  // Event / alert feeds
  var ev=d.events||[];
  var feed=ev.length?ev.map(function(e){
    return '<div class="rowline"><span class="k">'+esc(e.ts||'')+'</span><span style="font-size:12px">'+esc(e.kind||e.type||'event')+' — '+esc(e.detail||e.message||'')+'</span></div>';
  }).join(''):'<p class="dimtxt">No events recorded yet — route a request from the Dashboard chat.</p>';
  set('events',feed);set('alertfeed',feed);

  // Memory stats
  var mem=d.memory||{};
  txt('mstats',(mem.documents||0)+' documents in FAISS · backend '+esc(mem.backend||'faiss'));
  set('chipdocs','<span class="chip on">'+(mem.documents||0)+' DOCS</span>');
  set('chipev','<span class="chip">'+ev.length+' EVENTS</span>');
}

function tick(){fetch('/api/diagnostics').then(function(r){return r.json()}).then(render).catch(function(){})}
tick();setInterval(tick,5000);

// Memory search panel (live FAISS query)
var mb=document.getElementById('memsearch');
if(mb)mb.addEventListener('click',function(){
  var q=document.getElementById('memq'),out=document.getElementById('memhits');
  if(!q||!out||!q.value.trim())return;
  out.innerHTML='<p class="dimtxt">Searching FAISS…</p>';
  fetch('/api/memory/search',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({query:q.value.trim(),k:4})})
    .then(function(r){return r.json()})
    .then(function(j){
      var hits=j.results||j.hits||[];
      out.innerHTML=hits.length?hits.map(function(h){
        return '<div class="rowline"><span style="font-size:12px">'+esc(typeof h==='string'?h:(h.text||h.document||''))+'</span></div>';
      }).join(''):'<p class="dimtxt">No matches — store knowledge on the Memory tab first.</p>';
    }).catch(function(){out.innerHTML='<p class="dimtxt">Memory service unreachable.</p>'});
});
})();

