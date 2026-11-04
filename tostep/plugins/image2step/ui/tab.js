// ---- Image → STEP tab
const IMS=['calsheet','token','markers','dpi','image_width','mm_per_px','square'];
function imShow(){const s=$('im-scale').value,kd=$('im-tkind').value;document.querySelectorAll('.im-hgt').forEach(e=>e.classList.toggle('hide',s!=='token'&&s!=='calsheet'));for(const k of IMS)document.querySelectorAll('.im-'+k).forEach(e=>e.classList.toggle('hide',k!==s));
  for(const k of ['paper','plastic'])document.querySelectorAll('.imk-'+k).forEach(e=>e.classList.toggle('hide',s!=='token'||k!==kd))}
imShow();
{for(const id of ['im-standh','im-across','im-along']){const e=$(id);try{const v=localStorage.getItem('tostep-'+id);if(v)e.value=v}catch(x){}e.addEventListener('change',()=>{try{localStorage.setItem('tostep-'+id,e.value)}catch(x){}})}}
function imFind(){const tr=$('im-find').value==='transitions';document.querySelectorAll('.im-tr').forEach(e=>e.classList.toggle('hide',!tr));document.querySelectorAll('.im-tone').forEach(e=>e.classList.toggle('hide',tr))}
let IMEMPTY=null;
function imEmptyPick(){const p=$('pick');p.accept='.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp';p.onchange=()=>{if(p.files[0])imEmpty(p.files[0]);p.value=''};p.click()}
async function imEmpty(file){const f=$('f-img2');if(!file){IMEMPTY=null;f.textContent='None: the part is told from the paper in one photo.';$('x-img2').style.display='none';return}
  f.textContent='Uploading '+file.name+'…';
  try{const r=await fetch('/api/upload?name='+encodeURIComponent(file.name),{method:'POST',headers:{'X-Token':TOKEN},body:file});const j=await r.json();if(!r.ok)throw new Error(j.error);
    IMEMPTY=j.id;f.textContent=j.name+': compared with the photo of the part (its shadow and the paper\'s banding are taken out).';$('x-img2').style.display=''}catch(e){IMEMPTY=null;f.textContent=e.message}}
function imParams(){return{empty_sheet:($('im-scale').value==='calsheet'&&IMEMPTY)?IMEMPTY:'',stand_height:$('im-standh').value,sheet_across:$('im-across').value,sheet_along:$('im-along').value,scale:$('im-scale').value,token_kind:$('im-tkind').value,token_side:$('im-side').value,token_thickness:$('im-tthick').value,above:$('im-above').value,camera:$('im-cam').value,straight_walls:$('im-walls').checked,
  check_line_mm:$('im-check').value,width_mm:$('im-width').value,mm_per_px:$('im-mmpx').value,square_mm:$('im-sq').value,
  tone:$('im-tone').value,line_art:$('im-line').value,enlarge:$('im-enl').value,picture_edge:$('im-edge').value,find:$('im-find').value,colour_step:$('im-cstep').value,shared_edges:$('im-shared').value,weld_edges:$('im-weld').checked,thickness:$('im-thick').value,curves_only:$('im-wires').checked,
  borderline:$('im-maybe').checked,also_svg:$('im-svg').checked,also_dxf:$('im-dxf').checked}}
{const d=$('d-img');d.onclick=()=>{const p=$('pick');p.accept='.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp';p.onchange=()=>{if(p.files[0])imUpload(p.files[0]);p.value=''};p.click()};
 d.ondragover=e=>{e.preventDefault();d.classList.add('hot')};d.ondragleave=()=>d.classList.remove('hot');
 d.ondrop=e=>{e.preventDefault();d.classList.remove('hot');if(e.dataTransfer.files[0])imUpload(e.dataTransfer.files[0])}}
async function imUpload(file){const f=$('f-img');f.textContent='Uploading '+file.name+'…';$('b-img').disabled=true;$('b-img-prev').disabled=true;$('im-prev').innerHTML='';$('im-prev-t').classList.add('hide');
  try{const r=await fetch('/api/upload?name='+encodeURIComponent(file.name),{method:'POST',headers:{'X-Token':TOKEN},body:file});const j=await r.json();if(!r.ok)throw new Error(j.error);
    sel.img=j.id;f.textContent=j.name+' ('+fmt(j.size)+')';$('im-note').textContent='Looking for a token or a marker sheet…';
    try{const g=await api('/api/image/guess',{file:j.id});
      if(g.scale){$('im-scale').value=g.scale;$('im-note').textContent=g.note||''}
      else{if($('im-scale').value==='token'||$('im-scale').value==='calsheet'||$('im-scale').value==='markers')$('im-scale').value='image_width';
        $('im-note').textContent='No token or marker sheet found in this picture, so the scale is taken from the picture\'s width: type its real width in the box above, or choose another way to set the scale.'}
      imShow();pqAll()}catch(e){$('im-note').textContent=e.message}
    $('b-img').disabled=false;$('b-img-prev').disabled=false;imPreview()}
  catch(e){f.textContent='Upload failed: '+e.message}}
async function imPreview(){if(!sel.img)return;$('im-prev-i').textContent='Tracing…';
  try{const j=await api('/api/image/preview',{file:sel.img,params:imParams()});
    $('im-prev').innerHTML='<img alt="Traced outlines" style="max-width:100%;max-height:520px;border:1px solid var(--line);border-radius:8px" src="'+j.png+'">';
    const t=$('im-prev-t');t.textContent=j.info;t.classList.remove('hide');$('im-prev-i').textContent=''}
  catch(e){$('im-prev-i').textContent=e.message}}
function imRun(){start('img','image',imParams())}
function imDl(what){if(what==='calsheet'||what==='stand'){location.href='/api/image/'+what+'?t='+TOKEN+'&paper='+encodeURIComponent($('im-cpaper').value);return}if(what==='tokensheet'){location.href='/api/image/tokensheet?t='+TOKEN+'&side='+encodeURIComponent($('im-side').value)+'&paper='+encodeURIComponent($('im-tpaper').value);return}
  location.href=what==='token'?('/api/image/token?t='+TOKEN+'&side='+encodeURIComponent($('im-side').value)+'&thick='+encodeURIComponent($('im-tthick').value)):('/api/image/sheet?t='+TOKEN+'&paper='+encodeURIComponent($('im-paper').value))}
Object.assign(PQ,{
 'im-scale':v=>({calsheet:[.35,.97],token:[.3,.85],markers:[.3,.95],dpi:[.1,.9],image_width:[.1,.5],mm_per_px:[.1,.6],square:[.2,.7]}[v]||[.2,.6]),
 'im-enl':v=>{v=+v;return v===1?[.12,.45]:v===0?[.35,.85]:[cl(.2+.08*v),.85]},
 'im-walls':c=>c?[.2,.85]:[.15,.6],
 'im-thick':v=>[+v>0?.25:.12,.7],
 'im-wires':c=>c?[.08,.3]:[.12,.7]
});
