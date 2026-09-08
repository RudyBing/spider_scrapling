"""TechCrunch Spider 单独调试入口（不连数据库）"""
import asyncio
import sys
import os
from loguru import logger

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(__file__))

# 禁止 loguru 自动添加 handler，避免与调试控制台冲突
logger.remove()
logger.add(sys.stderr, level="DEBUG", format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")

from spiders.litellm import LiteLLMSpider
from spiders.techcrunch import TechCrunchSpider
from spiders.technologyreview import TechnologyReviewSpider


async def main():
    # spider = LiteLLMSpider()
    # spider = TechCrunchSpider()
    # spider = TechnologyReviewSpider()
    # items = await spider.crawl() or []
    # print(f"\n共采集 {len(items)} 条")
    # logger.info("=== 开始执行翻译任务 ===")

    ## 翻译
    # from services.translate_service import get_translate_service
    # service = get_translate_service()
    # result = await service.run_translation_task(limit=200, max_concurrent=1)
    # logger.info(result)

    ## ai生成模型描述和能力
    from services.ai_generate_service import run_ai_generate_task
    result = await run_ai_generate_task(limit=50)
    if result.get("success", 0) > 0:
        logger.info(f"✅ AI 生成完成：成功 {result['success']} 个")
    elif result.get("error"):
        logger.error(f"AI 生成失败：{result['error']}")
    else:
        logger.info("没有待处理的模型")    


if __name__ == "__main__":
    asyncio.run(main())

