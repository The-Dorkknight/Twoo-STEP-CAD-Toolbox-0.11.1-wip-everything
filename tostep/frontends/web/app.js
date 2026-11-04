const TOKEN="__TOKEN__", OUT=__OUT__;
window.TABS=__TABS__;                 // tab ids, in order (from the plugins' manifests)
const KIND_TAB=__KIND_TAB__, KIND_NAME=__KIND_NAME__, NO_ORIG=__NO_ORIG__, NO_PREVIEW=__NO_PREVIEW__;
const $=id=>document.getElementById(id);
const sel={},UPLOADED={};          // UPLOADED[tab]: what a tab does after its file arrived
async function integ(w){const l=$('log-plug');l.textContent='Working…';try{const j=await api('/api/integrate',{what:w});l.textContent=j.lines.join('\n')}catch(e){l.textContent=e.message}}
function tab(t){for(const k of window.TABS){$('p-'+k).classList.toggle('hide',k!==t);$('t-'+k).classList.toggle('on',k===t)}}
async function api(path,body){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'X-Token':TOKEN,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const j=await r.json();if(!r.ok)throw new Error(j.error||r.statusText);return j}
function fmt(n){return n>1e6?(n/1e6).toFixed(1)+' MB':(n/1e3).toFixed(0)+' kB'}
async function upload(which,file){
  const f=$('f-'+which);f.textContent='Uploading '+file.name+'…';$('b-'+which).disabled=true;
  try{const r=await fetch('/api/upload?name='+encodeURIComponent(file.name),{method:'POST',headers:{'X-Token':TOKEN},body:file});const j=await r.json();if(!r.ok)throw new Error(j.error);
    sel[which]=j.id;f.textContent=j.name+' ('+fmt(j.size)+')';$('b-'+which).disabled=false;if(UPLOADED[which])UPLOADED[which]()}
  catch(e){f.textContent='Upload failed: '+e.message}}
function hookDrop(which,accept){const d=$('d-'+which);
  d.onclick=()=>{const p=$('pick');p.accept=accept;p.onchange=()=>{if(p.files[0])upload(which,p.files[0]);p.value=''};p.click()};
  d.ondragover=e=>{e.preventDefault();d.classList.add('hot')};d.ondragleave=()=>d.classList.remove('hot');
  d.ondrop=e=>{e.preventDefault();d.classList.remove('hot');if(e.dataTransfer.files[0])upload(which,e.dataTransfer.files[0])}}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
const VU=$('vu');for(let i=0;i<20;i++)VU.appendChild(document.createElement('i'));
async function actTick(){try{const a=await api('/api/activity');const n=Math.round(a.cpu/5);
  [...VU.children].forEach((e,i)=>e.className=i<n?(i<12?'g':i<17?'y':'r'):'');
  $('led').className=a.disk>4000?'on':'';$('lcd').textContent=String(Math.round(a.cpu)).padStart(2,'0')+'%'}catch(e){}setTimeout(actTick,600)}actTick();
