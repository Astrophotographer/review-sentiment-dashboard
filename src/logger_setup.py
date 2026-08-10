"""
로깅 설정 모듈
--------------
INFO / WARNING / ERROR 레벨의 로그를 콘솔과 파일(logs/app.log)에 동시에 기록한다.
"""
import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(config: dict) -> logging.Logger:
    log_dir = config.get("logging", {}).get("log_dir", "logs")
    level_name = config.get("logging", {}).get("level", "INFO")
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("review_dashboard")
    logger.setLevel(getattr(logging, level_name, logging.INFO))

    if logger.handlers:
        # 이미 설정된 경우 중복 핸들러 방지
        return logger

    fmt = logging.Formatter("[%(levelname)s] %(message)s")
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(file_fmt)
    file_handler.setLevel(logging.DEBUG)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger
