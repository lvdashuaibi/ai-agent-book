"""工具的静态定义、真实实现和执行入口。"""

import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime
from urllib.request import getproxies
from zoneinfo import ZoneInfo

from httpx import Client, HTTPError
from opentelemetry.trace import StatusCode

from telemetry import get_tracer


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "查询指定 IANA 时区的当前日期和时间，例如 America/Vancouver、Asia/Shanghai。",
            "parameters": {
                "type": "object",
                "properties": {"timezone": {"type": "string", "description": "IANA 时区名称"}},
                "required": ["timezone"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询城市当前天气数据。城市尽量包含国家或行政区，例如 Vancouver, Canada。返回实际匹配的地点和数据时间。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称，可附国家或行政区"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["city", "unit"],
                "additionalProperties": False,
            },
        },
    },
]

WEATHER_CODES = {
    0: "晴", 1: "大部晴朗", 2: "局部多云", 3: "阴天", 45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "中毛毛雨", 55: "强毛毛雨", 56: "轻冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "轻冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒", 80: "小阵雨", 81: "中阵雨",
    82: "强阵雨", 85: "小阵雪", 86: "强阵雪", 95: "雷暴", 96: "雷暴伴小冰雹", 99: "雷暴伴大冰雹",
}


def get_current_time(timezone: str) -> dict:
    now = datetime.now(ZoneInfo(timezone))
    return {"timezone": timezone, "datetime": now.isoformat(), "day_of_week": now.strftime("%A")}


def get_weather(city: str, unit: str) -> dict:
    """先解析城市坐标，再查询 Open-Meteo 当前天气，均为真实 HTTP 请求。"""
    if unit not in ("celsius", "fahrenheit"):
        raise ValueError("unit 必须为 celsius 或 fahrenheit。")
    with Client(timeout=10.0, proxy=getproxies().get("https"), trust_env=False) as client:
        location_response = client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en"},
        )
        location_response.raise_for_status()
        locations = location_response.json().get("results", [])
        if not locations:
            raise ValueError("找不到该城市，请提供城市英文名及国家或行政区后重试。")
        location = locations[0]
        weather_response = client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": location["latitude"], "longitude": location["longitude"],
                "current": "temperature_2m,relative_humidity_2m,weather_code",
                "temperature_unit": unit, "timezone": "auto",
            },
        )
        weather_response.raise_for_status()
        data = weather_response.json()
    current = data["current"]
    return {
        "city": location["name"], "country": location.get("country"),
        "admin1": location.get("admin1"),
        "latitude": location["latitude"], "longitude": location["longitude"],
        "timezone": data["timezone"], "data_time": current["time"],
        "temperature": current["temperature_2m"], "unit": unit,
        "humidity": current["relative_humidity_2m"], "weather_code": current["weather_code"],
        "conditions": WEATHER_CODES.get(current["weather_code"], "未知天气代码"),
        "source": "Open-Meteo 当前天气（天气模型数据）",
    }


TOOL_HANDLERS = {"get_current_time": get_current_time, "get_weather": get_weather}


def parse_arguments(call: dict) -> tuple[str, dict]:
    """校验工具名称和 JSON 参数，仅允许执行已注册的两个函数。"""
    name = call["function"]["name"]
    if name not in TOOL_HANDLERS:
        raise ValueError(f"未注册工具：{name}")
    arguments = json.loads(call["function"]["arguments"])
    definition = next(item["function"] for item in TOOL_DEFINITIONS if item["function"]["name"] == name)
    required = definition["parameters"]["required"]
    if not isinstance(arguments, dict) or set(arguments) != set(required):
        raise ValueError(f"参数应为仅包含 {', '.join(required)} 的 JSON 对象。")
    if any(not isinstance(value, str) or not value.strip() for value in arguments.values()):
        raise ValueError("所有工具参数必须为非空字符串。")
    return name, arguments


def execute_tool(call: dict) -> dict:
    """工具失败也返回匹配 call ID 的结果，让模型决定是否修正参数或解释错误。"""
    tracer = get_tracer(__name__)  # 【观测】由外层决定是否启用 trace。
    with tracer.start_as_current_span("tool.execute") as span:
        span.set_attributes({
            "openinference.span.kind": "TOOL",
            "tool.name": call["function"]["name"],
            "tool.call_id": call["id"],
            "input.value": call["function"]["arguments"],
        })
        try:
            name, arguments = parse_arguments(call)
            output = TOOL_HANDLERS[name](**arguments)
            span.set_status(StatusCode.OK)
        except HTTPError:
            output = {"error": "天气服务请求失败或超时，请稍后重试。"}
            span.set_status(StatusCode.ERROR, output["error"])
        except (ValueError, KeyError, TypeError) as exception:
            output = {"error": str(exception)}
            span.set_status(StatusCode.ERROR, output["error"])
        except Exception as exception:
            span.record_exception(exception)
            output = {"error": "工具执行异常，请查看本地 trace。"}
            span.set_status(StatusCode.ERROR, output["error"])
        content = json.dumps(output, ensure_ascii=False)
        span.set_attribute("output.value", content)
    return {"role": "tool", "tool_call_id": call["id"], "content": content}


def execute_tools(calls: list[dict]) -> list[dict]:
    """同一批独立调用并行执行，按原调用顺序回传，并将父 trace 传入线程。"""
    with ThreadPoolExecutor(max_workers=min(4, len(calls))) as executor:
        futures = [executor.submit(copy_context().run, execute_tool, call) for call in calls]
        return [future.result() for future in futures]
