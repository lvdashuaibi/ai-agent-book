"""实际执行的 Agent Loop；网页展示和 trace 记录在 observability.py。"""

from collections.abc import Callable
from dataclasses import replace

from openai.types.chat import ChatCompletionMessageParam

from config import Settings
from context import build_context
from execution import call_model_safely
from model import ModelResult
from tools import execute_tools


def _ignore_event(name: str, data: dict) -> None:
    """【观测】未传观察回调时，忽略事件。"""


class AgentLoop:
    def __init__(self, settings: Settings):
        if settings.max_steps < 1 or settings.max_tool_calls_per_step < 1:
            raise ValueError("模型步数和单步工具调用上限必须大于零。")
        self.settings = settings
        # 【实际使用】下一轮会发送给模型的成功历史，包含 assistant 和 tool 消息。
        self.history: list[ChatCompletionMessageParam] = []

    def run_turn(
        self,
        user_input: str,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> ModelResult:
        """执行用户任务，返回最终回复或错误。可直接调用，无需网页或记录器。

        只有标为【观测】的 emit 调用用于界面和 trace，不参与模型决策。
        观察者收到上下文、模型结果和工具结果事件，负责自行保存快照。
        """
        user_input = self._validate_input(user_input)
        messages = build_context(self.settings.system_prompt, self.history, user_input)
        emit = on_event or _ignore_event

        for step in range(1, self.settings.max_steps + 1):
            emit("context", {"step": step, "messages": messages})  # 【观测】调用前通知界面。
            result = call_model_safely(messages, self.settings)
            emit("model_result", {"result": result})  # 【观测】记录本次模型返回。
            if result.error:
                return result

            # 【实际使用】原始 assistant 消息必须回传，才能与后续工具结果关联。
            messages.append(result.assistant_message)
            if result.finish_reason == "stop":
                self.history = messages[1:]  # 提交成功轨迹，system 由下一轮重新加入。
                return result

            if step == self.settings.max_steps:
                return replace(result, error="已达到本轮模型调用步数上限。", error_code="max_steps")

            # 【实际使用】执行工具，并将带 tool_call_id 的结果交给下一次模型调用。
            tool_results = execute_tools(result.tool_calls)
            messages.extend(tool_results)
            emit("tool_results", {"messages": tool_results})  # 【观测】保存工具执行快照。

    @staticmethod
    def _validate_input(user_input: str) -> str:
        user_input = user_input.strip()
        if not user_input:
            raise ValueError("请输入问题。")
        return user_input
