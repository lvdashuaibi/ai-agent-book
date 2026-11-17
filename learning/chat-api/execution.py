"""把模型调用中的异常和非正常结束转换成统一结果。"""

from openai import APIConnectionError, APIStatusError, APITimeoutError
from openai.types.chat import ChatCompletionMessageParam
from opentelemetry.trace import get_current_span

from config import Settings
from model import ModelResult, call_model


FINISH_ERRORS = {
    "length": "模型输出被长度限制截断，本轮未完成。",
    "function_call": "模型请求调用函数，当前阶段尚未实现工具执行。",
    "content_filter": "模型回复被服务端内容策略终止。",
}


def call_model_safely(
    messages: list[ChatCompletionMessageParam], settings: Settings,
) -> ModelResult:
    """异常在这里处理，loop 只需关注 finish_reason 和结果。"""
    try:
        if not settings.api_key:
            raise ValueError("请先在本地 .env 文件中填写 DEEPSEEK_API_KEY，再重启服务。")
        result = call_model(messages, settings)
    except APITimeoutError:
        return ModelResult(None, None, "模型请求超时，本轮已停止，请重试。", "timeout")
    except APIConnectionError:
        return ModelResult(None, None, "连接 DeepSeek 失败，请检查网络后重试。", "connection_error")
    except APIStatusError as exception:
        return ModelResult(None, None, f"DeepSeek 返回 HTTP {exception.status_code}，请检查密钥、余额和模型名。", "api_error")
    except ValueError as exception:
        return ModelResult(None, None, str(exception), "invalid_response_or_config")
    except Exception as exception:
        get_current_span().record_exception(exception)
        return ModelResult(None, None, "模型调用异常，请检查本地 trace 记录。", "internal_error")

    if result.finish_reason == "tool_calls":
        return validate_tool_calls(result, settings.max_tool_calls_per_step)
    if result.finish_reason != "stop":
        error = FINISH_ERRORS.get(result.finish_reason, "模型未正常结束回复，请查看 trace。")
        return ModelResult(result.reply, result.finish_reason, error, result.finish_reason or "unknown")
    if result.reply is None or not result.reply.strip():
        return ModelResult(result.reply, result.finish_reason, "模型未返回有效文本回复。", "invalid_response")
    return result


def validate_tool_calls(result: ModelResult, limit: int) -> ModelResult:
    """先确认整批调用具备有效的 ID 和结构，再允许 loop 执行。"""
    try:
        if not 1 <= len(result.tool_calls) <= limit:
            raise ValueError(f"单步工具调用数量必须在 1–{limit} 之间。")
        ids = []
        for call in result.tool_calls:
            if call.get("type") != "function" or not isinstance(call.get("id"), str) or not call["id"]:
                raise ValueError("工具调用必须具有 function 类型和有效 ID。")
            function = call.get("function", {})
            if not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), str):
                raise ValueError("工具调用缺少名称或 JSON 参数字符串。")
            ids.append(call["id"])
        if len(set(ids)) != len(ids):
            raise ValueError("同一批工具调用的 ID 不能重复。")
    except ValueError as exception:
        return ModelResult(result.reply, result.finish_reason, str(exception), "invalid_tool_calls")
    return result
