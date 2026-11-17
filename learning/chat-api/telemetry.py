"""用 OpenInference 采集模型调用，用 OpenTelemetry 将 spans 保存到本地。"""

import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Lock

from openinference.instrumentation.openai import OpenAIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import StatusCode, get_tracer as get_default_tracer


TRACE_FILE = Path(__file__).parent / "traces" / "spans.jsonl"
_file_lock = Lock()
_provider = None


class JsonlExporter(SpanExporter):
    """每行一个标准 OpenTelemetry span，包含调用、回复、usage 和错误。"""

    def export(self, spans):
        with _file_lock:
            TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with TRACE_FILE.open("a", encoding="utf-8") as output:
                for span in spans:
                    output.write(span.to_json(indent=None) + "\n")
        return SpanExportResult.SUCCESS


def setup_tracing() -> TracerProvider:
    global _provider
    if _provider is None:
        _provider = TracerProvider(resource=Resource.create({"service.name": "learning-agent"}))
        _provider.add_span_processor(SimpleSpanProcessor(JsonlExporter()))
        OpenAIInstrumentor().instrument(tracer_provider=_provider)
    return _provider


def get_tracer(name: str):
    """仅获取 tracer；直接使用核心执行器时，不自动启用本地观测。"""
    if _provider is None:
        return get_default_tracer(name)
    return _provider.get_tracer(name)


@contextmanager
def trace_turn(session_id: str, turn_number: int, user_input: str):
    """建立一轮任务的 trace，将采集细节移出核心流程。"""
    tracer = setup_tracing().get_tracer(__name__)
    with tracer.start_as_current_span("agent.turn") as span:
        span.set_attributes({
            "session.id": session_id,
            "agent.turn": turn_number,
            "agent.user_input": user_input,
        })
        yield span


def record_turn(span, turn: dict) -> None:
    """记录本轮回复、错误与结束原因。"""
    span.set_attribute("agent.stop_reason", turn["stop_reason"])
    if turn["finish_reason"] is not None:
        span.set_attribute("agent.finish_reason", turn["finish_reason"])
    if turn["error"]:
        span.set_status(StatusCode.ERROR, turn["error"])
        span.set_attribute("agent.error", turn["error"])
    else:
        span.set_status(StatusCode.OK)
    if turn["reply"] is not None:
        span.set_attribute("agent.reply", turn["reply"])
    steps = turn.get("steps", [])
    span.set_attribute("agent.model_steps", len(steps))
    span.set_attribute("agent.tool_calls", sum(len(step["tool_results"]) for step in steps))


def cache_hit_rate(hit: int | None, miss: int | None) -> float | None:
    """按输入 token 数计算命中比例；缺失或没有输入时不计算。"""
    if hit is None or miss is None or hit + miss == 0:
        return None
    return hit / (hit + miss)


def extract_cache_usage(attributes: dict) -> dict:
    """从已采集的 API usage 读取缓存数据，无需在核心 loop 中添加统计。"""
    try:
        usage = json.loads(attributes.get("output.value", "{}")).get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
    except (ValueError, AttributeError, TypeError):
        usage = {}

    # 优先使用 DeepSeek 原始字段，兼容 OpenInference 已提取的 cached_tokens。
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = attributes.get("llm.token_count.prompt_details.cache_read")
    miss = usage.get("prompt_cache_miss_tokens")
    hit = hit if type(hit) is int and hit >= 0 else None
    miss = miss if type(miss) is int and miss >= 0 else None
    prompt = attributes.get("llm.token_count.prompt")
    if miss is None and hit is not None and type(prompt) is int and prompt >= hit:
        miss = prompt - hit
    return {
        "cache_hit_tokens": hit,
        "cache_miss_tokens": miss,
        "cache_hit_rate": cache_hit_rate(hit, miss),
    }


def summarize_model_call(span: dict, step: int) -> dict:
    attributes = span["attributes"]
    start = datetime.fromisoformat(span["start_time"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(span["end_time"].replace("Z", "+00:00"))
    try:
        messages = json.loads(attributes.get("input.value", "{}")).get("messages", [])
    except (ValueError, AttributeError):
        messages = []
    return {
        "step": step, "messages": messages,
        "input_tokens": attributes.get("llm.token_count.prompt"),
        "output_tokens": attributes.get("llm.token_count.completion"),
        "total_tokens": attributes.get("llm.token_count.total"),
        **extract_cache_usage(attributes),
        "duration_ms": round((end - start).total_seconds() * 1000),
    }


def sum_usage(calls: list[dict], key: str) -> int | None:
    """存在未返回 usage 的调用时，总用量也标为未知，避免少报。"""
    if not calls or any(call[key] is None for call in calls):
        return None
    return sum(call[key] for call in calls)


def read_traces() -> list[dict]:
    """为网页汇总最近的 trace，完整的原始 spans 仍保存在文件中。"""
    with _file_lock:
        if not TRACE_FILE.exists():
            return []
        lines = TRACE_FILE.read_text(encoding="utf-8").splitlines()

    groups = {}
    for line in lines[-1000:]:
        try:
            span = json.loads(line)
        except json.JSONDecodeError:
            continue
        trace_id = span["context"]["trace_id"]
        groups.setdefault(trace_id, []).append(span)

    records = []
    for trace_id, spans in groups.items():
        root = next((span for span in spans if span["name"] == "agent.turn"), None)
        if root is None:
            continue
        llms = sorted(
            [span for span in spans if span["attributes"].get("openinference.span.kind") == "LLM"],
            key=lambda span: span["start_time"],
        )
        calls = [summarize_model_call(span, step) for step, span in enumerate(llms, 1)]
        cache_hit = sum_usage(calls, "cache_hit_tokens")
        cache_miss = sum_usage(calls, "cache_miss_tokens")
        attributes = llms[-1]["attributes"] if llms else {}
        tool_calls = [{
            "name": span["attributes"].get("tool.name"),
            "call_id": span["attributes"].get("tool.call_id"),
            "arguments": span["attributes"].get("input.value"),
            "result": span["attributes"].get("output.value"),
            "status": span["status"]["status_code"],
        } for span in spans if span["attributes"].get("openinference.span.kind") == "TOOL"]
        metadata = root["attributes"]
        start = datetime.fromisoformat(root["start_time"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(root["end_time"].replace("Z", "+00:00"))
        records.append({
            "trace_id": trace_id,
            "session_id": metadata.get("session.id"),
            "turn": metadata.get("agent.turn"),
            "started_at": root["start_time"],
            "duration_ms": round((end - start).total_seconds() * 1000),
            "status": root["status"]["status_code"],
            "user_input": metadata.get("agent.user_input", ""),
            "reply": metadata.get("agent.reply", ""),
            "error": metadata.get("agent.error"),
            "stop_reason": metadata.get("agent.stop_reason"),
            "finish_reason": metadata.get("agent.finish_reason"),
            "model": attributes.get("llm.model_name"),
            "input_tokens": sum_usage(calls, "input_tokens"),
            "output_tokens": sum_usage(calls, "output_tokens"),
            "total_tokens": sum_usage(calls, "total_tokens"),
            "cache_hit_tokens": cache_hit,
            "cache_miss_tokens": cache_miss,
            "cache_hit_rate": cache_hit_rate(cache_hit, cache_miss),
            "model_calls": calls,
            "tool_calls": tool_calls,
            "spans": spans,
        })
    return sorted(records, key=lambda record: record["started_at"], reverse=True)[:100]
