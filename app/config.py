"""配置加载：settings.json 放公开设置，secrets.json 放你自己的密钥。"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
LOG_DIR = ROOT / "logs"

SETTINGS_FILE = ROOT / "settings.json"
SECRETS_FILE = ROOT / "secrets.json"


def ensure_dirs() -> None:
    for d in (DATA_DIR, REPORT_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        settings = json.load(f)
    # 环境变量可以覆盖，方便临时调试
    if os.environ.get("BRIEF_PORT"):
        settings["port"] = int(os.environ["BRIEF_PORT"])
    # 云端（GitHub Actions）模式：打开国内访问不到的优质源
    if os.environ.get("BRIEF_PROFILE") == "cloud":
        for src in settings.get("sources", []):
            if src.get("cloud_only"):
                src["enabled"] = True
        # 源更多，给抓取留更长时间
        settings["fetch_deadline_seconds"] = max(int(settings.get("fetch_deadline_seconds", 25)), 45)
    return settings


def load_secrets() -> dict:
    secrets: dict = {}
    if SECRETS_FILE.exists():
        with open(SECRETS_FILE, "r", encoding="utf-8") as f:
            secrets = json.load(f)
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key and not secrets.get("deepseek_api_key"):
        secrets["deepseek_api_key"] = key
    sendkey = os.environ.get("SCT_SENDKEY")
    if sendkey:
        secrets.setdefault("webhook", {})
        secrets["webhook"].update(enabled=True, sendkey=sendkey)
    return secrets


def has_api_key(secrets: dict | None = None) -> bool:
    secrets = secrets if secrets is not None else load_secrets()
    key = str(secrets.get("deepseek_api_key") or "").strip()
    return bool(key) and not key.startswith("sk-在这里填入")
