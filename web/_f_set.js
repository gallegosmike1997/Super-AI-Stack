
function set(id,html){var el=document.getElementById(id);if(el){el.innerHTML=html}}
function txt(id,v){var el=document.getElementById(id);if(el){el.textContent=v}}
function fmtUp(s){s=s||0;var h=Math.floor(s/3600),m=Math.floor((s%3600)/60);return h?h+'h '+m+'m':m+'m '+(s%60)+'s'}
function renderStack(d){
  var cat=d.catalog||[],svcs=d.services||{};
  set('catalog','<table class="tbl"><tr><th>LAYER</th><th>MODEL</th><th>PURPOSE</th><th>STATE</th></tr>'+cat.map(function(c){
    var s=svcs[c.expert]||{status:'offline'};return '<tr><td>'+esc(c.layer)+'</td><td>'+esc(c.model)+'</td><td>'+esc(c.purpose)+'</td><td>'+pill(s.status)+'</td></tr>'
  }).join('')+'</table>');
  set('pipeline',cat.map(function(c,i){
    var s=svcs[c.expert]||{status:'offline'};return '<div class="layer"><h4><em>'+String(i+1).padStart(2,'0')+'</em></h4><h4 style="margin-left:6px">'+esc(c.layer)+' · '+esc(c.expert)+'</h4><p>'+esc(c.purpose)+' — model <b style="color:#c9e8ff">'+esc(c.model)+'</b> '+pill(s.status)+'</p></div>'
  }).join(''));
  var okN=cat.filter(function(c){return (svcs[c.expert]||{}).status=='ok'}).length;
  set('rail',cat.map(function(c){
    var s=svcs[c.expert]||{status:'offline'};return '<div class="rowline"><span class="k">'+esc(c.expert)+'</span>'+pill(s.status)+'</div>'
  }).join('')+'<div class="rowline"><span class="k">healthy</span><b>'+okN+'/'+cat.length+'</b></div>');
  var ld=document.getElementById('livedot'),lt=document.getElementById('livetxt');
  if(ld){ld.className='dot'+(okN===cat.length?'':' off')}
  if(lt){lt.textContent=okN===cat.length?'ALL SYSTEMS LIVE':okN+'/'+cat.length+' UP'}
  set('concepts',cat.map(function(c){
    var s=svcs[c.expert]||{status:'offline'};return '<div class="layer"><h4><em>◆</em><span style="margin-left:6px">'+esc(c.layer)+'</span> <span class="chip'+(s.status=='ok'?' on':'')+'">'+pill(s.status)+'</span></h4><p style="margin-left:18px">'+esc(c.purpose)+' · '+esc(c.model)+'</p></div>'
  }).join(''));
}
