const $ = (id) => document.getElementById(id);
let turns = [];
let traces = [];
let currentMessages = [];
let selectedId = null;
let selectedTab = "context";
let rawContext = false;
let pending = null;
let busy = false;

const token = (value) => value == null ? "未知" : value.toLocaleString("zh-CN");
const percentage = (value) => value == null ? "未知" : value.toLocaleString("zh-CN", { style: "percent", maximumFractionDigits: 1 });
const duration = (milliseconds) => `${(milliseconds / 1000).toFixed(2)}s`;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function request(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

function resizeInput() {
  $("prompt").style.height = "auto";
  $("prompt").style.height = `${Math.min(170, $("prompt").scrollHeight)}px`;
  $("send").disabled = busy || !$("prompt").value.trim();
}

function setBusy(value) {
  busy = value;
  $("reset").disabled = value;
  $("refresh").disabled = value;
  $("send").disabled = value || !$("prompt").value.trim();
  $("send").querySelector("span").textContent = value ? "等待回复" : "发送";
  $("input-hint").textContent = value ? "可以先写下一个问题" : "Enter 发送 · Shift + Enter 换行";
}

function openInspector(open) {
  $("layout").classList.toggle("inspector-hidden", !open);
  $("toggle-inspector").setAttribute("aria-expanded", String(open));
}

function showTab(tab, focus = false) {
  selectedTab = tab;
  for (const name of ["context", "trace"]) {
    const selected = name === tab;
    $(`${name}-view`).hidden = !selected;
    $(`${name}-tab`).classList.toggle("active", selected);
    $(`${name}-tab`).setAttribute("aria-selected", String(selected));
    $(`${name}-tab`).tabIndex = selected ? 0 : -1;
  }
  if (focus) $(`${tab}-tab`).focus();
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    const original = button.textContent;
    button.textContent = "已复制";
    setTimeout(() => { button.textContent = original; }, 1400);
  } catch {
    $("notice").textContent = "复制失败，请选中文字后手动复制。";
  }
}

function appendInline(parent, text) {
  for (const part of text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g)) {
    if (part.startsWith("`") && part.endsWith("`")) {
      parent.append(element("code", "", part.slice(1, -1)));
    } else if (part.startsWith("**") && part.endsWith("**")) {
      parent.append(element("strong", "", part.slice(2, -2)));
    } else {
      parent.append(document.createTextNode(part));
    }
  }
}

