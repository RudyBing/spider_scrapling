#!/usr/bin/env python3
"""爬虫系统入口

用法:
    python main.py              # 单次运行（仅爬虫）
    python main.py --translate  # 爬虫 + 翻译
    python main.py --schedule   # 定时调度模式
"""
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from loguru import logger
from scheduler.dispatcher import Crawler


def run_once(with_translate: bool = False):
    """单次执行（供 GitHub Actions 调用）
    
    Args:
        with_translate: 是否在执行爬虫后运行翻译任务
    """
    logger.info("=== 爬虫启动（单次运行） ===")
    crawler = Crawler()
    import asyncio
    asyncio.run(crawler.run())
    logger.info("=== 爬虫结束 ===")
    
    if with_translate:
        logger.info("=== 开始执行翻译任务 ===")
        from services.translate_service import get_translate_service
        service = get_translate_service()
        result = asyncio.run(service.run_translation_task(limit=100, max_concurrent=5))
        if result.get("success", 0) > 0:
            logger.info(f"✅ 翻译完成：成功 {result['success']} 条")
        elif result.get("error"):
            logger.error(f"翻译失败：{result['error']}")
        else:
            logger.info("没有待翻译的新闻")


def main():
    parser = argparse.ArgumentParser(description="Scrapling 爬虫系统")
    parser.add_argument("--schedule", action="store_true", help="定时调度模式")
    parser.add_argument("--translate", action="store_true", help="爬虫运行后执行翻译任务")
    args = parser.parse_args()

    logger.add(
        "logs/crawler_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="30 days",
        level="INFO",
        encoding="utf-8",
    )
    logger.add(sys.stderr, level="DEBUG")

    logger.info("=" * 50)
    logger.info("Scrapling 爬虫系统启动")
    logger.info("=" * 50)

    if args.schedule:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        from config import load_settings

        settings = load_settings()
        cron_expr = settings.get("SCHEDULE_CRON", "0 3 * * *")
        parts = cron_expr.split()

        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            lambda: run_once(with_translate=args.translate),
            trigger=CronTrigger(
                hour=int(parts[1]),
                minute=int(parts[0]),
                day=int(parts[2]) if parts[2] != "*" else "*",
                month=int(parts[3]) if parts[3] != "*" else "*",
                day_of_week=int(parts[4]) if parts[4] != "*" else "*",
            ),
            id="crawl_job",
            name="定时爬取任务",
        )
        scheduler.start()
        logger.info(f"调度器已启动，cron: {cron_expr}")
        try:
            while True:
                import asyncio as _asyncio
                _asyncio.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            logger.info("调度器停止")
            scheduler.shutdown()
    else:
        run_once(with_translate=args.translate)


if __name__ == "__main__":
    main()