const RUN={};
function fmtT(s){s=Math.max(0,Math.round(s));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')}
async function cancelJob(which){const jid=RUN[which];if(!jid)return;try{await api('/api/cancel',{job:jid})}catch(e){}}
function resetPanel(which){RUN[which]=null;const own=which==='conv'||which==='wash';if(own){sel[which]=null;$('f-'+which).textContent='No file chosen';$('b-'+which).disabled=true}
  $('log-'+which).textContent='';$('bar-'+which).style.width='0';$('al-'+which).textContent='';
  const r=$('res-'+which);r.style.display='none';$('x-'+which).classList.add('hide')}
const cl=(x,a=0,b=1)=>Math.min(b,Math.max(a,x));
const PQ={},PQM={};
function pqHTML(p,q){const seg=(x,c)=>'<u class="'+c+'">'+[0,1,2,3].map(i=>'<i'+(x>(i+.5)/4-.06?' class="on"':'')+'></i>').join('')+'</u>';return seg(p,'r')+seg(q,'g')}
function pqSet(el,p,q){let s=el.querySelector(':scope>.pq');if(!s){s=document.createElement('span');s.className='pq';el.insertBefore(s,el.firstChild)}
  s.innerHTML=pqHTML(p,q);s.title='Load on your computer '+Math.round(p*100)+'% · quality '+Math.round(q*100)+'%'}
function pqAll(){for(const id in PQ){const e=$(id);if(!e)continue;const lab=e.closest('label')||e.closest('.row');if(!lab)continue;
    const v=e.type==='checkbox'?e.checked:e.value;const[p,q]=PQ[id](v);pqSet(lab,p,q)}
  for(const g in PQM){const r=document.querySelector('input[name='+g+']:checked');if(!r)continue;
    document.querySelectorAll('input[name='+g+']').forEach(x=>{const[p,q]=PQM[g][x.value]||[.5,.5];pqSet(x.closest('label'),p,q)})}}
document.addEventListener('input',pqAll);document.addEventListener('change',pqAll);pqAll();
// ---- front-panel toys
let KP=1;function keyTurn(){KP=(KP+1)%3;$('keyk').style.transform='rotate('+[-45,0,45][KP]+'deg)';$('turbo').classList.toggle('on',KP===2)}
let HB=false,JOBS_ALIVE=false;setInterval(()=>{HB=!HB;const h=$('hb');h.className='lamp'+(JOBS_ALIVE==='quiet'?' hold':'');if(JOBS_ALIVE&&HB)h.classList.add('on')},500);
function startAtVal(){const w=$('when').value;if(w==='now')return null;const d=new Date();
  if(w==='1h')return d.getTime()/1000+3600;if(w==='3h')return d.getTime()/1000+10800;
  let t=$('whent').value||'01:00';if(w==='tonight')t='01:00';const[h,m]=t.split(':').map(Number);
  const x=new Date();x.setHours(h,m,0,0);if(x<=d)x.setDate(x.getDate()+1);return x.getTime()/1000}
function fmtClock(ts){return new Date(ts*1000).toLocaleString([], {weekday:'short',hour:'2-digit',minute:'2-digit'})}
async function start(which,kind,params){
  const log=$('log-'+which),btn=$('b-'+which);
  let jid;try{jid=(await api('/api/run',{kind,file:sel[which],params,start_at:which==='conv'?startAtVal():null})).job}catch(e){log.textContent=e.message;return}
  watch(which,jid);qTick()}
function watch(which,jid){
  const log=$('log-'+which),bar=$('bar-'+which),res=$('res-'+which),btn=$('b-'+which);
  log.textContent='';bar.style.width='0';res.style.display='none';res.className='result';$('x-'+which).classList.remove('hide');
  RUN[which]=jid;let since=0;
  const poll=async()=>{if(RUN[which]!==jid)return;let j;try{j=await api('/api/job/'+jid+'?since='+since)}catch(e){log.textContent+='\nLost connection to the app — is its window still open?';return}
    if(RUN[which]!==jid)return;
    if(j.log.length){log.textContent+=j.log.join('\n')+'\n';log.scrollTop=log.scrollHeight}since=j.next;
    bar.style.width=Math.round(100*j.progress)+'%';
    if(j.state==='cancelled'){JOBS_ALIVE=false;resetPanel(which);return}
    const run=j.state==='running';JOBS_ALIVE=run?((j.now-j.last_msg>45)?'quiet':true):false;
    $('al-'+which).textContent=j.state==='scheduled'?'Scheduled for '+fmtClock(j.start_at)+' — keep this app open (it keeps the computer awake).':
      j.state==='queued'?'Waiting in the queue for a free slot…':
      'Working… running '+fmtT(j.now-j.started)+' · last message '+fmtT(j.now-j.last_msg)+' ago · '+Math.round(100*j.progress)+'% (big steps can sit still for minutes — the CPU bar shows it is alive)';
    if(j.state==='done'){JOBS_ALIVE=false;$('x-'+which).classList.add('hide');$('al-'+which).textContent='';res.style.display='block';
      res.innerHTML='<b>Done</b> — saved as <code>'+esc(j.saved_to)+'</code><pre>'+esc(j.summary)+'</pre>'+
       '<a class="btn" style="text-decoration:none" href="/api/download/'+jid+'?t='+TOKEN+'">Download '+esc(j.filename)+'</a> '+
       (j.labelled_saved?'<a class="btn2" style="text-decoration:none" href="/api/download/'+jid+'?what=labels&t='+TOKEN+'">Download the hardpoint STL</a> ':'')+
       '<button class="btn2" onclick="api(\'/api/reveal/'+jid+'\',{})">Show in folder</button> '+
       (NO_PREVIEW.includes(j.kind)?'':'<button class="btn2" onclick="view3d(\''+jid+'\','+(NO_ORIG.includes(j.kind))+')">Preview 3D</button>');if(!NO_PREVIEW.includes(j.kind))view3d(jid,NO_ORIG.includes(j.kind));return}
    if(j.state==='error'){JOBS_ALIVE=false;$('x-'+which).classList.add('hide');$('al-'+which).textContent='';res.style.display='block';res.className='result err';res.innerHTML='<b>Failed:</b> '+esc(j.error||'')+'<br><button class="btn2" style="margin-top:8px" onclick="viewDiag(\''+jid+'\')">Show the diagnostic code</button>';return}
    setTimeout(poll,400)};
  poll()}
// ---- queue list
function qCancel(id){api('/api/cancel',{job:id}).then(qTick)}
function qShow(id,kind){const w=KIND_TAB[kind]||window.TABS[0];tab(w);watch(w,id)}
async function qTick(){try{const j=(await api('/api/jobs')).jobs;const c=$('qcard');
  c.classList.toggle('hide',j.length<2&&!j.some(x=>x.state==='scheduled'));
  $('qrows').innerHTML=j.slice().reverse().map(x=>{const act=['queued','scheduled','running'].includes(x.state);
    const st=x.state==='scheduled'?'scheduled '+fmtClock(x.start_at):x.state;
    return '<div class="qr"><b>'+esc(x.name)+'</b><span class="hint">'+(KIND_NAME[x.kind]||x.kind)+' · '+st+'</span>'+
      '<span class="qb"><i style="width:'+Math.round(100*x.progress)+'%"></i></span>'+
      '<button class="btn2" onclick="qShow(\''+x.id+'\',\''+x.kind+'\')">Show</button>'+
      (act?'<button class="btn2" onclick="qCancel(\''+x.id+'\')">Cancel</button>':'')+'</div>'}).join('')}catch(e){}
  setTimeout(qTick,1500)}
qTick();
// ---- performance profile
async function perfLoad(){const p=await api('/api/perf');const h=p.hw,e=p.effective;
  $('prof-sel').value=p.profile;
  $('prof-d').textContent=p.descriptions[e.name]||'';
  $('prof-hw').textContent='Detected: '+h.physical+' cores / '+h.logical+' threads, '+h.ram_gb+' GB RAM, '+(h.gpu_hardware||'no usable graphics card')+
    '. Using “'+e.name+'”: '+e.threads+' thread(s), '+e.jobs+' job(s) at once'+(e.parallel_engines?', parallel Second pass':'')+(e.gpu?', GPU on':'')+'.';
  $('gpu-ck').checked=p.gpu_wanted;
  const ST={ready:['ok','Ready'],install:['go','Needs support installed'],fix:['bad','Installed support does not work for it'],no:['no','Not usable']};
  const cs=p.gpus||[];
  $('gpu-cards').innerHTML=cs.length?cs.map(c=>{const st=ST[c.state]||ST.no;
    const act=c.state==='install'||c.state==='fix'?'<button class="btn2" '+(p.gpu_busy?'disabled ':'')+'onclick="gpuInstall(this.dataset.n)" data-n="'+esc(c.name)+'">'+(c.state==='fix'?'Reinstall the right build':'Install support')+(c.plan?' ('+esc(c.plan.size)+')':'')+'</button>':'';
    return '<div class="gpurow gpu-'+st[0]+'"><b>'+esc(c.name)+'</b> <span class="chip">'+st[1]+'</span><div class="hint">'+esc(c.why||'')+(c.detail?' <br>Last test said: '+esc(c.detail):'')+
      (c.driver?' <br>Driver '+esc(c.driver)+(c.cc?', compute capability '+c.cc:''):'')+'</div>'+act+'</div>'}).join(''):
    '<div class="hint">The operating system did not list a graphics card, so there is nothing to use. (On Linux this needs the <code>lspci</code> tool; on Windows, PowerShell.) Press Look again after installing a driver.</div>';
  if(h.gpu_backend){$('gpu-ck').disabled=false}
  if(p.gpu_pack_log.length){$('gpu-log').style.display='block';$('gpu-log').textContent=p.gpu_pack_log.join('\n');$('gpu-log').scrollTop=1e9}
  ['gpu-test','gpu-look'].forEach(i=>$(i).disabled=!!p.gpu_busy);
  const PP={potato:[.15,.75],moderate:[.55,.9],extreme:[.95,.95]}[e.name]||[.5,.8];pqSet($('prof-row'),PP[0],PP[1])}
async function perfSet(){await api('/api/perf',{profile:$('prof-sel').value,gpu:$('gpu-ck').checked});perfLoad()}
async function gpuWait(){const t=setInterval(async()=>{await perfLoad();const L=$('gpu-log').textContent.split('\n'),last=L[L.length-1];if(/^(Done\.|Failed)/.test(last))clearInterval(t)},1500)}
async function gpuAct(url,body){try{await api(url,body||{})}catch(e){$('gpu-log').style.display='block';$('gpu-log').textContent=e.message;return}
  $('gpu-log').style.display='block';gpuWait()}
function gpuInstall(n){gpuAct('/api/gpupack',{card:n})}
function gpuTest(){gpuAct('/api/gputest')}
function gpuLook(){gpuAct('/api/gpulook')}
perfLoad();
// ---- tiny WebGL viewer (no internet needed)
const V={job:null,cache:{},gl:null,prog:null,buf:null,n:0,rx:-0.9,ry:0.6,dist:2.6,pan:[0,0],c:[0,0,0],r:1};
function b64f(s){const b=atob(s),u=new Uint8Array(b.length);for(let i=0;i<b.length;i++)u[i]=b.charCodeAt(i);return new Float32Array(u.buffer)}
function vinit(){if(V.gl)return;const cv=$('gl'),gl=cv.getContext('webgl',{antialias:true});if(!gl){$('v-info').textContent='WebGL not available in this browser';return}
  V.gl=gl;const sh=(t,src)=>{const s=gl.createShader(t);gl.shaderSource(s,src);gl.compileShader(s);return s};
  const p=gl.createProgram();
  gl.attachShader(p,sh(gl.VERTEX_SHADER,'attribute vec3 p;attribute vec3 n;attribute vec3 c;uniform mat4 m;uniform mat3 r;varying vec3 vc;varying vec3 vn;void main(){gl_Position=m*vec4(p,1.0);vn=r*n;vc=c;}'));
  gl.attachShader(p,sh(gl.FRAGMENT_SHADER,'precision mediump float;varying vec3 vc;varying vec3 vn;void main(){vec3 N=normalize(vn);float d=abs(N.z)*0.65+max(dot(N,normalize(vec3(0.4,0.6,0.7))),0.0)*0.35;gl_FragColor=vec4(vc*(0.28+0.72*d),1.0);}'));
  gl.linkProgram(p);V.prog=p;
  const cv2=cv;let drag=null;
  cv2.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,pan:e.button===2||e.shiftKey};cv2.setPointerCapture(e.pointerId)};
  cv2.onpointermove=e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;
    if(drag.pan){V.pan[0]+=dx/cv2.clientHeight*V.dist;V.pan[1]-=dy/cv2.clientHeight*V.dist}else{V.ry+=dx*0.01;V.rx+=dy*0.01}draw()};
  cv2.onpointerup=()=>drag=null;cv2.oncontextmenu=e=>e.preventDefault();
  cv2.onwheel=e=>{e.preventDefault();V.dist*=Math.exp(e.deltaY*0.001);draw()};
  cv2.ondblclick=()=>{V.rx=-0.9;V.ry=0.6;V.dist=2.6;V.pan=[0,0];draw()};
  window.addEventListener('resize',draw)}
