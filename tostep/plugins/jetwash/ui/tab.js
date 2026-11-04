hookDrop('wash','.step,.stp,.stl,.obj,.ply,.3mf');
const CD={1:'<b>Gentle</b> — only near-zero junk (below threshold/10), loose specks, split faces',2:'<b>Normal</b> — slivers, micro-steps, paper-thin fins/slots, tiny faces, near-touching bodies',3:'<b>Aggressive</b> — also holes, bosses, fillets and chamfers smaller than the threshold'};
const cs=$('coarse');cs.oninput=()=>$('coarse-d').innerHTML=CD[cs.value];cs.oninput();
$('thr-s').oninput=()=>{$('thr').value=(+Math.pow(10,+$('thr-s').value).toPrecision(3))};
$('thr').onchange=()=>{const v=+$('thr').value;if(v>0)$('thr-s').value=Math.log10(v)};
async function useLast(){const j=await api('/api/last');if(!j.job){$('f-wash').textContent='Nothing converted yet in this session';return}
  sel.wash='job:'+j.job;$('f-wash').textContent=j.name+' (last converted)';$('b-wash').disabled=false}
function runWash(){start('wash','wash',Object.assign(convParams(),{threshold:$('thr').value,coarseness:$('coarse').value}))}
Object.assign(PQ,{
 thr:v=>[.5,cl(1-(Math.log10(+v||.05)+4)/5)],
 coarse:v=>[[.3,.5,.8][v-1]||.5,[.95,.8,.5][v-1]||.8],
});
