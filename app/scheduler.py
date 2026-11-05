"""每天定时生成并推送。放在后台跑，电脑开机且登录时有效（关机当天不会补发）。"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime

from .config import LOG_DIR, ensure_dirs, load_settings
from .pipeline import run
from .store import get_report

LOG_FILE = LOG_DIR / "scheduler.log"


def setup_log() -> None:
    ensure_dirs()
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )


def main() -> None:
    setup_log()
    settings = load_settings()
    hh, mm = (int(x) for x in str(settings.get("schedule_time", "08:00")).split(":"))
    logging.info("定时器启动，每天 %02d:%02d 生成并推送", hh, mm)
    attempts: dict[str, int] = {}

    while True:
        try:
            now = datetime.now()
            today = date.today().isoformat()
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now >= target and not get_report(today) and attempts.get(today, 0) < 3:
                attempts[today] = attempts.get(today, 0) + 1
                logging.info("开始生成 %s（第 %d 次尝试）", today, attempts[today])
                res = run(today, push=True)
                logging.info("结果：%s", res.get("message"))
                if not res.get("ok"):
                    # 失败等 10 分钟再试，避免网络抖动
                    time.sleep(600)
                    continue
            elif now.hour == 0 and now.minute == 0:
                attempts.clear()
        except Exception as e:  # noqa: BLE001
            logging.exception("定时任务异常：%s", e)
        time.sleep(30)


if __name__ == "__main__":
    main()