function vload(d){const gl=V.gl,P=b64f(d.pos),C=b64f(d.col),n=P.length/3,N=new Float32Array(P.length);
  let mn=[1e30,1e30,1e30],mx=[-1e30,-1e30,-1e30];for(let i=0;i<n;i++)for(let k=0;k<3;k++){const v=P[3*i+k];if(v<mn[k])mn[k]=v;if(v>mx[k])mx[k]=v}
  for(let t=0;t<n;t+=3){const a=9*t/3,ax=P[a],ay=P[a+1],az=P[a+2],ux=P[a+3]-ax,uy=P[a+4]-ay,uz=P[a+5]-az,vx=P[a+6]-ax,vy=P[a+7]-ay,vz=P[a+8]-az;
    let nx=uy*vz-uz*vy,ny=uz*vx-ux*vz,nz=ux*vy-uy*vx;const l=Math.hypot(nx,ny,nz)||1;nx/=l;ny/=l;nz/=l;for(let k=0;k<3;k++){N[a+3*k]=nx;N[a+3*k+1]=ny;N[a+3*k+2]=nz}}
  V.c=[0,1,2].map(k=>(mn[k]+mx[k])/2);V.r=Math.max(1e-9,Math.hypot(mx[0]-mn[0],mx[1]-mn[1],mx[2]-mn[2])/2);
  const mk=a=>{const b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,a,gl.STATIC_DRAW);return b};
  V.buf={p:mk(P),n:mk(N),c:mk(C)};V.n=n;draw()}
