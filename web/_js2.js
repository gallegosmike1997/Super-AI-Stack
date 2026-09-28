
let streaming=false,ctl=null;
function setBusy(b,m){streaming=b;document.querySelector('#chat button').disabled=b;document.querySelector('#chat button').textContent=b?(m||'Streaming…'):'SEND'}
$('#chat').addEventListener('submit',async e=>{
  e.preventDefault();
  if(streaming)return;
  const inp=$('#msg'),file=$('#file').files[0],text=inp.value.trim();
  if(!text&&!file)return;
  inp.value='';
  say(text||file.name,'u');
  const a=say('routing…','a');
  const at=await read(file);
  ctl=new AbortController();
  setBusy(true,'Routing…');
  try{
    const body={message:text,session_id:session,attachments:at?[at]:[],metadata:{use_memory:$('#mem').checked}};
    if(at){
      const res=await fetch('/chat',{method:'POST',headers:hdr(),body:JSON.stringify(body),signal:ctl.signal});
      if(!res.ok)throw new Error('gateway '+res.status);
      const j=await res.json();
      paint(a,j.response,false);
      badge(a,(j.expert||'?')+' · '+(j.model||'?'),false);
      route.innerHTML='<span class="pill">'+esc(j.task_type||'chat')+'</span> '+esc(j.expert||'?')+' · '+esc(j.model||'?');
      note('route='+j.task_type+' expert='+j.expert+' model='+j.model);
      (j.artifacts||[]).forEach(x=>{if(x.type==='image'&&x.data){const im=document.createElement('img');im.src='data:'+(x.mime_type||'image/png')+';base64,'+x.data;im.style.width='100%';im.style.marginTop='8px';a.append(im);const g=$('#gen');g.innerHTML='';g.append(im.cloneNode(true))}});
    }else{
      const res=await fetch('/chat/stream',{method:'POST',headers:hdr(),body:JSON.stringify(body),signal:ctl.signal});
      if(!res.ok)throw new Error('gateway '+res.status);
      let answer='';
      const stop=ev=>{if(ev.key==='Escape')ctl.abort()};
      document.addEventListener('keydown',stop);
      route.textContent='streaming… (Esc stops)';
      try{
        await sse(res,(ev,data)=>{
          if(data==='[DONE]')return;
          let j=null;try{j=JSON.parse(data)}catch(_){}
          if(!j)return;
          if(ev==='meta'){badge(a,j.expert+' · '+j.model,true);route.innerHTML='<span class="pill">'+esc(j.task_type)+'</span> '+esc(j.expert)+' · '+esc(j.model)+' <span style="color:var(--muted)">('+esc(j.source)+')</span>';note('route='+j.task_type+' expert='+j.expert+' model='+j.model+' source='+j.source);return}
          if(ev==='error'){note('stream error: '+j.detail);route.innerHTML='<span class="pill off">fallback</span> '+esc(j.detail);return}
          if(ev==='done'){badge(a,j.expert+' · '+j.model,false);note('stream done ('+j.chars+' chars)');return}
          if(ev==='delta'){answer+=j.text;paint(a,answer,true);a.scrollIntoView({block:'nearest'})}
        });
      }catch(ab){note('stream stopped by user')}
      document.removeEventListener('keydown',stop);
      paint(a,answer||'No content returned.',false);
      route.innerHTML+=' <span style="color:var(--muted)">· '+answer.length+' chars</span>';
    }
    const s=await fetch('/sessions/'+session,{headers:key()?{'x-api-key':key()}:{}}).then(r=>r.json()).catch(()=>null);
    if(s){const t=s.messages.slice(-8).join('\n');$('#sess').textContent=t;$('#sess2').textContent=t}
  }catch(err){
    a.className='msg err';
    a.textContent='Stack unavailable: '+((err&&err.message)||'start gateway + router + experts.');
    route.innerHTML='<span class="pill off">offline</span>';
    note('error: '+((err&&err.message)||err));
  }
  setBusy(false);
  $('#file').value='';
});
$('#msg').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();document.querySelector('#chat button').click()}});
$('#memadd').onclick=async()=>{
  const t=$('#memtext').value.trim();if(!t)return;
  $('#memout').textContent='storing…';
  try{const j=await api('/api/memory/add',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text:t})});$('#memout').textContent='stored ✓ '+(j.status||'');$('#memtext').value='';note('memory stored');diag()}
  catch(e){$('#memout').textContent='store failed: '+e.message}
};
$('#memsearch').onclick=async()=>{
  const q=$('#memq').value.trim();if(!q)return;
  $('#memhits').textContent='searching…';
  try{const j=await api('/api/memory/search',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({query:q})});const rs=j.results||[];$('#memhits').innerHTML=rs.length?rs.map((r,i)=>'<div class="layer"><p><b style="color:#c9e8ff">#'+(i+1)+'</b> '+esc(String(r).slice(0,220))+'</p></div>').join(''):'<p>No matches.</p>';note('memory search: '+rs.length+' hits')}
  catch(e){$('#memhits').textContent='search failed: '+e.message}
};
stack();diag();act();
setInterval(()=>{stack();diag()},15000);
setInterval(act,20000);
note('console boot complete — session '+session.slice(0,8));
