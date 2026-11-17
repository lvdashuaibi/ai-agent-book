"""实验 2-8 主干：同轨迹、同模型、同采样种子，只改变末尾状态栏。"""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from status_model import capture_attention, generate, load_model
from status_scenario import book_experiment, prepare_arms
from status_setup import MODEL_ID, REVISION

RUN_DIR = Path(__file__).parent / "traces" / "status-attention"
SEEDS = [27, 41, 73]


def run_experiment(trials=1, max_tokens=512, emit=lambda event: None):
    """准备对照 → 本地生成 → 首次配对采集注意力 → 保存报告。"""
    if trials not in (1, 3) or max_tokens not in (128, 256, 512):
        raise ValueError("不支持的试验次数或生成上限")
    arms, protocol = prepare_arms()
    folder = RUN_DIR / uuid4().hex
    folder.mkdir(parents=True)
    emit({"type": "progress", "message": "正在加载本地 Qwen3-0.6B（首次加载较慢）"})
    model, tokenizer = load_model()
    records = {arm: {"messages": messages, "trials": []} for arm, messages in arms.items()}
    for trial, seed in enumerate(SEEDS[:trials]):
        # 交替先跑 A/B，减轻固定顺序对耗时测量的影响。
        for arm in ("A", "B") if trial % 2 == 0 else ("B", "A"):
            rendered = book_experiment().render(tokenizer, arms[arm])
            emit({"type": "phase", "arm": arm, "trial": trial + 1, "seed": seed, "messages": arms[arm]})
            record = generate(model, tokenizer, rendered, seed, max_tokens,
                              lambda count, text: emit({"type": "tokens", "arm": arm, "count": count, "text": text}))
            full_ids = record.pop("full_ids")
            if trial == 0:
                emit({"type": "progress", "message": f"{arm} 组生成完成，正在采集最后一层真实注意力"})
                regions = book_experiment().find_region_indices(tokenizer, rendered)
                records[arm]["attention"] = capture_attention(model, tokenizer, full_ids, record["context_tokens"], regions)
            records[arm]["trials"].append(record)
            emit({"type": "trial", "arm": arm, "trial": trial + 1, "record": record})
    result = save_report(folder, records, protocol, max_tokens)
    emit({"type": "result", "result": result})
    return result


def save_report(folder, records, protocol, max_tokens):
    import numpy as np
    import torch
    import transformers
    from status_plot import plot_attention
    arrays = {}
    for arm, record in records.items():
        attention = record["attention"]
        arrays[arm + "_full"] = attention.pop("full_matrix")
        arrays[arm + "_prediction"] = attention["matrix"]
    np.savez_compressed(folder / "attention.npz", **arrays)
    plot_attention(records, folder / "attention.png")
    for record in records.values():
        # 网页显示保留 9 位小数；无损权重保存在 NPZ 中。
        record["attention"]["matrix"] = np.round(record["attention"]["matrix"].astype(float), 9).tolist()
    result = {"id": folder.name, "experiment": "2-8", "model": MODEL_ID, "revision": REVISION,
              "device": "cpu", "dtype": "float32", "threads": torch.get_num_threads(),
              "torch": torch.__version__, "transformers": transformers.__version__,
              "created_at": datetime.now(timezone.utc).isoformat(), "max_tokens": max_tokens,
              "same_base_trajectory": records["A"]["messages"] == records["B"]["messages"][:-1],
              "attention_trial": 1, "arms": records, "source_protocol": protocol,
              "notes": ["固定历史是模拟数据，生成和注意力为本机实测；未拨打真实电话。",
                        "只对第一对试验采集最后一层、全部注意力头的均值，不能据此证明因果关系。",
                        "INCOMPLETE 表示达到生成上限或输出无效，不作为成功拒绝；OTHER 单独报告。",
                        "耗时仅统计生成，不含模型加载和注意力采集；小样本 CPU 耗时不代表线上性能。"]}
    (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result