function mul(a,b){const o=new Array(16).fill(0);for(let i=0;i<4;i++)for(let j=0;j<4;j++)for(let k=0;k<4;k++)o[j*4+i]+=a[k*4+i]*b[j*4+k];return o}
function draw(){const gl=V.gl;if(!gl||!V.buf)return;const cv=$('gl'),w=cv.clientWidth,h=cv.clientHeight;if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h}
  gl.viewport(0,0,w,h);gl.clearColor(0.125,0.14,0.17,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.useProgram(V.prog);
  const s=1/V.r,cx=Math.cos(V.rx),sx=Math.sin(V.rx),cy=Math.cos(V.ry),sy=Math.sin(V.ry);
  const R=[cy,sx*sy,-cx*sy,0, 0,cx,sx,0, sy,-sx*cy,cx*cy,0, 0,0,0,1];   // column-major
  const T=[s,0,0,0,0,s,0,0,0,0,s,0,-V.c[0]*s,-V.c[1]*s,-V.c[2]*s,1];
  const Vw=[1,0,0,0,0,1,0,0,0,0,1,0,V.pan[0],V.pan[1],-V.dist,1];
  const f=1/Math.tan(0.45),a=w/h,near=0.01,far=100;const Pm=[f/a,0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0];
  const M=mul(Pm,mul(Vw,mul(R,T)));
  gl.uniformMatrix4fv(gl.getUniformLocation(V.prog,'m'),false,new Float32Array(M));
  gl.uniformMatrix3fv(gl.getUniformLocation(V.prog,'r'),false,new Float32Array([R[0],R[1],R[2],R[4],R[5],R[6],R[8],R[9],R[10]]));
  for(const [nm,b] of [['p',V.buf.p],['n',V.buf.n],['c',V.buf.c]]){const l=gl.getAttribLocation(V.prog,nm);gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.enableVertexAttribArray(l);gl.vertexAttribPointer(l,3,gl.FLOAT,false,0,0)}
  gl.drawArrays(gl.TRIANGLES,0,V.n)}
