# src/config_loader.py
# 每次调用都从磁盘重新读取，修改 config.yaml 后立即生效，无需重启

import yaml
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def load_config() -> dict:
    """从磁盘加载完整配置"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_tickers() -> list:
    config = load_config()
    return [item["ticker"] for item in config["watchlist"]]


def get_watchlist() -> list:
    """返回完整 watchlist，包含 name 和 group"""
    config = load_config()
    return config["watchlist"]


def get_settings() -> dict:
    config = load_config()
    return config.get("settings", {})
