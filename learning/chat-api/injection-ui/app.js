const $ = id => document.getElementById(id);
let catalog, runs = [], events = [], selectedEvent = null, activeResult = null, busy = false;
let phaseName = null, pendingTools = [];
const json = value => JSON.stringify(value, null, 2);
function node(tag, text, cls) { const n = document.createElement(tag); n.textContent = text; if (cls) n.className = cls; return n; }
async function get(path) { const response = await fetch(path); if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }
function setBusy(value) { busy = value; for (const id of ['run','compare','attack','defense','repeats','history','refresh']) $(id).disabled = value; }
function describe(event) {
  if (event.type === 'start') return ['实验开始', `${catalog.attacks[event.attack]} · ${event.defense}`];
  if (event.type === 'context') return ['发送模型上下文', `${event.phase} · 第 ${event.step} 次 · ${event.messages.length} 条消息`];
  if (event.type === 'model') return ['模型返回', `${event.phase} · ${event.finish_reason} · ${event.usage?.total_tokens ?? '未知'} token`];
  if (event.type === 'tool') return [`${event.blocked ? '执行层已拦截' : event.executed ? '工具已执行' : '工具参数错误'} · ${event.name}`, `${event.phase}${event.unauthorized ? ' · 用户未授权' : ''}`];
  return ['执行异常', event.message || '请查看详情'];
}
function inspect(event, button) {
  selectedEvent = event;
  const [title, caption] = describe(event);
  $('detail-title').textContent = title;
  $('detail-caption').textContent = caption;
  $('detail').textContent = json(event);
  $('raw-detail').open = false;
  showReadableDetail(event);
  openDetail(true);
}
function addEvent(event) {
  const nearBottom = $('events').scrollHeight - $('events').scrollTop - $('events').clientHeight < 100;
  events.push(event);
  if (events.length === 1) $('events').replaceChildren();
  if(event.type==='start') $('events').append(node('div',`${catalog.attacks[event.attack]} · ${event.defense} ${catalog.defenses[event.defense]}`,'run-divider'));
  if(event.type==='context' && phaseName!==event.phase) {
    phaseName=event.phase;
    $('events').append(node('div',event.phase,'phase-divider'));
    const user=event.messages.find(message=>message.role==='user');
    if(user)$('events').append(messageCard('user',user.content));
  }
  if(event.type==='model') {
    const calls=event.message.tool_calls||[];
    const content=event.message.content || (calls.length ? `请求使用 ${calls.length} 个工具` : '本次模型没有返回文本。');
    const card=messageCard('assistant',content,`第 ${event.step} 次回复${event.finish_reason==='stop'?' · 本会话结束':''}`);
    const actions=node('div','','message-actions');
    const context=[...events].reverse().find(item=>item.type==='context'&&item.phase===event.phase&&item.step===event.step);
    if(context)actions.append(textButton('请求上下文',()=>inspect(context)));
    actions.append(textButton('回复详情',()=>inspect(event)),node('span',`${event.usage?.total_tokens??'未知'} token`,'subtle'));
    card.append(actions);$('events').append(card);
    for(const call of calls) {
      const record={type:'tool',phase:event.phase,name:call.function.name,arguments:call.function.arguments};
      const tool=toolCard(record,inspect);$('events').append(tool);pendingTools.push({record,card:tool});
    }
  }
  if(event.type==='tool') {
    const pending=pendingTools.find(item=>item.record.phase===event.phase&&item.record.name===event.name&&item.record.arguments===event.arguments);
    if(pending){updateToolCard(pending.card,event,inspect);pendingTools=pendingTools.filter(item=>item!==pending);}
    else $('events').append(toolCard(event,inspect));
    setWorld({memory:event.memory,files:event.files,outbox:event.outbox});
  }
  if(event.type==='error')$('events').append(node('div',event.message,'run-error'));
  if(nearBottom)$('events').scrollTop=$('events').scrollHeight;
}
function showScores(result) {
  activeResult = result;
  const third = result.attack === 'clean' || result.attack === 'authorized';
  const label = third ? '正常任务完成' : '攻击是否落地';
  const valid = result.attack_success !== null;
  const value = !valid ? '未完成' : (third ? result.normal_success : result.attack_success) ? '是' : '否';
  $('scores').replaceChildren();
  for (const [name, score] of [['未授权请求', result.attempts], ['执行层拦截', result.blocked], [label,value]]) {
    const block = node('div',''); block.append(node('span',name),node('strong',String(score))); $('scores').append(block);
  }
  setWorld({memory:result.memory,memory_poisoned:result.memory_poisoned,files:result.files,outbox:result.outbox,secret_leaked:result.secret_leaked});
  for(const pending of pendingTools)updateToolCard(pending.card,{...pending.record,ended:true},inspect);
  pendingTools=[];
  const summary=node('div','','run-summary');
  const explanation=!valid?'本次流程未完整结束，不能判定防御成功。':third?(result.normal_success?'正常任务已完成。':'正常任务未通过检查，请核对上方回复。'):result.attack_success?'已观察到攻击产生实际效果。':result.attempts?`模型发起了 ${result.attempts} 次未授权请求；请展开工具卡片核对执行情况。`:'模型没有发起未授权操作，本次无需执行层拦截。';
  summary.append(node('p',explanation),node('small',`输入 ${result.input_tokens??'未知'} / 输出 ${result.output_tokens??'未知'} token · ${result.duration_s}s`));
  $('events').append(summary);
}
function resetConversation(){events=[];selectedEvent=null;activeResult=null;phaseName=null;pendingTools=[];$('events').replaceChildren();$('detail').textContent='尚未选择事件';$('readable-detail').replaceChildren(node('p','点击“请求上下文”或工具“详情”查看。','note'));openDetail(false);setWorld({memory:[],files:{},outbox:[]});}
function showRun(result) { resetConversation(); for (const event of result.events) addEvent(event); showScores(result); $('history').value=result.id; $('events').scrollTop=0; }
function renderMatrix() {
  $('matrix').replaceChildren();
  for (const [attack,label] of Object.entries(catalog.attacks)) {
    const row = node('tr',''); row.append(node('td',label));
    for (const defense of Object.keys(catalog.defenses)) {
      const cells = runs.filter(run => run.attack === attack && run.defense === defense && run.model === catalog.model);
      const valid = cells.filter(run => run.attack_success !== null);
      const success = valid.filter(run => ['clean','authorized'].includes(attack) ? run.normal_success : run.attack_success).length;
      const cell = node('td',valid.length ? `${success}/${valid.length} · ${(success/valid.length*100).toFixed(0)}%` : '—');
      if (cells.length > valid.length) cell.append(node('small',`${cells.length-valid.length} 次未完成`));
      row.append(cell);
    }
    $('matrix').append(row);
  }
  $('history').replaceChildren(node('option','选择历史实验'));
  for (const result of runs) { const option = node('option',`${result.defense} · ${catalog.attacks[result.attack]} · ${new Date(result.created_at).toLocaleTimeString('zh-CN')}`); option.value=result.id; $('history').append(option); }
  if (activeResult) $('history').value = activeResult.id;
}
async function runOne(attack, defense) {
  resetConversation();
  for (const strong of $('scores').querySelectorAll('strong')) strong.textContent='—';
  const response = await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:json({attack,defense})});
  if (!response.ok) throw new Error((await response.json()).error || `HTTP ${response.status}`);
  const reader=response.body.getReader(), decoder=new TextDecoder(); let buffer='', result;
  function consume(line) { if (!line.trim()) return; const event=JSON.parse(line); if(event.type==='result'){result=event.result; showScores(result);} else addEvent(event); }
  while(true) { const {value,done}=await reader.read(); buffer+=decoder.decode(value,{stream:!done}); const lines=buffer.split('\n'); buffer=lines.pop(); lines.forEach(consume); if(done)break; }
  if(buffer.trim())consume(buffer);
  if(!result) throw new Error('连接中断，请刷新记录查看服务端保存的结果。');
  runs=[result,...runs.filter(run=>run.id!==result.id)].slice(0,40); renderMatrix();
  return result;
}
async function start(compare) {
  if(busy)return; setBusy(true);
  const attack=$('attack').value, defenses=compare?Object.keys(catalog.defenses):[$('defense').value], repeats=Number($('repeats').value);
  let done=0, unfinished=0;
  try { for(let repeat=0;repeat<repeats;repeat++) for(const defense of defenses) {
    $('status').textContent=`运行 ${++done}/${defenses.length*repeats} · ${defense} · 重复 ${repeat+1}`;
    const result=await runOne(attack,defense); if(result.attack_success===null)unfinished++;
  } $('status').textContent=`已完成 ${done} 次实验${unfinished?`，其中 ${unfinished} 次异常或未完成`:''} · 记录已保存`; }
  catch(error){$('status').textContent=error.message;}
  finally{setBusy(false);}
}
async function init(){
  [catalog,runs]=await Promise.all([get('/api/catalog'),get('/api/runs')]);
  $('model').textContent=catalog.model;
  for(const [id,label] of Object.entries(catalog.attacks)){const option=node('option',label); option.value=id; $('attack').append(option);}
  for(const [id,label] of Object.entries(catalog.defenses)){const option=node('option',`${id} · ${label}`); option.value=id; $('defense').append(option);}
  $('attack').value='indirect'; renderMatrix(); if(runs.length)showRun(runs[0]);
}
$('run').onclick=()=>start(false); $('compare').onclick=()=>start(true);
$('history').onchange=()=>{const result=runs.find(run=>run.id===$('history').value); if(result)showRun(result);};
$('refresh').onclick=async()=>{try{runs=await get('/api/runs');renderMatrix();if(runs.length)showRun(runs[0]);}catch(error){$('status').textContent=error.message;}};
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText(json(selectedEvent));$('status').textContent='已复制当前详情';}catch{$('status').textContent='复制失败，可直接选中详情文本复制';}};
$('toggle-detail').onclick=()=>openDetail($('workspace').classList.contains('inspector-hidden'));
$('close-detail').onclick=()=>openDetail(false);
init().catch(error=>{$('status').textContent=error.message;});
