"""加载配置：YAML 文件 + 环境变量"""
from pathlib import Path
from loguru import logger
import yaml
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "settings.yaml"
SITES_PATH = BASE_DIR / "config" / "sites.yaml"


def load_settings() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    # 环境变量覆盖
    data["DATABASE_URL"] = os.getenv("DATABASE_URL", data.get("DATABASE_URL", ""))
    data["CONCURRENCY"] = int(os.getenv("CONCURRENCY", data.get("CONCURRENCY", 5)))
    data["REQUEST_DELAY"] = int(os.getenv("REQUEST_DELAY", data.get("REQUEST_DELAY", 2)))
    data["USE_STEALTH"] = os.getenv("USE_STEALTH", str(data.get("USE_STEALTH", True))).lower() == "true"
    data["SCHEDULE_CRON"] = os.getenv("SCHEDULE_CRON", data.get("SCHEDULE_CRON", "0 3 * * *"))
    return data


def load_sites() -> list[dict]:
    with open(SITES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f).get("sites", [])


logger.info("配置加载完成")
