hookDrop('conv','.stl,.obj,.ply,.3mf,.off');
for(const [id,suf] of [['patch','%'],['bend','°']]){const s=$(id);s.oninput=()=>$(id+'-v').textContent=s.value+suf}
function modeNow(){return document.querySelector('input[name=mode]:checked').value}
function showMode(){const m=modeNow(),pr=m==='extrude'||m==='loft';$('prof').classList.toggle('hide',!pr);$('settings').classList.toggle('hide',pr);
  document.querySelectorAll('#prof .ex').forEach(e=>e.classList.toggle('hide',m!=='extrude'));document.querySelectorAll('#prof .lo').forEach(e=>e.classList.toggle('hide',m!=='loft'))}
document.querySelectorAll('input[name=mode]').forEach(r=>r.onchange=showMode);showMode();
async function preview(){$('prev-i').textContent='Tracing…';$('prev').innerHTML='';
  try{const j=await api('/api/preview',{file:sel.conv,params:convParams()});$('prev').innerHTML=j.svg;$('prev-i').textContent=j.info}catch(e){$('prev-i').textContent=e.message}}
function convParams(){return{units:$('units').value,mode:modeNow(),tol:$('tol').value,
  engine:document.querySelector('input[name=engine]:checked').value,
  axis:$('axis').value,simplify:$('simp').value,source:$('src').value,slice_at:$('sliceat').value,height:$('height').value,
  max_slices:$('maxs').value,sensitivity:$('sens').value,smooth_sketch:$('ssk').checked,symmetric:$('sym').checked,heights:$('heights').value,
  plane_angle:$('plane').value,crease_angle:$('crease').value,patch_size:$('patch').value,max_bend:$('bend').value,
  primitives:$('prim').checked,memory_guard:$('memg').checked,guess:$('guess').checked,organic_tol:$('otol').value,decimate:$('dec').value,smooth:$('smooth').value,budget_s:$('budget').value,gap_fill:$('gapf').value}}
function runConv(){start('conv','convert',convParams())}
Object.assign(PQ,{
 tol:v=>{v=+v;if(!(v>0))return[.5,.6];const x=cl((Math.log10(v)+2.3)/2.3);return[.95-.8*x,.97-.7*x]},
 simp:v=>{v=+v;if(!(v>0))return[.4,.6];const x=cl((Math.log10(v)+2.3)/2.3);return[.9-.7*x,.95-.65*x]},
 plane:v=>[.8-.05*cl(+v,0,10),.9-.04*Math.abs(+v-3)],
 crease:v=>[cl(1.05-(+v-10)/55,.15,.95),.6+.3*cl(1-Math.abs(+v-25)/40)],
 patch:v=>[1-.8*(+v)/100,.95-.55*(+v)/100],
 bend:v=>[.9-.6*(+v)/120,.95-.5*(+v)/120],
 prim:c=>c?[.35,.9]:[.6,.5],
 memg:c=>c?[.2,.7]:[.85,.95],
 guess:c=>c?[.25,.6]:[.8,.9],
 ssk:c=>c?[.3,.9]:[.15,.5],
 otol:v=>{const x=cl((+v-.05)/.6);return[.6-.45*x,.85-.5*x]},
 dec:v=>{v=+v;if(!(v>0))return[.85,1];const x=cl((Math.log10(v)-3.3)/2.7);return[.2+.65*x,.35+.6*x]},
 smooth:v=>[.1+.04*cl(+v,0,12),.7+.015*cl(+v,0,12)],
 maxs:v=>[cl(+v/60),.3+.6*cl(+v/60)],
 sens:v=>[cl(.1+(.2-Math.log10(+v||.02)-.5)/3),.5+.3*cl(-Math.log10(+v||.02)/2)],
});
Object.assign(PQM,{mode:{surface:[.6,.85],faceted:[.5,.6],extrude:[.15,.4],loft:[.3,.5]},engine:{classic:[.5,.75],staged:[.35,.8],ladder:[.3,.8],v2:[.65,.7],best:[.95,.85],quad:[.45,.85],auto:[.4,.85]}});
UPLOADED.conv=()=>{$('b-prev').disabled=false};
// ---- quality presets (the table is app/presets.py)
const PRESETS=__PRESETS__;
function preRadio(name,v){const r=document.querySelector('input[name='+name+'][value="'+v+'"]');if(r){r.checked=true;r.dispatchEvent(new Event('change',{bubbles:true}))}}
function preApply(id){const adv=id==='advanced';const P=PRESETS.presets.find(p=>p.id===id);
  if(!adv&&!P)return preApply(PRESETS.default);
  document.querySelectorAll('#pre-btns button').forEach(b=>{const on=b.dataset.p===id;b.classList.toggle('on',on);b.setAttribute('aria-checked',on)});
  $('p-conv').classList.toggle('simple',!adv);
  if(!adv){const s=P.settings;preRadio('mode',s.mode);preRadio('engine',s.engine);
    const val={tol:'tol',plane_angle:'plane',crease_angle:'crease',patch_size:'patch',max_bend:'bend',organic_tol:'otol',decimate:'dec',smooth:'smooth',budget_s:'budget'};
    for(const k in val){const e=$(val[k]);e.value=s[k];e.dispatchEvent(new Event('input',{bubbles:true}))}
    const chk={primitives:'prim',memory_guard:'memg',guess:'guess'};
    for(const k in chk){const e=$(chk[k]);e.checked=!!s[k];e.dispatchEvent(new Event('change',{bubbles:true}))}
    $('gapf').value=s.gap_fill||'plate';
    $('otol').disabled=!s.guess;$('pre-d').textContent='';try{localStorage.setItem('tostep-preset-base',id)}catch(e){}}
  else $('pre-d').textContent='Every setting is shown below. Change anything you like; picking a preset again sets them back.';
  try{localStorage.setItem('tostep-preset',id)}catch(e){}}
