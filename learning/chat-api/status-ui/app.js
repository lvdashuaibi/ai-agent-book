const $=id=>document.getElementById(id);
const labels={phone_history:'三段拨号结果',search_distractors:'四段搜索结果',status_bar:'状态栏',latest_user_query:'最后的用户追问'};
const decisions={REFUSAL:'明确拒绝第四次拨号',VIOLATION:'请求第四次拨号（违反约束）',INCOMPLETE:'未完整结束 · 不能算成功拒绝',OTHER:'完整结束 · 需人工判读'};
let current=null, busy=false;
function node(tag,text,cls){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;}
async function get(path){const response=await fetch(path);if(!response.ok)throw new Error(`HTTP ${response.status}`);return response.json();}
function percent(value,digits=2){return value==null?'未注入':(value*100).toFixed(digits)+'%';}
function metric(label,value){const item=node('div','','metric');item.append(node('span',label),node('strong',String(value)));return item;}
function setBusy(value){busy=value;for(const id of ['run','history','trials','max-tokens'])$(id).disabled=value;}
function showTrial(arm,record,index=0){
  const box=$(`reply-${arm}`);box.replaceChildren();
  const kind=record.behavior.classification;
  box.append(node('div',decisions[kind],`verdict ${kind==='VIOLATION'?'bad':kind==='INCOMPLETE'||kind==='OTHER'?'unknown':''}`));
  if(record.behavior.tool_calls.length){
    for(const call of record.behavior.tool_calls)box.append(node('div',`工具请求：${call.name}\n参数：${JSON.stringify(call.arguments)}\n本实验仅观测，不执行工具。`,'tool-request'));
  }else box.append(node('div',record.final_text||'模型尚未给出最终回答，请展开下方思考文本。'));
  $(`thinking-${arm}`).textContent=record.thinking_text;
  $(`thinking-${arm}`).parentElement.open=!record.final_text;
  $(`raw-${arm}`).textContent=JSON.stringify({seed:record.seed,finished:record.finished,behavior:record.behavior,raw_text:record.raw_text},null,2);
  const metrics=$(`metrics-${arm}`);metrics.replaceChildren(metric('输入 token',record.context_tokens),metric('思考 token',record.thinking_tokens??'旧记录未采集'),metric('生成 token',record.generated_tokens),metric('生成耗时',record.duration_s+'s'));
  for(const button of $(`trials-${arm}`).children)button.classList.toggle('selected',Number(button.dataset.index)===index);
}
function showArms(result){
  for(const arm of ['A','B']){
    const trials=result.arms[arm].trials, picker=$(`trials-${arm}`);picker.replaceChildren();
    trials.forEach((record,index)=>{const button=node('button',`试验 ${index+1} · seed ${record.seed}`);button.dataset.index=index;button.onclick=()=>showTrial(arm,record,index);picker.append(button);});
    showTrial(arm,trials[0]);
  }
}
function renderRegions(result){
  const body=$('regions');body.replaceChildren();
  for(const [name,label] of Object.entries(labels)){
    const row=node('tr','');row.append(node('td',label));
    for(const arm of ['A','B']){const value=result.arms[arm].attention.metrics[name];row.append(node('td',`${percent(value.mass)} / ${value.token_count}`));}
    for(const arm of ['A','B'])row.append(node('td',percent(result.arms[arm].attention.metrics[name].per_token_mass,4)));
    body.append(row);
  }
}
function color(weight){
  const t=Math.max(0,Math.min(1,(Math.log10(Math.max(weight,1e-7))+7)/7));
  const palette=[[6,6,35],[70,20,90],[150,45,100],[225,100,65],[250,230,165]];
  const position=t*4,i=Math.min(3,Math.floor(position)),fraction=position-i;
  return palette[i].map((value,k)=>Math.round(value+(palette[i+1][k]-value)*fraction));
}
function draw(arm,attention){
  const matrix=attention.matrix, rows=matrix.length,cols=matrix[0].length;
  const canvas=$(`heat-${arm}`);canvas.width=cols;canvas.height=rows;
  const context=canvas.getContext('2d'), pixels=context.createImageData(cols,rows);
  for(let y=0;y<rows;y++)for(let x=0;x<cols;x++){
    const rgb=color(matrix[y][x]), offset=(y*cols+x)*4;
    pixels.data[offset]=rgb[0];pixels.data[offset+1]=rgb[1];pixels.data[offset+2]=rgb[2];pixels.data[offset+3]=255;
  }
  context.putImageData(pixels,0,0);
  for(const [region,stroke] of [['phone_history','#56dce1'],['status_bar','#7bea7b']]){
    const indices=attention.regions[region];context.fillStyle=stroke;
    indices.forEach((index,k)=>{if(k===0||index!==indices[k-1]+1)context.fillRect(index,0,1,rows);});
  }
  const inspect=event=>{
    const rect=canvas.getBoundingClientRect();
    const x=Math.max(0,Math.min(cols-1,Math.floor((event.clientX-rect.left)/rect.width*cols)));
    const y=Math.max(0,Math.min(rows-1,Math.floor((event.clientY-rect.top)/rect.height*rows)));
    const region=Object.keys(labels).find(key=>attention.regions[key].includes(x));
    $(`hover-${arm}`).textContent=`输出 #${y+1} ${JSON.stringify(attention.output_tokens[y])} → 输入 #${x} ${JSON.stringify(attention.input_tokens[x])} · ${region?labels[region]:'其他上下文'} · 权重 ${matrix[y][x].toExponential(4)}`;
  };
  canvas.onmousemove=inspect;canvas.onclick=inspect;
  $(`hover-${arm}`).textContent=`${rows} 个输出预测位置 × ${cols} 个输入位置 · 第 ${attention.layer} 层 · ${attention.heads} 头平均`;
}
function conclusion(result){
  const summaries=['A','B'].map(arm=>{
    const trials=result.arms[arm].trials;
    const count=label=>trials.filter(trial=>trial.behavior.classification===label).length;
    return `${arm} 组 ${trials.length} 次：明确拒绝 ${count('REFUSAL')}，请求第四次拨号 ${count('VIOLATION')}，未完整结束 ${count('INCOMPLETE')}，其他 ${count('OTHER')}。`;
  });
  $('conclusion').textContent=summaries.join('\n')+'\n这些是本机、当前模型版本和固定轨迹下的结果，不能直接套用书中的提升比例。';
  $('conclusion').style.whiteSpace='pre-wrap';
}
function showResult(result){
  current=result;showArms(result);renderRegions(result);conclusion(result);
  for(const arm of ['A','B'])draw(arm,result.arms[arm].attention);
  for(const [id,type] of [['png','heatmap'],['npz','weights']]){$(id).hidden=false;$(id).href=`/api/${type}/${result.id}`;$(id).target='_blank';}
  $('status').textContent=`本机实测 · ${new Date(result.created_at).toLocaleString('zh-CN')} · ${result.model} · CPU · ${result.arms.A.trials.length} 对试验 · 注意力来自第 1 对`;
}
async function loadHistory(id){showResult(await get(`/api/run/${id}`));$('history').value=id;}
async function refreshHistory(selected){
  const runs=await get('/api/runs');$('history').replaceChildren();
  if(!runs.length){$('history').append(node('option','暂无本机结果'));return;}
  for(const run of runs){const option=node('option',`${new Date(run.created_at).toLocaleString('zh-CN')} · 上限 ${run.max_tokens}`);option.value=run.id;$('history').append(option);}
  if(selected)$('history').value=selected;
  return runs;
}
function renderTrajectory(arms){
  const history=$('trajectory');history.replaceChildren();
  const messages=arms.A;
  messages.forEach(message=>{
    const card=node('div','','history-item');
    const label={system:'系统约束',user:'用户任务',assistant:'Agent 工具请求',tool:'模拟工具返回'}[message.role];
    card.append(node('b',label+'\n'));
    if(message.content)card.append(document.createTextNode(message.content));
    for(const call of message.tool_calls||[])card.append(document.createTextNode(`${call.function.name} ${JSON.stringify(call.function.arguments)}`));
    history.append(card);
  });
  $('status-message').textContent=arms.B.at(-1).content;
}
async function start(){
  if(busy)return;setBusy(true);current=null;
  $('regions').replaceChildren();$('conclusion').textContent='正在进行配对试验，尚不能判定结果。';$('png').hidden=true;$('npz').hidden=true;
  for(const arm of ['A','B']){
    $(`reply-${arm}`).textContent='等待生成…';$(`metrics-${arm}`).replaceChildren();$(`trials-${arm}`).replaceChildren();$(`thinking-${arm}`).textContent='';$(`raw-${arm}`).textContent='';
    const canvas=$(`heat-${arm}`);canvas.width=1;canvas.height=1;$(`hover-${arm}`).textContent='等待本次真实权重';
  }
  let completed=false;
  try{
    const response=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trials:Number($('trials').value),max_tokens:Number($('max-tokens').value)})});
    if(!response.ok)throw new Error((await response.json()).error||`HTTP ${response.status}`);
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
    const consume=line=>{
      if(!line.trim())return;const event=JSON.parse(line);
      if(event.type==='error')throw new Error(event.message);
      if(event.type==='progress')$('status').textContent=event.message;
      if(event.type==='phase'){$('status').textContent=`${event.arm} 组 · 试验 ${event.trial} · seed ${event.seed} · 本地生成中`;$(`reply-${event.arm}`).textContent='正在生成，请稍候…';}
      if(event.type==='tokens'){$('status').textContent=`${event.arm} 组已生成 ${event.count} token`;$(`thinking-${event.arm}`).textContent=event.text;}
      if(event.type==='trial')showTrial(event.arm,event.record,event.trial-1);
      if(event.type==='result'){completed=true;showResult(event.result);}
    };
    while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value,{stream:!done});const lines=buffer.split('\n');buffer=lines.pop();lines.forEach(consume);if(done)break;}
    if(buffer.trim())consume(buffer);
    if(!completed)throw new Error('连接中断；可刷新历史检查是否已保存结果。');
    await refreshHistory(current.id);
  }catch(error){$('status').textContent=error.message;}
  finally{setBusy(false);}
}
$('run').onclick=start;$('history').onchange=()=>loadHistory($('history').value).catch(error=>$('status').textContent=error.message);
(async()=>{const catalog=await get('/api/catalog');renderTrajectory(catalog.arms);const runs=await refreshHistory();if(runs?.length)await loadHistory(runs[0].id);else $('status').textContent=catalog.model_ready?'模型已下载，可开始本地试验。':'请先运行 status_setup.py 下载本地模型。';})().catch(error=>$('status').textContent=error.message);