function renderProse(container, text) {
  container.replaceChildren();
  const codePattern = /```([^\n]*)\n([\s\S]*?)(?:```|$)/g;
  let position = 0;
  function addText(value) {
    for (const paragraph of value.split(/\n\s*\n/)) {
      if (!paragraph.trim()) continue;
      const heading = paragraph.match(/^(#{1,4})\s+([^\n]+)$/);
      const block = element(heading ? "h3" : "p");
      appendInline(block, heading ? heading[2] : paragraph);
      container.append(block);
    }
  }
  for (const match of text.matchAll(codePattern)) {
    addText(text.slice(position, match.index));
    const box = element("div", "code-box");
    const toolbar = element("div", "code-toolbar");
    const button = element("button", "text-button", "复制代码");
    button.onclick = () => copyText(match[2], button);
    toolbar.append(element("span", "", match[1].trim() || "code"), button);
    const pre = element("pre");
    pre.append(element("code", "", match[2].replace(/\n$/, "")));
    box.append(toolbar, pre);
    container.append(box);
    position = match.index + match[0].length;
  }
  addText(text.slice(position));
}

function renderContext(messages, caption) {
  currentMessages = messages;
  $("context-caption").textContent = caption;
  $("context-count").textContent = messages.length;
  $("context-json").textContent = JSON.stringify(messages, null, 2);
  $("context-empty").hidden = messages.length > 0;
  $("context-cards").hidden = rawContext;
  $("context-json").hidden = !rawContext || !messages.length;
  $("copy-context").disabled = messages.length === 0;
  $("context-cards").replaceChildren();
  messages.forEach((message, index) => {
    const card = element("details", "context-card");
    card.open = message.role === "system" || index === messages.length - 1;
    const summary = element("summary");
    let content = typeof message.content === "string" ? message.content : JSON.stringify(message.content, null, 2);
    if (message.tool_calls?.length) content = JSON.stringify({ content: message.content, tool_calls: message.tool_calls }, null, 2);
    if (message.role === "tool") content = `tool_call_id: ${message.tool_call_id}\n\n${prettyJson(message.content)}`;
    summary.append(element("span", `role ${message.role}`, message.role), element("span", "context-preview", content));
    card.append(summary, element("pre", "", content));
    $("context-cards").append(card);
  });
}

function renderTrace(trace) {
  $("trace-empty").hidden = Boolean(trace);
  $("trace-detail").hidden = !trace;
  if (!trace) return;
  $("trace-status").textContent = trace.error ? "调用失败" : "调用完成";
  $("trace-status").classList.toggle("error", Boolean(trace.error));
  $("trace-time").textContent = new Date(trace.started_at).toLocaleTimeString("zh-CN");
  $("input-tokens").textContent = token(trace.input_tokens);
  $("output-tokens").textContent = token(trace.output_tokens);
  $("total-tokens").textContent = token(trace.total_tokens);
  $("latency").textContent = duration(trace.duration_ms);
  $("cache-hit-tokens").textContent = token(trace.cache_hit_tokens);
  $("cache-miss-tokens").textContent = token(trace.cache_miss_tokens);
  $("cache-hit-rate").textContent = percentage(trace.cache_hit_rate);
  $("trace-model").textContent = trace.model || "未知";
  const stopReasons = {
    final_answer: "最终回复（finish_reason=stop）",
    length: "输出截断（length）",
    tool_calls: "请求执行工具",
    function_call: "请求函数，当前未实现执行",
    content_filter: "服务端内容策略终止",
    timeout: "请求超时",
    connection_error: "连接错误",
    api_error: "API 错误",
    invalid_response_or_config: "配置或响应无效",
    invalid_response: "模型未返回有效文本",
    internal_error: "执行异常",
    invalid_tool_calls: "工具调用结构无效",
    max_steps: "达到模型调用步数上限",
  };
  $("stop-reason").textContent = stopReasons[trace.stop_reason] || trace.stop_reason || "旧记录未采集";
  $("selected-trace").textContent = trace.trace_id;
  renderProse($("trace-reply"), trace.error || trace.reply);
  $("trace-json").textContent = JSON.stringify(trace.spans, null, 2);
  renderCalls(trace);
}

function prettyJson(value) {
  try { return JSON.stringify(JSON.parse(value), null, 2); }
  catch { return value || ""; }
}

function renderCalls(trace) {
  const calls = trace.model_calls || [];
  $("model-calls").replaceChildren();
  if (calls.length) {
    $("model-calls").append(element("h3", "", `${calls.length} 次模型调用 · token 明细`));
    const table = element("table", "call-breakdown");
    const head = element("thead");
    const header = element("tr");
    for (const title of ["调用", "输入", "输出", "总计"]) header.append(element("th", "", title));
    head.append(header);
    const body = element("tbody");
    for (const call of calls) {
      const row = element("tr", "call-token-row");
      for (const value of [`第 ${call.step} 次`, token(call.input_tokens), token(call.output_tokens), token(call.total_tokens)]) row.append(element("td", "", value));
      const cacheRow = element("tr", "call-cache-row");
      const cell = element("td");
      cell.colSpan = 4;
      const detail = element("div", "call-cache-detail");
      detail.append(
        element("span", "", `缓存命中 ${token(call.cache_hit_tokens)}`),
        element("span", "", `未命中 ${token(call.cache_miss_tokens)}`),
        element("span", "", `命中率 ${percentage(call.cache_hit_rate)}`),
      );
      cell.append(detail);
      cacheRow.append(cell);
      body.append(row, cacheRow);
    }
    table.append(head, body);
    $("model-calls").append(table);
  }
  $("tool-calls").replaceChildren();
  if (trace.tool_calls?.length) $("tool-calls").append(element("h3", "", `${trace.tool_calls.length} 次工具执行`));
  for (const call of trace.tool_calls || []) {
    const card = element("details", "tool-call-card");
    card.append(element("summary", "", `${call.name} · ${call.status === "ERROR" ? "失败" : "完成"}`));
    card.append(element("pre", "", `参数\n${prettyJson(call.arguments)}\n\n结果\n${prettyJson(call.result)}`));
    $("tool-calls").append(card);
  }
}

function contextFromTrace(trace) {
  const llm = trace?.spans.filter((span) => span.attributes?.["openinference.span.kind"] === "LLM").at(-1);
  try {
    return JSON.parse(llm?.attributes?.["input.value"] || "{}").messages || [];
  } catch {
    return [];
  }
}

function selectTurn(id, tab, reveal = true) {
  selectedId = id;
  const turn = turns.find((item) => item.trace_id === id);
  const trace = traces.find((item) => item.trace_id === id);
  const index = turns.indexOf(turn);
  const steps = turn?.steps?.length ? turn.steps : trace?.model_calls?.length ? trace.model_calls : [{ messages: turn?.messages || contextFromTrace(trace) }];
  renderSteps(steps, index);
  renderTrace(trace);
  $("turn-picker").value = id || "";
  if (tab) showTab(tab);
  if (reveal) openInspector(true);
  for (const button of document.querySelectorAll(".turn-action")) {
    button.classList.toggle("selected", button.dataset.traceId === id && button.dataset.tab === selectedTab);
  }
}

function renderSteps(steps, turnIndex) {
  const picker = $("step-picker");
  picker.replaceChildren();
  picker.hidden = steps.length < 2;
  steps.forEach((step, index) => {
    const suffix = step.finish_reason ? ` · ${step.finish_reason}` : "";
    const option = element("option", "", `第 ${index + 1} 次模型调用${suffix}`);
    option.value = index;
    picker.append(option);
  });
  picker.value = steps.length - 1;
  const showStep = () => {
    const index = Number(picker.value);
    const caption = turnIndex >= 0 ? `第 ${turnIndex + 1} 轮` : "历史调用";
    renderContext(steps[index].messages, `${caption} · 第 ${index + 1} 次模型调用`);
  };
  picker.onchange = showStep;
  showStep();
}

function renderPicker() {
  const picker = $("turn-picker");
  picker.replaceChildren();
  const current = element("optgroup");
  current.label = "本次会话";
  for (let index = turns.length - 1; index >= 0; index--) {
    const turn = turns[index];
    const option = element("option", "", `第 ${index + 1} 轮 · ${turn.prompt.slice(0, 35)}`);
    option.value = turn.trace_id;
    current.append(option);
  }
  if (current.children.length) picker.append(current);
  const previous = element("optgroup");
  previous.label = "历史 Trace";
  for (const trace of traces.filter((record) => !turns.some((turn) => turn.trace_id === record.trace_id))) {
    const date = new Date(trace.started_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    const option = element("option", "", `${date} · ${trace.user_input.slice(0, 30)}`);
    option.value = trace.trace_id;
    previous.append(option);
  }
  if (previous.children.length) picker.append(previous);
  if (pending && !selectedId) {
    const running = element("option", "", "当前轮次 · 等待模型回复…");
    running.value = "pending";
    running.disabled = true;
    picker.prepend(running);
  }
  if (!picker.options.length || !turns.length) {
    const placeholder = element("option", "", "选择轮次或历史调用");
    placeholder.value = "";
    picker.prepend(placeholder);
  }
  picker.value = pending && !selectedId ? "pending" : selectedId || "";
  $("record-count").textContent = `${traces.length} 条本地 trace · 自动保存`;
}

function addUser(prompt) {
  const message = element("article", "message user");
  message.append(element("div", "message-body", prompt));
  $("messages").append(message);
}

function addAssistant(turn, index) {
  const message = element("article", `message ${turn.error ? "error" : "assistant"}`);
  const heading = element("div", "assistant-heading");
  heading.append(element("span", "assistant-avatar", "↻"), element("span", "", "Agent"));
  const body = element("div", "message-body prose");
  renderProse(body, turn.error || turn.reply || "");
  const actions = element("div", "message-actions");
  for (const [tab, label] of [["context", "上下文"], ["trace", "Trace"]]) {
    const button = element("button", "action-button turn-action", label);
    button.dataset.traceId = turn.trace_id;
    button.dataset.tab = tab;
    button.setAttribute("aria-label", `查看第 ${index + 1} 轮${label}`);
    button.onclick = () => selectTurn(turn.trace_id, tab);
    actions.append(button);
  }
  const copy = element("button", "action-button", "复制");
  copy.onclick = () => copyText(turn.error || turn.reply || "", copy);
  actions.append(copy);
  if (turn.error) {
    const retry = element("button", "action-button", "重试");
    retry.disabled = busy;
    retry.onclick = () => submitPrompt(turn.prompt);
    actions.append(retry);
  }
  const trace = traces.find((record) => record.trace_id === turn.trace_id);
  if (trace) actions.append(element("span", "usage-inline", `${token(trace.total_tokens)} token · ${duration(trace.duration_ms)}`));
  message.append(heading, body, actions);
  $("messages").append(message);
}

function renderChat(scroll = false) {
  const list = $("messages");
  const previousScroll = list.scrollTop;
  list.replaceChildren();
  if (!turns.length && !pending) {
    const empty = element("div", "chat-empty");
    empty.append(element("span", "welcome-mark", "↻"), element("h2", "", "想聊点什么？"), element("p", "", "开始一段对话，在右侧查看每一轮的上下文和 token 用量。"));
    const suggestions = element("div", "suggestions");
    for (const prompt of ["温哥华现在几点，天气如何？", "我叫小林，请记住我的名字。", "用一个 Python 例子解释上下文。"] ) {
      const button = element("button", "suggestion", prompt);
      button.onclick = () => { $("prompt").value = prompt; resizeInput(); $("prompt").focus(); };
      suggestions.append(button);
    }
    empty.append(suggestions);
    list.append(empty);
  }
  turns.forEach((turn, index) => { addUser(turn.prompt); addAssistant(turn, index); });
  if (pending) {
    addUser(pending);
    const message = element("article", "message assistant");
    const heading = element("div", "assistant-heading");
    heading.append(element("span", "assistant-avatar", "↻"), element("span", "", "Agent"));
    const body = element("div", "message-body pending-text");
    body.append(element("span", "loading-dot"), element("span", "", "正在思考，等待模型回复…"));
    message.append(heading, body);
    list.append(message);
  }
  $("turn-count").textContent = `${turns.length} 轮对话`;
  list.scrollTop = scroll ? list.scrollHeight : previousScroll;
}

async function loadData(followLatest = false, scroll = false) {
  const [state, records] = await Promise.all([request("/api/state"), request("/api/traces")]);
  turns = state.turns;
  traces = records;
  setBusy(busy);
  $("model").textContent = state.model;
  $("tool-count").textContent = (state.tools || []).length;
  $("tool-definitions").textContent = JSON.stringify(state.tools || [], null, 2);
  if (followLatest) selectedId = turns.at(-1)?.trace_id || null;
  if (selectedId && !turns.some((turn) => turn.trace_id === selectedId) && !traces.some((trace) => trace.trace_id === selectedId)) selectedId = null;
  renderChat(scroll);
  renderPicker();
  if (selectedId) selectTurn(selectedId, null, false);
  else { $("step-picker").hidden = true; renderContext([], "每轮实际发送的消息"); renderTrace(null); }
}

async function streamTurn(prompt) {
  const response = await fetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt }),
  });
  if (!response.ok) throw new Error((await response.json()).error || `HTTP ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;
  let modelStep = 0;
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      if (event.type === "context") {
        selectedId = null;
        renderPicker();
        $("step-picker").hidden = true;
        renderContext(event.messages, `第 ${++modelStep} 次模型调用 · 等待回复…`);
        renderTrace(null);
      }
      if (event.type === "result") completed = true;
      if (event.type === "error") throw new Error(event.error);
    }
    if (done) break;
  }
  if (!completed) throw new Error("连接中断，请刷新查看本轮结果后再发送。");
}

async function submitPrompt(prompt) {
  if (busy || !prompt.trim()) return;
  $("notice").textContent = "";
  $("prompt").value = "";
  resizeInput();
  setBusy(true);
  pending = prompt;
  renderChat(true);
  try {
    await streamTurn(prompt);
  } catch (error) {
    $("notice").textContent = error.message;
    if (!$("prompt").value) $("prompt").value = prompt;
  } finally {
    pending = null;
    try { await loadData(true, true); } catch (error) { $("notice").textContent = error.message; }
    setBusy(false);
    resizeInput();
  }
}

async function resetConversation() {
  if (busy) return;
  setBusy(true);
  try {
    await request("/api/reset", {});
    selectedId = null;
    $("prompt").value = "";
    $("notice").textContent = "";
    await loadData(true);
  } catch (error) { $("notice").textContent = error.message; }
  finally { setBusy(false); resizeInput(); $("prompt").focus(); }
}

$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitPrompt($("prompt").value.trim());
});
$("prompt").addEventListener("input", resizeInput);
$("prompt").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!busy) $("chat-form").requestSubmit();
  }
});
for (const tab of ["context", "trace"]) {
  $(`${tab}-tab`).onclick = () => { showTab(tab); if (selectedId) selectTurn(selectedId, tab, false); };
  $(`${tab}-tab`).addEventListener("keydown", (event) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      showTab(event.key === "Home" ? "context" : event.key === "End" ? "trace" : selectedTab === "context" ? "trace" : "context", true);
    }
  });
}
$("turn-picker").onchange = () => selectTurn($("turn-picker").value);
$("toggle-inspector").onclick = () => openInspector($("layout").classList.contains("inspector-hidden"));
$("close-inspector").onclick = () => openInspector(false);
$("reset").onclick = resetConversation;
$("json-toggle").onclick = () => {
  rawContext = !rawContext;
  $("json-toggle").setAttribute("aria-pressed", String(rawContext));
  $("context-cards").hidden = rawContext;
  $("context-json").hidden = !rawContext || !currentMessages.length;
};
$("copy-context").onclick = () => copyText(JSON.stringify(currentMessages, null, 2), $("copy-context"));
$("copy-trace").onclick = () => copyText($("trace-json").textContent, $("copy-trace"));
$("refresh").onclick = () => loadData().catch((error) => { $("notice").textContent = error.message; });
if (window.matchMedia("(max-width: 760px)").matches) openInspector(false);
loadData(true, true).catch((error) => { $("notice").textContent = `无法连接本地服务：${error.message}`; });