async function vshow(which){$('v-res').classList.toggle('on',which==='result');$('v-mesh').classList.toggle('on',which==='mesh');
  const key=V.job+which;$('v-info').textContent='Loading…';
  try{if(!V.cache[key]){const r=await fetch('/api/preview3d/'+V.job+'?which='+which,{headers:{'X-Token':TOKEN}});const j=await r.json();if(!r.ok)throw new Error(j.error);V.cache[key]=j}
    const d=V.cache[key];vload(d);const c=d.counts;
    $('v-info').textContent=which==='result'&&c?`${c.plane} flat · ${c.round} round · ${c.nurbs} NURBS · ${c.facet} facet triangles`:(which==='mesh'?'original mesh':'')}
  catch(e){$('v-info').textContent='Preview failed: '+e.message}}
function view3d(jid,noOrig){V.job=jid;$('v-res').style.display='';$('v-mesh').style.display=noOrig?'none':'';$('viewer').style.display='flex';vinit();setTimeout(()=>vshow('result'),30);diagLoad(jid)}
// ---- diagnostic: the job's settings and result as a QR code and in words, for one screenshot
let DIAG='';
function diagToggle(){const a=$('v-diag'),on=a.style.display==='none';a.style.display=on?'':'none';$('v-dbtn').classList.toggle('on',on);draw()}
function diagQR(rows){const cv=$('v-qr');if(!rows){cv.style.display='none';$('v-qrn').textContent='(No QR picture: the "segno" package is missing. Start the app with its start file once more, online, to add it. The code below says the same.)';return}
  cv.style.display='';$('v-qrn').textContent='';const n=rows.length,q=4,room=$('v-diag').clientHeight>=640?216:176,m=Math.max(3,Math.floor(room/(n+2*q))),s=(n+2*q)*m;cv.width=s;cv.height=s;cv.style.width=s+'px';cv.style.height=s+'px';
  const c=cv.getContext('2d');c.fillStyle='#fff';c.fillRect(0,0,s,s);c.fillStyle='#000';for(let y=0;y<n;y++)for(let x=0;x<n;x++)if(rows[y][x]==='1')c.fillRect((x+q)*m,(y+q)*m,m,m)}
