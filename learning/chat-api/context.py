"""把系统提示词、历史消息和当前输入组装成模型上下文。"""

from openai.types.chat import ChatCompletionMessageParam


def build_context(
    system_prompt: str,
    history: list[ChatCompletionMessageParam],
    user_input: str,
) -> list[ChatCompletionMessageParam]:
    """每轮都完整提供上下文，历史记录由 loop 管理。"""
    return [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": user_input},
    ]
