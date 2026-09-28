
function renderStack(d){
  const cat=d.catalog||[],svcs=d.services||{};
  const tb=$('#catalog tbody');tb.innerHTML=cat.map(c=>{const st=stateOf(svcs[c.expert]);return '<tr><td>'+esc(c.layer)+'</td><td>'+esc(c.model)+'</td><td>'+esc(c.purpose)+'</td><td>'+pill(st.txt)+'</td></tr>'}).join('');
  $('#pipeline').innerHTML=cat.map((c,i)=>'<div class="layer"><h4><em>'+String(i+1).padStart(2,'0')+'</em>'+esc(c.layer)+' · '+esc(c.expert)+'</h4><p>'+esc(c.purpose)+' — model <b style="color:#c9e8ff">'+esc(c.model)+'</b> '+pill(stateOf(svcs[c.expert]).txt)+'</p></div>').join('');
  const okN=cat.filter(c=>svcs[c.expert]&&svcs[c.expert].status==='ok').length;
  $('#rail').innerHTML=cat.map(c=>'<div class="rowline"><span class="k">'+esc(c.expert)+'</span>'+pill(stateOf(svcs[c.expert]).txt)+'</div>').join('')+'<div class="rowline"><span class="k">healthy</span><b>'+okN+'/'+cat.length+'</b></div>';
  $('#livedot').className='dot'+(okN===cat.length?'':' off');$('#livetxt').textContent=okN===cat.length?'ALL SYSTEMS LIVE':okN+'/'+cat.length+' UP';
  $('#concepts').innerHTML=cat.map(c=>'<div class="layer"><h4><em>◆</em>'+esc(c.layer)+'</h4><p>'+esc(c.purpose)+'</p></div>').join('');
}
function renderDiag(d){
  const m=d.metrics||{};$('#t-req').textContent=m.requests_total??'0';$('#t-err').textContent=m.errors_total??'0';$('#t-stream').textContent=m.stream_requests_total??'0';
  const tools=d.tools||[];$('#t-tools').textContent=tools.length;
  const svcs=d.services||{};
  $('#healthtbl tbody').innerHTML=Object.keys(svcs).map(n=>{const s=svcs[n],st=stateOf(s);return '<tr><td>'+esc(n)+'</td><td>'+esc((s&&s.model)||'—')+'</td><td>'+pill(st.txt)+(s&&s.latency_ms!=null?' <span class="mono" style="color:var(--muted)">'+s.latency_ms+'ms</span>':'')+'</td></tr>'}).join('');
  const b=d.backend||{};let bh='<div class="rowline"><span class="k">url</span><span class="mono">'+esc(b.url||'—')+'</span></div><div class="rowline"><span class="k">reachable</span>'+pill(b.reachable?'ok':'off')+'</div>';
  const inst=b.installed||[];bh+='<div class="rowline"><span class="k">installed</span><b>'+(b.reachable?inst.length:'—')+'</b></div>';
  (b.resident||[]).forEach(r=>{bh+='<div class="rowline"><span class="k">resident</span><span class="mono">'+esc(r.model||'?')+' · '+esc(r.context||'?')+' ctx</span></div>'});
  $('#backend').innerHTML=bh;
  const rm=$('#residentmeter');if(b.reachable){rm.hidden=false;const n=(b.resident||[]).length;$('#residentnum').textContent=n+'/1';$('#residentfill').style.width=Math.min(100,n*50)+'%'}
  const mem=d.memory||{},memOk=mem.status==='ok';
  $('#railmemory').innerHTML='<div class="rowline"><span class="k">faiss</span>'+pill(memOk?'ok':'off')+'</div><div class="rowline"><span class="k">documents</span><b>'+(memOk?(mem.documents??'0'):'—')+'</b></div>';
  $('#railbackend').innerHTML='<div class="rowline"><span class="k">backend</span>'+pill(b.reachable?'ok':'off')+'</div><div class="rowline"><span class="k">models</span><b>'+(b.reachable?inst.length:'—')+'</b></div>';
}
function renderActivity(d){
  const det=e=>Object.entries(e).filter(([k])=>!['type','at','ts','time'].includes(k)).map(([k,v])=>k+'='+v).join(' ');
  $('#events').innerHTML=(d.alerts||[]).map(a=>'<div class="evt '+(a.level==='error'?'error':a.level==='warn'?'warn':'ok')+'"><span class="t">[ALERT]</span><span class="x">'+esc(a.text)+'</span></div>').join('')+(d.events||[]).slice().reverse().map(e=>'<div class="evt ok"><span class="t">'+esc(e.at||e.ts||e.time||'')+'</span><span class="x">'+esc(e.type||'event')+(det(e)?' — '+esc(det(e)):'')+'</span></div>').join('')||'<p>No events yet.</p>';
}
async function stack(){try{renderStack(await api('/api/stack'))}catch(e){note('stack poll failed: '+e.message)}}
async function diag(){try{renderDiag(await api('/api/diagnostics'))}catch(e){note('diagnostics poll failed: '+e.message)}}
async function act(){try{renderActivity(await api('/api/activity'))}catch(e){note('activity poll failed: '+e.message)}}
