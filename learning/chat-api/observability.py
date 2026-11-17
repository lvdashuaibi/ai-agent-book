"""观测适配层：收集步骤快照、推送网页上下文、记录 trace。

需要直接使用 Agent 时调用 loop.AgentLoop；需要网页观测时再包这一层。
"""

from uuid import uuid4

from loop import AgentLoop
from telemetry import record_turn, trace_turn


class TurnRecorder:
    """只记录执行事件，不负责调用模型或工具。"""

    def __init__(self, on_context=None):
        self.steps = []
        self.on_context = on_context

    def record(self, event: str, data: dict) -> None:
        if event == "context":
            messages = data["messages"]
            self.steps.append({
                "step": data["step"], "messages": list(messages),
                "reply": None, "finish_reason": None,
                "tool_calls": [], "tool_results": [], "error": None,
            })
            if self.on_context:
                self.on_context(messages)
        elif event == "model_result":
            result = data["result"]
            self.steps[-1].update({
                "reply": result.reply, "finish_reason": result.finish_reason,
                "tool_calls": result.tool_calls, "error": result.error,
            })
        elif event == "tool_results":
            self.steps[-1]["tool_results"] = data["messages"]


class ObservedAgent:
    """为实际执行器添加观测能力，并维护网页需要的展示记录。"""

    def __init__(self, agent: AgentLoop):
        self.agent = agent
        self.turns = []
        self.session_id = uuid4().hex

    def run_turn(self, user_input: str, on_context=None) -> dict:
        recorder = TurnRecorder(on_context)
        with trace_turn(self.session_id, len(self.turns) + 1, user_input) as span:
            result = self.agent.run_turn(user_input, on_event=recorder.record)
            turn = {
                "prompt": user_input.strip(),
                "messages": recorder.steps[-1]["messages"],
                "steps": recorder.steps,
                "reply": result.reply,
                "error": result.error,
                "finish_reason": result.finish_reason,
                "stop_reason": result.error_code or ("final_answer" if result.finish_reason == "stop" else result.finish_reason),
                "trace_id": f"0x{span.get_span_context().trace_id:032x}",
            }
            record_turn(span, turn)
        self.turns.append(turn)
        return turn
