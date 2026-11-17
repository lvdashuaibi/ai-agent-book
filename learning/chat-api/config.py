"""配置加载与默认值。密钥从本地 .env 或环境变量读取。"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    system_prompt: str = (
        "You are a helpful assistant. Reply in Chinese. "
        "Use the provided tools for current time and weather. "
        "Report the resolved location and data time for weather. "
        "If a tool returns an error, correct the arguments or explain the failure honestly."
    )
    thinking_mode: str = "disabled"
    timeout: float = 60.0
    max_retries: int = 0
    max_steps: int = 50
    max_tool_calls_per_step: int = 8


def load_settings() -> Settings:
    """加载脚本旁的配置文件，已设置的环境变量优先。"""
    load_dotenv(Path(__file__).with_name(".env"))
    return Settings(
        api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
        model=os.getenv("DEEPSEEK_MODEL", Settings.model).strip(),
    )
