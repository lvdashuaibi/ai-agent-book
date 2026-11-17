// 仅负责把观测事件显示为对话，不更改消息、授权或实验结果。
const FIELD_LABELS = {url:'页面地址',path:'文件路径',content:'正文',to:'收件人',
  source:'资料来源',trusted:'可信来源',ok:'操作成功',simulated:'模拟执行',error:'原因',
  memory:'保存的记忆',files:'模拟文件',outbox:'模拟发件箱',memory_poisoned:'检测到记忆污染',secret_leaked:'检测到秘密泄露'};
const TOOL_LABELS = {read_webpage:'读取网页',load_memory:'读取记忆',save_memory:'保存记忆',write_file:'保存文件',send_email:'发送邮件'};
function parseData(value) { if(typeof value!=='string')return value; try{return JSON.parse(value);}catch{return value;} }
function readableText(text) {
  const container=node('div','','readable-text');
  for(const part of String(text ?? '').split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g)) {
    if(part.startsWith('**')&&part.endsWith('**'))container.append(node('strong',part.slice(2,-2)));
    else if(part.startsWith('`')&&part.endsWith('`'))container.append(node('code',part.slice(1,-1)));
    else container.append(document.createTextNode(part));
  }
  return container;
}
function renderData(value) {
  const container=node('div','','readable-data');
  if(value==null)container.append(node('span','未提供','subtle'));
  else if(typeof value==='string')container.append(readableText(value));
  else if(typeof value==='boolean')container.append(node('span',value?'是':'否'));
  else if(Array.isArray(value)) {
    if(!value.length)container.append(node('span','暂无内容','subtle'));
    value.forEach((item,index)=>{const row=node('div','','data-item'); row.append(node('span',String(index+1),'item-number'),renderData(item)); container.append(row);});
  } else if(typeof value==='object') {
    for(const [key,child] of Object.entries(value)) {
      const row=node('div','','data-field');row.append(node('div',FIELD_LABELS[key]||key,'field-label'),renderData(child));container.append(row);
    }
  } else container.append(node('span',String(value)));
  return container;
}
function textButton(label,action) {const button=node('button',label,'text-action');button.onclick=action;return button;}
function messageCard(role,content,caption='') {
  const card=node('article','',`chat-message ${role}`);
  const label={assistant:'Agent',user:'用户任务',system:'系统规则',tool:'工具结果'}[role]||role;
  const heading=node('div','','message-heading');heading.append(node('span',role==='assistant'?'↻':role==='user'?'你':'·','avatar'),node('strong',label));
  if(caption)heading.append(node('span',caption,'message-caption'));
  card.append(heading,readableText(content));return card;
}
function toolCard(record,onInspect) {
  const card=node('details','','inline-tool');
  updateToolCard(card,record,onInspect);
  return card;
}
function updateToolCard(card,record,onInspect) {
  const open=card.open;
  const args=parseData(record.arguments);
  const target=args && typeof args==='object' ? args.url||args.path||args.to||'' : '';
  const status=record.blocked?'已拦截':record.error?'执行错误':record.executed?'已完成':record.ended?'未执行':'等待执行';
  card.classList.toggle('blocked',Boolean(record.blocked));
  card.classList.toggle('danger',Boolean(record.unauthorized&&record.executed));
  const summary=node('summary','');
  summary.append(node('span',record.blocked?'⊘':'⌘','tool-symbol'),node('span',TOOL_LABELS[record.name]||record.name,'tool-name'),node('span',target,'tool-target'),node('span',status,'tool-status'));
  const body=node('div','','tool-body');
  body.append(node('div',record.name,'function-label'));
  if(record.unauthorized)body.append(node('p',record.blocked?'用户未授权 → 执行层已阻止，操作未发生。':record.executed?'用户未授权 → 操作仍被执行，产生了实际效果。':'用户未授权 → 请求未执行。','permission-note'));
  body.append(node('h3','调用参数'),renderData(args));
  if(record.result!==undefined){body.append(node('h3','工具返回'),renderData(record.result));}
  else body.append(node('p',record.ended?'本次流程已结束，没有执行这项请求。':'等待执行器返回结果…','subtle'));
  body.append(textButton('查看工具详情',()=>onInspect(record)));
  card.replaceChildren(summary,body);card.open=open;
}
function showReadableDetail(event) {
  const container=$('readable-detail');container.replaceChildren();
  if(event.type==='context') {
    for(const message of event.messages) {
      const card=node('details','','context-message');
      card.open=message.role==='user'||message.role==='tool';
      card.append(node('summary',{system:'系统规则',user:'用户输入',assistant:'Agent 历史回复',tool:'工具返回'}[message.role]||message.role));
      if(message.content!=null)card.append(renderData(message.role==='tool'?parseData(message.content):message.content));
      for(const call of message.tool_calls||[]) {
        card.append(node('h3',`请求工具 · ${call.function.name}`),renderData(parseData(call.function.arguments)));
      }
      if(message.tool_call_id)card.append(node('small',`对应调用：${message.tool_call_id}`,'subtle'));
      container.append(card);
    }
    const tools=node('details','','context-message');tools.append(node('summary',`可用工具 · ${(event.tools||[]).length} 个`));
    for(const item of event.tools||[])tools.append(node('h3',item.function.name),readableText(item.function.description));
    container.append(tools);
  } else if(event.type==='model') {
    container.append(messageCard('assistant',event.message.content||'本次返回没有文本，模型请求使用工具。'));
    for(const call of event.message.tool_calls||[])container.append(node('h3',`请求工具 · ${call.function.name}`),renderData(parseData(call.function.arguments)));
    container.append(node('p',`结束原因：${event.finish_reason} · 输入 ${event.usage?.prompt_tokens??'未知'} / 输出 ${event.usage?.completion_tokens??'未知'} token`,'note'));
  } else if(event.type==='tool') {
    const card=toolCard(event,()=>{});card.open=true;container.append(card);
  } else if(event.type==='start') {
    container.append(node('h3',catalog.attacks[event.attack]),readableText(catalog.defenses[event.defense]));
  } else container.append(readableText(event.message||'查看原始数据了解详情。'));
}
function setWorld(value) { $('world').textContent=json(value); $('world-view').replaceChildren(renderData(value)); }
function openDetail(open) {
  $('workspace').classList.toggle('inspector-hidden',!open);
  $('toggle-detail').setAttribute('aria-expanded',String(open));
  $('toggle-detail').textContent=open?'收起详情':'查看详情';
}
