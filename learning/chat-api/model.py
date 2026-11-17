"""将组装好的上下文发送给 DeepSeek，取回模型回复。"""

from dataclasses import dataclass, field

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from config import Settings
from tools import TOOL_DEFINITIONS


@dataclass(frozen=True)
class ModelResult:
    """保留 API 返回的文本和结束原因，供 loop 决定后续行为。"""

    reply: str | None
    finish_reason: str | None
    error: str | None = None
    error_code: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    assistant_message: dict | None = None


def call_model(messages: list[ChatCompletionMessageParam], settings: Settings) -> ModelResult:
    """发送一次请求，返回文本与 API 原始 finish_reason。"""
    with OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
    ) as client:
        response = client.chat.completions.create(
            model=settings.model,
            messages=messages,
            stream=False,
            tools=TOOL_DEFINITIONS,
            extra_body={"thinking": {"type": settings.thinking_mode}},
        )

    choice = response.choices[0]
    return ModelResult(
        reply=choice.message.content,
        finish_reason=choice.finish_reason,
        tool_calls=[call.model_dump(exclude_none=True) for call in (choice.message.tool_calls or [])],
        assistant_message=choice.message.model_dump(exclude_none=True),
    )