function preBars(p,q){const seg=(x,c)=>'<u class="'+c+'">'+[0,1,2,3,4,5,6,7].map(i=>'<i'+(x>(i+.5)/8-.03?' class="on"':'')+'></i>').join('')+'</u>';
  return '<div class="bb" title="Load on your computer '+Math.round(p*100)+'% · quality '+Math.round(q*100)+'%"><span>Load</span>'+seg(p,'r')+'<span>Quality</span>'+seg(q,'g')+'</div>'}
function prePic(kind){   // flat face (left) and an organic bump (right): grey = the mesh, blue = what the STEP gets
  const hy=x=>58-30*Math.pow(Math.sin(Math.PI*(x-76)/80),1.4)-3*Math.sin((x-76)/6);
  const xs=[];for(let x=76;x<=156;x+=6.67)xs.push(x);
  const mesh=xs.map(x=>x.toFixed(1)+','+hy(x).toFixed(1)).join(' ');
  const sm=(off,a,b)=>{let d='';for(let x=a;x<=b+.01;x+=1)d+=(d?'L':'M')+x.toFixed(1)+','+(hy(x)-off).toFixed(1);return d};
  let org='',note='';
  if(kind==='loose'){org='<path d="'+sm(0,76,156)+'L156,58L76,58Z" fill="none"/><path d="'+sm(6,76,156)+'" stroke="var(--acc)" stroke-width="2.5" fill="none"/>'
    +'<path d="'+sm(6,76,156)+'L'+[...xs].reverse().map(x=>x.toFixed(1)+','+hy(x).toFixed(1)).join('L')+'Z" fill="var(--acc)" opacity=".22"/>';note='up to 0.5 mm off'}
  else if(kind==='near'){org='<path d="'+sm(2.5,76,156)+'" stroke="var(--acc)" stroke-width="2.5" fill="none"/>';note='up to 0.3 mm off'}
  else if(kind==='fit'){org='<path d="'+sm(0,76,129)+'" stroke="var(--acc)" stroke-width="2.5" fill="none"/><polyline points="'+xs.filter(x=>x>=129).map(x=>x.toFixed(1)+','+hy(x).toFixed(1)).join(' ')+'" stroke="#3b3f47" stroke-width="2.5" fill="none"/>';note='facets where it can\'t fit'}
  else if(kind==='best'){org='<path d="'+sm(0,76,156)+'" stroke="var(--acc)" stroke-width="2.5" fill="none"/>';note='2 fitters, best kept'}
  else return '<div class="pic"><svg viewBox="0 0 160 66" aria-hidden="true">'+[16,33,50].map((y,i)=>'<line x1="14" y1="'+y+'" x2="146" y2="'+y+'" stroke="var(--line)" stroke-width="3" stroke-linecap="round"/><circle cx="'+[48,110,78][i]+'" cy="'+y+'" r="6" fill="var(--acc)"/>').join('')+'</svg><div class="pcap"><span>every setting, your choice</span></div></div>';
  return '<div class="pic"><svg viewBox="0 0 160 66" aria-hidden="true"><rect x="6" y="22" width="56" height="36" fill="none" stroke="#9aa3b2" stroke-width="1"/>'
    +'<polyline points="6,22 20,22 34,22 48,22 62,22" stroke="#9aa3b2" stroke-width="1" fill="none"/><line x1="6" y1="22" x2="62" y2="22" stroke="var(--acc)" stroke-width="3.5" stroke-linecap="round"/>'
    +'<polyline points="'+mesh+'" stroke="#9aa3b2" stroke-width="1" fill="none"/>'+xs.map(x=>'<circle cx="'+x.toFixed(1)+'" cy="'+hy(x).toFixed(1)+'" r="1.3" fill="#9aa3b2"/>').join('')
    +org+'<line x1="76" y1="58" x2="156" y2="58" stroke="#9aa3b2" stroke-width="1"/></svg><div class="pcap"><span>flat faces: exact</span><span>organic: '+note+'</span></div></div>'}
{const box=$('pre-btns');for(const p of PRESETS.presets.concat([{id:'advanced',name:'Advanced…',tag:'opens the full panel'}])){
  const b=document.createElement('button');b.type='button';b.dataset.p=p.id;b.setAttribute('role','radio');
  b.innerHTML='<div class="ph"><b>'+esc(p.name)+'</b><small>'+esc(p.tag)+'</small></div>'+(p.pq?preBars(p.pq[0],p.pq[1]):'<div class="bb"><span>Load</span><span>yours to set</span><span>Quality</span><span>yours to set</span></div>')+prePic(p.pic||'')+'<span class="pl">'+esc(p.line||'Shows every setting of this tab (mode, tolerances, surface fitter, performance), starting from the preset chosen before.')+'</span>';
  b.onclick=()=>preApply(p.id);box.appendChild(b)}
 let first=PRESETS.default,base=PRESETS.default;try{first=localStorage.getItem('tostep-preset')||first;base=localStorage.getItem('tostep-preset-base')||base}catch(e){}
 if(first==='advanced')preApply(base);preApply(first)}
