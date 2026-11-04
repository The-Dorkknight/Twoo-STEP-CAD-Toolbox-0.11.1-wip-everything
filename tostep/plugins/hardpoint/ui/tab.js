// ---- Hardpoints tab
function hpDrop(){const d=$('d-hp');
  d.onclick=()=>{const p=$('pick');p.accept='.stl';p.onchange=()=>{if(p.files[0])hpUpload(p.files[0]);p.value=''};p.click()};
  d.ondragover=e=>{e.preventDefault();d.classList.add('hot')};d.ondragleave=()=>d.classList.remove('hot');
  d.ondrop=e=>{e.preventDefault();d.classList.remove('hot');if(e.dataTransfer.files[0])hpUpload(e.dataTransfer.files[0])}}
hpDrop();
function hpButtons(valid,have){$('b-hp').disabled=!valid;$('b-hp-nolab').disabled=!(valid||false);$('b-hp-make').disabled=!have}
async function hpUpload(file){
  const f=$('f-hp'),box=$('hp-info');f.textContent='Uploading '+file.name+'…';hpButtons(false,false);box.classList.add('hide');
  try{const r=await fetch('/api/upload?name='+encodeURIComponent(file.name),{method:'POST',headers:{'X-Token':TOKEN},body:file});const j=await r.json();if(!r.ok)throw new Error(j.error);
    sel.hp=j.id;f.textContent=j.name+' ('+fmt(j.size)+')';
    const i=await api('/api/hp/inspect',{file:j.id});hpShow(i)}
  catch(e){f.textContent='Failed: '+e.message}}
function hpShow(i){const box=$('hp-info');box.classList.remove('hide');
  const kinds=Object.entries(i.kinds||{}).map(([k,v])=>v+' '+k).join(', ');
  const tag=i.valid?'<b style="color:var(--ok)">Hardpoint STL, labels valid</b>':i.labelled?'<b style="color:var(--bad)">Hardpoint STL, labels out of date</b>':'<b>Plain STL</b>';
  box.innerHTML='<div class="result" style="display:block;margin:0">'+tag+'<br>'+esc(i.message)+
   (i.triangles!=null?'<br><span class="hint">'+i.triangles.toLocaleString()+' triangles'+(i.valid?' · '+i.faces+' faces ('+esc(kinds)+') · '+i.hardpoints+' hardpoints'+(i.unlabelled?' · '+i.unlabelled+' unlabelled triangles':''):'')+
   (i.labelled?' · units '+esc(i.units)+(i.tol?' · meshed to '+i.tol+' '+esc(i.units):'')+(i.by?' · written by '+esc(i.by):''):'')+'</span>':'')+'</div>';
  hpButtons(i.valid,i.triangles!=null)}
function runHp(what){const p={units:$('hp-units').value,mode:'surface',tol:$('hp-tol').value,engine:$('hp-engine').value,
  plane_angle:3,crease_angle:30,patch_size:30,max_bend:60,primitives:true,memory_guard:true,guess:false,decimate:0,smooth:0,
  use_labels:what==='labels'||what==='make',make_labelled:what==='make'};
  start('hp','convert',p)}
