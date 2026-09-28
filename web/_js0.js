
const $=s=>document.querySelector(s);
const thread=$('#thread'),log=$('#log'),route=$('#route'),session=crypto.randomUUID();
const key=()=>($('#apikey').value||'').trim();
const hdr=()=>key()?{'content-type':'application/json','x-api-key':key()}:{'content-type':'application/json'};
const esc=t=>String(t).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function md(t){const out=[];String(t).split('```').forEach((b,i)=>{if(i%2)out.push('<pre class="code">'+esc(b.replace(/^[a-zA-Z0-9_+.-]*\n/,''))+'</pre>');else out.push(esc(b).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\n/g,'<br>'))});return out.join('')}
const say=(t,c)=>{const d=document.createElement('div');d.className='msg '+(c==='u'?'u':c==='e'?'err':'a');if(c==='a'&&t)d.innerHTML=md(t);else d.textContent=t;thread.append(d);d.scrollIntoView({block:'nearest',behavior:'smooth'});return d};
const paint=(el,t,live)=>{el.innerHTML=md(t)+(live?'<span class="cursor"></span>':'')};
function badge(el,text,live){let b=el.querySelector('.badge');if(!b){b=document.createElement('span');b.className='badge';el.prepend(b)}b.textContent=text;b.classList.toggle('live',!!live)}
const note=t=>{log.innerHTML+='<br>'+new Date().toLocaleTimeString()+' '+t};
async function sse(res,onFrame){const rd=res.body.getReader(),dc=new TextDecoder();let buf='';for(;;){const {value,done}=await rd.read();if(done)break;buf+=dc.decode(value,{stream:true});let cut;while((cut=buf.indexOf('\n\n'))>=0){const raw=buf.slice(0,cut);buf=buf.slice(cut+2);let ev='message';const data=[];raw.split('\n').forEach(line=>{if(line.startsWith('event:'))ev=line.slice(6).trim();else if(line.startsWith('data:'))data.push(line.slice(5).replace(/^ /,''))});if(data.length)onFrame(ev,data.join('\n'))}}}
const read=f=>new Promise(r=>{if(!f)return r(null);const x=new FileReader();x.onload=()=>r({kind:f.type.startsWith('image/')?'image':'audio',content_type:f.type,uri:x.result});x.readAsDataURL(f)});
const pill=s=>'<span class="pill '+(s==='ok'?'':'off')+'">'+esc(s)+'</span>';
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#tabs button').forEach(x=>x.classList.remove('on'));b.classList.add('on');['dashboard','stack','models','memory','activity','concept'].forEach(v=>$('#view-'+v).hidden=v!==b.dataset.view);note('view='+b.dataset.view)});
async function api(path,opts){const r=await fetch(path,Object.assign({headers:key()?{'x-api-key':key()}:{}},opts||{}));if(!r.ok)throw new Error(path+' '+r.status);return r.json()}
function stateOf(svc){if(!svc||svc.status!=='ok')return {cls:'off',txt:'down'};return svc.backend==='configured'?{cls:'',txt:'live'}:{cls:'warn',txt:'stub'}}
