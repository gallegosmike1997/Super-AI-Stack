function renderActivity(d){
  var ev=d.events||[],al=d.alerts||[];
  var af=al.map(function(a){return '<div class="rowline"><span class="k alert '+a.level+'"></span><span>'+esc(a.text||a.message||'')+'</span></div>'}).join('');
  var ef=ev.slice().reverse().map(function(e){
    var det=Object.entries(e).filter(function(kv){return !['type','at','ts','time'].includes(kv[0])}).map(function(kv){return kv[0]+'='+kv[1]}).join(' ');
    return '<div class="evt ok"><span class="t">'+esc(e.at||e.ts||e.time||'')+'</span><span class="x">'+esc(e.type||'event')+(det?' — '+esc(det):'')+'</span></div>'
  }).join('');
  set('events',(af+ef)||'<p class="dimtxt">No events yet — route a request from the Dashboard.</p>');
  set('alertfeed',al.length?al.map(function(a){return '<div class="rowline"><span class="k alert '+(a.level||'')+'"></span><span>'+esc(a.text||a.message||'')+'</span></div>'}).join(''):'<p class="dimtxt">All clear — no active alerts.</p>');
}
async function stack(){try{renderStack(await api('/api/stack'))}catch(e){note('stack poll failed: '+e.message)}}
async function diag(){try{renderDiag(await api('/api/diagnostics'))}catch(e){note('diag poll failed: '+e.message)}}
async function act(){try{renderActivity(await api('/api/activity'))}catch(e){note('activity poll failed: '+e.message)}}
