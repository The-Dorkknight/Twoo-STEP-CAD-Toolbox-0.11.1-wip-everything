// ---- SCAD → STEP tab
let SC={cur:null,params:[],liveOn:false,liveSince:0,liveTimer:null};
async function scStatus(){try{const j=await api('/api/scad/status');scShowOs(j.openscad);if(j.preselect&&!SC.cur){scSetFile(j.preselect);tab('scad')}
  $('sc-recent').innerHTML=j.recent.map(p=>'<button class="btn2" title="'+esc(p)+'" data-p="'+esc(p)+'">'+esc(p.split(/[\\/]/).pop())+'</button>').join('');
  for(const b of $('sc-recent').children)b.onclick=()=>scOpenPath(b.dataset.p)}catch(e){}}
function scShowOs(o){$('sc-os').innerHTML=o.found?('OpenSCAD found: <code>'+esc(o.cmd)+'</code> '+esc(o.version||'')):'<b>OpenSCAD not found.</b> Install it from openscad.org, or enter its location below. (.csg files still convert without it.)';
  $('sc-oscfg').classList.toggle('hide',!!o.found)}
async function scSaveOs(){try{scShowOs((await api('/api/scad/settings',{openscad:$('sc-ospath').value})).openscad)}catch(e){alert(e.message)}}
async function scFindOs(){try{scShowOs((await api('/api/scad/find-openscad',{})).openscad)}catch(e){alert(e.message)}}
function scSetFile(j){SC.cur=j;sel.scad=j.id;$('sc-chosen').classList.remove('hide');$('sc-name').textContent=j.name;
  $('sc-wpath').textContent=j.path||'(dropped in: the STEP goes to the Output folder)';$('sc-open').style.display=j.local?'':'none';
  $('f-scad').textContent=j.name;$('b-scad').disabled=false;$('b-sc-live').disabled=!j.local;
  if(!j.local)$('sc-where').value='output';
  SC.params=j.params||[];scDrawParams()}
async function scBrowse(){try{const j=await api('/api/scad/browse',{start:SC.cur&&SC.cur.path?SC.cur.path.replace(/[^\\/]*$/,''):''});
  if(j.cancelled){$('sc-path').focus();return}scSetFile(j);scStatus()}catch(e){alert(e.message)}}
async function scOpenPath(p){try{scSetFile(await api('/api/scad/open-path',{path:p}));scStatus()}catch(e){alert(e.message)}}
$('sc-path').onkeydown=e=>{if(e.key==='Enter'&&$('sc-path').value.trim())scOpenPath($('sc-path').value)};
async function scRescan(){if(SC.cur){try{const j=await api('/api/scad/rescan',{file:SC.cur.id});const old=scValues();SC.params=j.params;scDrawParams(old)}catch(e){alert(e.message)}}}
async function scOpenIn(){if(SC.cur)api('/api/scad/open-in-openscad',{file:SC.cur.id})}
function scUnq(v){return String(v).replace(/^"|"$/g,'').replace(/\\"/g,'"')}
function scDrawParams(keep){keep=keep||{};const g=$('sc-params');g.innerHTML='';$('sc-pcard').classList.toggle('hide',!SC.params.length);let grp=null;
  SC.params.forEach((p,i)=>{if(p.group&&p.group!==grp){grp=p.group;g.insertAdjacentHTML('beforeend','<div class="grp" style="grid-column:1/-1">'+esc(grp)+'</div>')}
    const v=keep[p.name]!==undefined?keep[p.name]:(p.kind==='string'?scUnq(p.value):p.value);let inp;
    if(p.options){inp='<select id="scp'+i+'" style="width:150px">'+p.options.map((o,k)=>{const ov=p.kind==='string'?scUnq(o):o;return '<option value="'+esc(ov)+'"'+(ov==v?' selected':'')+'>'+esc(scUnq(p.labels[k]))+'</option>'}).join('')+'</select>'}
    else if(p.kind==='bool'){inp='<input type="checkbox" id="scp'+i+'"'+(String(v)==='true'?' checked':'')+'>'}
    else if(p.kind==='number'){inp='<input type="number" id="scp'+i+'" value="'+esc(v)+'"'+(p.min!==undefined?' min="'+p.min+'" max="'+p.max+'"':'')+' step="'+(p.step||'any')+'">'}
    else inp='<input type="text" id="scp'+i+'" value="'+esc(v)+'" style="width:150px;padding:5px 7px;border:1px solid var(--line);border-radius:7px;background:var(--bg);color:var(--ink);font:inherit">';
    g.insertAdjacentHTML('beforeend','<label><span>'+esc(p.name)+(p.desc?'<br><small class="hint">'+esc(p.desc)+'</small>':'')+'</span>'+inp+'</label>')})}
function scValues(){const o={};SC.params.forEach((p,i)=>{const e=$('scp'+i);if(e)o[p.name]=e.type==='checkbox'?(e.checked?'true':'false'):e.value});return o}
function scOpts(){return{units:$('sc-units').value,faceted_fn:$('sc-ffn').value,where:$('sc-where').value,verify:$('sc-verify').checked,smooth_outlines:$('sc-smooth').checked}}
{const d=$('d-scad');d.onclick=()=>{const p=$('pick');p.accept='.scad,.csg';p.onchange=()=>{if(p.files[0])scUpload(p.files[0]);p.value=''};p.click()};
 d.ondragover=e=>{e.preventDefault();d.classList.add('hot')};d.ondragleave=()=>d.classList.remove('hot');
 d.ondrop=e=>{e.preventDefault();d.classList.remove('hot');if(e.dataTransfer.files[0])scUpload(e.dataTransfer.files[0])}}
async function scUpload(file){try{const r=await fetch('/api/scad/upload?name='+encodeURIComponent(file.name),{method:'POST',headers:{'X-Token':TOKEN},body:file});const j=await r.json();if(!r.ok)throw new Error(j.error);scSetFile(j)}catch(e){alert('Upload failed: '+e.message)}}
function scRun(){start('scad','scad',Object.assign(scOpts(),{values:scValues()}))}
async function scLive(){try{if(SC.liveOn){await api('/api/scad/watch/stop',{})}else{const j=await api('/api/scad/watch/start',{file:SC.cur.id,opts:scOpts(),values:{}});$('sc-lout').textContent=j.out;$('log-scad').textContent='';$('res-scad').style.display='none';SC.liveSince=0}}catch(e){alert(e.message)}scPollLive()}
async function scPollLive(){clearTimeout(SC.liveTimer);let j;try{j=await api('/api/scad/watch?since='+SC.liveSince)}catch(e){return}
  SC.liveOn=j.on;$('b-sc-live').textContent=j.on?'Stop live link':'Live link';$('sc-livebox').classList.toggle('hide',!j.on);
  if(j.on){const l=$('log-scad');if(j.log.length){l.textContent+=j.log.join('\n')+'\n';l.scrollTop=l.scrollHeight}SC.liveSince=j.next;
    $('sc-lstate').textContent=j.runs?('Last update '+j.when+(j.ok?': closed solid.':': check the log.')):'Converting…';SC.liveTimer=setTimeout(scPollLive,800)}}
scStatus();scPollLive();
