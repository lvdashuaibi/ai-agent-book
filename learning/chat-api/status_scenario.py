"""复用书中固定轨迹；状态栏由代码从工具调用中统计，不让模型数数。"""
import importlib.util
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "chapter2" / "attention_visualization"


@lru_cache(maxsize=1)
def book_experiment():
    spec = importlib.util.spec_from_file_location("book_status_experiment", SOURCE / "run_status_bar_experiment.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_arms():
    source = book_experiment()
    protocol = json.loads((SOURCE / "status_bar_protocol.json").read_text(encoding="utf-8"))
    base = source.base_messages(protocol)
    counts = Counter()
    for message in base:
        for call in message.get("tool_calls", []):
            if call["function"]["name"] == "phone_call":
                args = call["function"]["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                counts[args["company"]] += 1
    count = counts["Xfinity"]
    limit = protocol["scenario"]["maximum_calls"]
    status = {"role": "user", "content": (
        "<agent_status>\nCurrent State:\n"
        f"- Tool call summary: 'phone_call' has been invoked {sum(counts.values())} times (Xfinity: {count} times)\n"
        f"- Constraint check: Maximum calls to Xfinity reached ({count}/{limit})\n</agent_status>")}
    assert status == source.status_message()
    return {"A": base, "B": [*base, status]}, protocol