async function diagLoad(jid){$('v-rows').innerHTML='';$('v-code').textContent='';DIAG='';
  try{const d=await api('/api/diag/'+jid);if(V.job!==jid)return;DIAG=d.code;diagQR(d.qr);
    let h='<p><b>File</b> '+esc(d.name||'?')+' <span class="hint">(not in the code)</span>';
    for(const [k,v] of d.rows)h+=(k?'</p><p><b'+(/FAILED|Error/.test(k)?' class="bad"':'')+'>'+esc(k)+'</b> ':'<br>')+esc(v);
    h+='</p>';
    if(!d.has_settings)h+='<p class="hint">This tab\'s own settings are not carried in the code yet: STL → STEP conversions only.</p>';
    if(d.error)h+='<p><b class="bad">Error text</b> '+esc(d.error)+' <span class="hint">(not in the code)</span></p>';
    $('v-rows').innerHTML=h;$('v-code').textContent=d.code}
  catch(e){$('v-rows').innerHTML='<p class="bad">Diagnostic not available: '+esc(e.message)+'</p>';diagQR(null);$('v-qrn').textContent=''}}
function diagCopy(){if(!DIAG)return;const done=()=>{$('v-copied').textContent='copied'};$('v-copied').textContent='';
  if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(DIAG).then(done,()=>{$('v-copied').textContent='select the code and copy it'});
  else{const r=document.createRange();r.selectNodeContents($('v-code'));const s=getSelection();s.removeAllRanges();s.addRange(r);$('v-copied').textContent='selected: press Ctrl+C'}}
function viewDiag(jid){V.job=jid;$('viewer').style.display='flex';$('v-res').style.display='none';$('v-mesh').style.display='none';$('v-info').textContent='This job failed: there is no 3D preview, only its diagnostic.';
  $('v-diag').style.display='';$('v-dbtn').classList.add('on');const g=V.gl;if(g){V.buf=null;g.clearColor(0.125,0.14,0.17,1);g.clear(g.COLOR_BUFFER_BIT)}diagLoad(jid)}
function discOk(){try{localStorage.setItem('tostep-agreed','1')}catch(e){}$('disc').style.display='none'}
(function(){let ok=false;try{ok=localStorage.getItem('tostep-agreed')==='1'}catch(e){}if(!ok)$('disc').style.display='flex'})();
async function quitApp(){try{await api('/api/quit',{})}catch(e){}document.body.innerHTML='<main><div class="card"><h2>Twoo STEP CAD Toolbox has stopped</h2><p>You can close this tab. Start the app again from its folder.</p></div></main>'}
