#!/usr/bin/env python3
"""独立运行 AI 生成描述任务

用法:
    python scripts/run_ai_generate.py           # 默认处理 100 个模型
    python scripts/run_ai_generate.py --limit 50  # 处理 50 个模型

功能:
    从 spider_ai_models 表读取 description/strengths 为空的记录，
    使用 AI 生成描述和优势，然后更新回数据库。

AI 服务：Agnes 2.5 Flash (主) + GLM-4.7 Flash (备) 双保险
"""
import sys
import os
import asyncio
import argparse

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from services.ai_generate_service import run_ai_generate_task


def main():
    parser = argparse.ArgumentParser(description="AI 生成模型描述和优势")
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="每次处理的最大模型数量（默认：100）"
    )
    args = parser.parse_args()
    
    # 配置日志
    logger.add(
        "logs/ai_generate_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="30 days",
        level="INFO",
        encoding="utf-8",
    )
    logger.add(sys.stderr, level="DEBUG")
    
    logger.info("=" * 60)
    logger.info("🤖 AI 生成模型描述和优势")
    logger.info("=" * 60)
    
    # 检查环境变量
    agnes_key = os.environ.get("AGNES_API_KEY")
    glm_key = os.environ.get("GLM_API_KEY")
    
    if not agnes_key and not glm_key:
        logger.error("❌ 缺少 API Key 配置")
        logger.error("   请设置以下至少一个环境变量:")
        logger.error("   - AGNES_API_KEY (Agnes 2.5 Flash)")
        logger.error("   - GLM_API_KEY (GLM-4.7 Flash)")
        logger.error("")
        logger.error("   例如:")
        logger.error("   export AGNES_API_KEY=your_agnes_api_key")
        logger.error("   export GLM_API_KEY=your_glm_api_key")
        sys.exit(1)
    
    if agnes_key:
        logger.info(f"✅ Agnes API Key 已配置")
    else:
        logger.warning(f"⚠️ Agnes API Key 未配置，将仅使用 GLM")
    
    if glm_key:
        logger.info(f"✅ GLM API Key 已配置")
    else:
        logger.warning(f"⚠️ GLM API Key 未配置，将仅使用 Agnes")
    
    logger.info("")
    logger.info(f"📦 处理限制：{args.limit} 个模型")
    logger.info("")
    
    # 运行 AI 生成任务
    try:
        result = asyncio.run(run_ai_generate_task(limit=args.limit))
        
        if result.get("error"):
            logger.error(f"❌ 任务失败：{result['error']}")
            sys.exit(1)
        
        success = result.get("success", 0)
        fail = result.get("fail", 0)
        
        logger.info("")
        if success > 0:
            logger.info(f"✅ AI 生成任务完成：成功 {success} 个，失败 {fail} 个")
        else:
            logger.info("✅ 没有待处理的模型或全部失败")
        
        sys.exit(0 if success > 0 or fail == 0 else 1)
        
    except Exception as e:
        logger.error(f"❌ 任务异常：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
