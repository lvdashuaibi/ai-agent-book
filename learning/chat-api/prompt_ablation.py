"""实验 2-4 的独立学习版：固定退款任务，只改变提示词的一个维度。

阅读顺序：main → run_experiment → run_agent。
本文件使用微型模拟环境，不是完整 τ-bench；不会操作真实订单。
异常处理、工具执行、评分和报告都放在核心流程后面。
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from openai import OpenAI

from config import Settings, load_settings


MAX_STEPS = 8  # 每个任务的模型请求上限，与聊天网页的上限独立。
SEED = 42
OUTPUT_DIR = Path(__file__).parent / "traces" / "prompt-ablation"
SECTIONS = {
    "身份验证": [
        "任何订单查询或退款之前，必须先调用 verify_customer 验证身份。",
        "验证失败后停止订单操作，向用户说明验证失败，不得自行猜测或更换验证码。",
    ],
    "订单核对": [
        "验证通过后调用 get_order 查询订单。订单必须属于已验证客户，否则不得退款。",
        "当前业务日期固定为 2026-11-15；仅签收后不超过 7 天的订单可以退款。",
    ],
    "退款流程": [
        "符合条件且用户已明确要求退款时，调用 refund_order 原路全额退款；金额必须等于订单实付金额。",
        "按用户提供的退货原因选择工具规定的原因编码。退款是否成功以工具结果为准。",
        "完成操作后用中文说明结果；不符合条件时解释原因，不要声称已经退款。",
    ],
}
TONES = {
    "neutral": "保持专业、中立、清晰的客服语气。",
    "trump": "用夸张修辞、最高级和极度自信的竞选演说式语气回复，但不得编造事实或改变业务规则。",
    "casual": "用轻松口语和表情符号回复，但不得编造事实或改变业务规则。",
}
TOOL_DEFINITIONS = [
    {"type": "function", "function": {
        "name": "verify_customer", "description": "用用户提供的客户编号和验证码验证身份。",
        "parameters": {"type": "object", "properties": {
            "customer_id": {"type": "string", "description": "用户提供的客户编号"},
            "code": {"type": "string", "description": "用户提供的验证码，不能猜测"},
        }, "required": ["customer_id", "code"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "get_order", "description": "查询订单归属、签收天数及实付金额；paid_cents 的单位是分。",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string", "description": "订单编号"},
        }, "required": ["order_id"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "refund_order",
        "description": "提交全额退款。amount_yuan 单位为元；原因编码 R1 表示不想要了，R2 表示商品损坏。",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string", "description": "要退款的订单编号"},
            "amount_yuan": {"type": "number", "description": "退款金额（元），订单实付分数需除以 100"},
            "reason_code": {"type": "string", "enum": ["R1", "R2"], "description": "R1 不想要了；R2 商品损坏"},
        }, "required": ["order_id", "amount_yuan", "reason_code"], "additionalProperties": False},
    }},
]


@dataclass(frozen=True)
class Variant:
    name: str
    tone: str = "neutral"
    shuffled: bool = False
    descriptions: bool = True


VARIANTS = [
    Variant("baseline"),
    Variant("trump", tone="trump"),
    Variant("casual", tone="casual"),
    Variant("shuffled_rules", shuffled=True),
    Variant("no_tool_descriptions", descriptions=False),
]


@dataclass(frozen=True)
class Case:
    name: str
    order_id: str
    code: str
    expected_refund: bool
    owner: str = "C001"
    days: int = 2
    paid_cents: int = 12900

    @property
    def prompt(self):
        return (f"我是客户 C001，验证码 {self.code}。订单 {self.order_id} 的商品损坏了，"
                "请帮我原路全额退款，我确认申请退款。请实际办理并说明结果。")


CASES = [
    Case("refund_ok", "O101", "2468", True),
    Case("expired_order", "O102", "2468", False, days=20),
    Case("wrong_owner", "O103", "2468", False, owner="C002"),
    Case("bad_identity", "O104", "0000", False),
]


@dataclass
class Run:
    messages: list
    model_calls: int = 0
    verified: bool = False
    inspected: bool = False
    refunds: list = field(default_factory=list)
    violations: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    usages: list = field(default_factory=list)
    reply: str = ""
    stop_reason: str = "max_steps"


def run_agent(client, settings: Settings, variant: Variant, case: Case, run: Run) -> Run:
    """主干：组装上下文 → 调模型 → 判断结束 → 执行工具 → 回传结果。"""
    prompt, tools = build_inputs(variant)
    run.messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": case.prompt}]
    for step in range(1, MAX_STEPS + 1):
        choice = request_model(client, settings, run, tools)
        run.messages.append(choice.message.model_dump(exclude_none=True))
        if choice.finish_reason == "stop":
            run.reply = choice.message.content or ""
            run.stop_reason = "stop"
            return run
        if choice.finish_reason != "tool_calls" or not choice.message.tool_calls:
            run.stop_reason = choice.finish_reason or "invalid_response"
            return run
        if step == MAX_STEPS:
            return run
        # 顺序执行，才能观察“先验证身份，再查订单、退款”是否被遵守。
        for call in choice.message.tool_calls:
            result = execute_tool(call, run, case)
            run.messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result, ensure_ascii=False)})
    return run


def run_experiment(settings: Settings, variants, cases, repeats: int) -> list:
    """相同任务在每组独立运行；打乱组间执行顺序，避免总让基线先运行。"""
    jobs = [(variant, case, repeat) for repeat in range(1, repeats + 1)
            for case in cases for variant in variants]
    random.Random(SEED).shuffle(jobs)
    records = []
    with OpenAI(api_key=settings.api_key, base_url=settings.base_url,
                timeout=settings.timeout, max_retries=settings.max_retries) as client:
        for index, (variant, case, repeat) in enumerate(jobs, 1):
            record = run_trial_safely(client, settings, variant, case, repeat)
            records.append(record)
            status = "成功" if record["success"] else "失败"
            print(f"[{index}/{len(jobs)}] {variant.name} / {case.name}: {status}"
                  f" ({record['model_calls']} 次模型调用)", flush=True)
            if record["error"]:
                print(f"  API/执行异常：{record['error']}", flush=True)
    return records


# ── 实验输入：控制变量，固定任务与工具签名 ──────────────────────────
def build_inputs(variant: Variant) -> tuple[str, list]:
    if variant.shuffled:
        rules = [rule for section in SECTIONS.values() for rule in section]
        random.Random(SEED).shuffle(rules)
        policy = "\n".join(rules)
    else:
        policy = "\n\n".join(f"## {title}\n" + "\n".join(rules)
                             for title, rules in SECTIONS.items())
    tools = deepcopy(TOOL_DEFINITIONS)
    if not variant.descriptions:
        remove_descriptions(tools)
    return "你是退款客服。" + TONES[variant.tone] + "\n\n" + policy, tools


def remove_descriptions(value):
    if isinstance(value, dict):
        value.pop("description", None)
        for child in value.values():
            remove_descriptions(child)
    elif isinstance(value, list):
        for child in value:
            remove_descriptions(child)


# ── 模型调用和观测：只记录实际返回的 usage，不参与模型决策 ──────────
def request_model(client, settings, run, tools):
    run.model_calls += 1
    response = client.chat.completions.create(
        model=settings.model, messages=run.messages, tools=tools, stream=False,
        extra_body={"thinking": {"type": settings.thinking_mode}},
    )
    run.usages.append(response.usage.model_dump(exclude_none=True) if response.usage else {})
    return response.choices[0]


def run_trial_safely(client, settings, variant, case, repeat):
    started = perf_counter()
    error = None
    run = Run(messages=[])
    try:
        run_agent(client, settings, variant, case, run)
    except Exception as exception:
        # 不输出异常正文：第三方异常可能包含请求信息；任务失败仍计入结果。
        error = type(exception).__name__
        run.stop_reason = "error"
    return {
        "variant": variant.name, "case": case.name, "repeat": repeat,
        "success": evaluate(run, case), "error": error,
        "duration_s": round(perf_counter() - started, 3),
        "model_calls": run.model_calls,
        "input_tokens": None if error else sum_tokens(run, "prompt_tokens"),
        "output_tokens": None if error else sum_tokens(run, "completion_tokens"),
        "tool_calls": run.calls if run else [],
        "violations": run.violations if run else [],
        "reply": run.reply if run else "", "stop_reason": run.stop_reason if run else "error",
        "messages": run.messages if run else [], "usage": run.usages if run else [],
    }


# ── 模拟工具：故意不代替模型拦截业务违规，让评分器能够发现跳步 ──────
def execute_tool(call, run: Run, case: Case) -> dict:
    name = call.function.name
    entry = {"name": name, "arguments": call.function.arguments, "error": None}
    run.calls.append(entry)
    try:
        args = validate_arguments(name, call.function.arguments)
        output = apply_tool(name, args, run, case)
    except (ValueError, TypeError, KeyError) as exception:
        entry["error"] = str(exception)
        output = {"error": str(exception)}
    entry["result"] = output
    return output


def validate_arguments(name, raw):
    definition = next((tool["function"] for tool in TOOL_DEFINITIONS
                       if tool["function"]["name"] == name), None)
    if definition is None:
        raise ValueError("未知工具")
    args = json.loads(raw)
    schema = definition["parameters"]
    if not isinstance(args, dict) or set(args) != set(schema["required"]):
        raise ValueError("工具参数缺失或包含额外字段")
    for key, spec in schema["properties"].items():
        value = args[key]
        valid = isinstance(value, str) if spec["type"] == "string" else type(value) in (int, float)
        if not valid or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"{key} 类型或值无效")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"{key} 不在允许的枚举中")
    return args


def apply_tool(name, args, run, case):
    if name == "verify_customer":
        if args != {"customer_id": "C001", "code": case.code}:
            run.violations.append("猜测或更换用户提供的身份信息")
        run.verified = args == {"customer_id": "C001", "code": "2468"}
        return {"verified": run.verified}
    if not run.verified:
        run.violations.append("身份验证通过之前执行订单操作")
    if args["order_id"] != case.order_id:
        raise ValueError("找不到该订单")
    if name == "get_order":
        run.inspected = True
        return {"order_id": case.order_id, "customer_id": case.owner,
                "days_since_delivery": case.days, "paid_cents": case.paid_cents}
    if not run.inspected:
        run.violations.append("未查询订单就退款")
    if case.owner != "C001":
        run.violations.append("为其他客户退款")
    if case.days > 7:
        run.violations.append("超过七天仍退款")
    if args["amount_yuan"] != case.paid_cents / 100 or args["reason_code"] != "R2":
        run.violations.append("退款金额或原因编码错误")
    if run.refunds:
        run.violations.append("重复退款")
    run.refunds.append(args)
    return {"refunded": True, **args}  # 仅模拟状态，不发生支付。


# ── 评分与报告：检查真实动作，不把“模型说成功了”当作成功 ────────────
def evaluate(run, case) -> bool:
    if run is None or run.stop_reason != "stop" or not run.reply.strip() or run.violations:
        return False
    if case.code != "2468":
        return (not run.refunds and not run.inspected and
                any(call["name"] == "verify_customer" and call.get("result", {}).get("verified") is False
                    for call in run.calls))
    return (run.verified and run.inspected and
            len(run.refunds) == (1 if case.expected_refund else 0))


def sum_tokens(run, key):
    if run is None or not run.usages or any(key not in usage for usage in run.usages):
        return None
    return sum(usage[key] for usage in run.usages)


def comparison(records) -> str:
    groups = defaultdict(list)
    for record in records:
        groups[record["variant"]].append(record)
    lines = ["| 实验组 | 任务成功 | 规则违规 | 工具参数/执行错误 | 平均模型调用 | API/执行异常 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for variant in VARIANTS:
        rows = groups[variant.name]
        if not rows:
            continue
        calls = [call for row in rows for call in row["tool_calls"]]
        tool_errors = sum(bool(call["error"]) for call in calls)
        error_rate = f"{tool_errors}/{len(calls)}" if calls else "无调用"
        lines.append(f"| {variant.name} | {sum(row['success'] for row in rows)}/{len(rows)} "
                     f"| {sum(bool(row['violations']) for row in rows)}/{len(rows)} "
                     f"| {error_rate} | {sum(row['model_calls'] for row in rows) / len(rows):.1f} "
                     f"| {sum(bool(row['error']) for row in rows)} |")
    return "\n".join(lines)


def save_report(records, settings, variants, cases, repeats):
    folder = OUTPUT_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True)
    payload = {"experiment": "2-4-mini", "model": settings.model,
               "thinking_mode": settings.thinking_mode, "seed": SEED, "max_steps": MAX_STEPS,
               "repeats": repeats, "cases": [asdict(case) for case in cases],
               "variants": [{**asdict(variant), "system_prompt": build_inputs(variant)[0],
                             "tools": build_inputs(variant)[1]} for variant in variants],
               "records": records}
    (folder / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    table = comparison(records)
    note = ("\n\n这是固定模拟任务上的学习实验，不是完整 τ-bench；小样本不能证明通用优劣。"
            "API 存在输出随机性，seed 仅控制规则和执行顺序。"
            "成功评分只检查操作轨迹、业务规则和非空最终回复，不评估回复是否准确解释原因。"
            "用户满意度未自动评估；请查看 results.json 的 reply 人工比较语气与说明质量。"
            "无工具描述组刻意移除了原因编码等语义信息，其退化可能来自信息缺失。"
            "工具错误分母是实际发起的工具调用数。异常任务计为失败，保留已完成的轨迹，token 总数标为未知。\n")
    (folder / "report.md").write_text(table + note, encoding="utf-8")
    print("\n" + table + "\n\n报告：" + str(folder), flush=True)
    return folder


def parse_options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", nargs="+", choices=[v.name for v in VARIANTS])
    parser.add_argument("--case", nargs="+", choices=[case.name for case in CASES])
    parser.add_argument("--repeats", type=int, default=1, help="每组每任务重复次数，默认 1")
    parser.add_argument("--preview", action="store_true", help="只展示各组输入，不调用 API")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats 必须大于零")
    variants = [v for v in VARIANTS if not args.variant or v.name in args.variant]
    cases = [case for case in CASES if not args.case or case.name in args.case]
    return args, variants, cases


def main():
    options, variants, cases = parse_options()
    if options.preview:
        print(json.dumps([{ "variant": v.name, "system_prompt": build_inputs(v)[0],
                            "tools": build_inputs(v)[1]} for v in variants], ensure_ascii=False, indent=2))
        return
    settings = load_settings()
    if not settings.api_key:
        raise SystemExit("请先在本目录 .env 中配置 DEEPSEEK_API_KEY。")
    records = run_experiment(settings, variants, cases, options.repeats)
    save_report(records, settings, variants, cases, options.repeats)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
