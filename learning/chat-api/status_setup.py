"""只下载实验所需的官方模型文件；首次运行一次，后续推理完全离线。"""
import os
from pathlib import Path
from urllib.request import getproxies

MODEL_ID = "Qwen/Qwen3-0.6B"
REVISION = "c1899de289a04d12100db370d81485cdf75e47ca"
MODEL_DIR = Path(__file__).parent / ".status-model"


def main():
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    proxy = getproxies().get("https")
    if proxy:
        os.environ.setdefault("HTTPS_PROXY", proxy)
        os.environ.setdefault("HTTP_PROXY", proxy)
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL_ID, revision=REVISION, local_dir=str(MODEL_DIR),
                      allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja"], max_workers=2)
    print("模型已就绪：" + str(MODEL_DIR), flush=True)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    main()
