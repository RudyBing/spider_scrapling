"""新闻翻译脚本 - 批量翻译未翻译的新闻

使用方法:
    python scripts/translate_news.py [--limit 50] [--concurrent 3]

参数:
    --limit: 每次翻译的最大数量 (默认 50)
    --concurrent: 最大并发数 (默认 3，避免 API 限流)

配置:
    在 .env 文件中添加腾讯翻译君 API 密钥:
    TENCENT_SECRET_ID=your_secret_id
    TENCENT_SECRET_KEY=your_secret_key
"""
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import argparse
from loguru import logger
from services.translate_service import get_translate_service


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="批量翻译新闻")
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="每次翻译的最大数量 (默认 50)"
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        default=3,
        help="最大并发数 (默认 3，避免 API 限流)"
    )
    parser.add_argument(
        "--source-lang",
        type=str,
        default="en",
        help="源语言 (默认 en)"
    )
    parser.add_argument(
        "--target-lang",
        type=str,
        default="zh",
        help="目标语言 (默认 zh)"
    )
    
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("🌐 新闻翻译工具 - 腾讯翻译君")
    logger.info("=" * 60)
    
    try:
        service = get_translate_service()
        
        result = await service.run_translation_task(
            limit=args.limit,
            source_lang=args.source_lang,
            target_lang=args.target_lang,
            max_concurrent=args.concurrent
        )
        
        if result.get("error"):
            logger.error(f"翻译失败：{result['error']}")
            return 1
        
        success = result.get("success", 0)
        fail = result.get("fail", 0)
        
        if success > 0:
            logger.info(f"✅ 翻译成功 {success} 条")
        
        if fail > 0:
            logger.warning(f"⚠️ 翻译失败 {fail} 条")
        
        return 0
        
    except KeyboardInterrupt:
        logger.info("\n⚠️ 用户中断翻译")
        return 1
    except Exception as e:
        logger.exception(f"翻译异常：{e}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
