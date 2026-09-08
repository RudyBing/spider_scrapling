"""AI 生成服务层 - 使用 AI 生成模型描述和优势

功能：
1. 从 spider_ai_models 表读取 description/strengths 为空的记录
2. 调用 AI API 生成描述和优势
3. 更新回数据库

AI 服务：Agnes 2.5 Flash (主) + GLM-4.7 Flash (备) 双保险

配置方式:
    在 .env 文件中添加:
    AGNES_API_KEY=your_agnes_api_key
    GLM_API_KEY=your_glm_api_key
    
    或使用环境变量:
    export AGNES_API_KEY=your_agnes_api_key
    export GLM_API_KEY=your_glm_api_key
"""
import asyncio
import os
import re
from datetime import datetime
from loguru import logger
from typing import Optional
import aiohttp
from dotenv import load_dotenv
from storage.db import get_pool

# 加载 .env 文件到环境变量
load_dotenv()


class AIService:
    """AI 服务配置"""
    def __init__(self, name: str, base_url: str, model: str, api_key_env: str, is_primary: bool = False):
        self.name = name
        self.base_url = base_url
        self.model = model
        self.api_key_env = api_key_env
        self.is_primary = is_primary


# AI 服务配置：Agnes (主) + GLM (备) 双保险
AI_SERVICES = {
    "agnes": AIService(
        name="Agnes 2.5 Flash",
        base_url="https://apihub.agnes-ai.cn/v1",
        model="agnes-2.5-flash",
        api_key_env="AGNES_API_KEY",
        is_primary=True,
    ),
    "glm": AIService(
        name="GLM-4.7 Flash",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4-flash",
        api_key_env="GLM_API_KEY",
        is_primary=False,
    ),
}

PRIMARY_SERVICE = AI_SERVICES["agnes"]
FALLBACK_SERVICE = AI_SERVICES["glm"]


def get_api_key(service: AIService) -> str:
    """获取 API Key"""
    api_key = os.environ.get(service.api_key_env)
    if not api_key:
        raise ValueError(
            f"缺少 API Key: 请设置环境变量 {service.api_key_env}\n"
            f"例如：export {service.api_key_env}=your_api_key"
        )
    return api_key


def generate_prompt(model: dict) -> str:
    """生成 AI 提示词"""
    pricing_input = model.get("pricing_input") or "未公开"
    pricing_output = model.get("pricing_output") or "未公开"
    multimodal = "支持" if model.get("multimodal", False) else "不支持"
    
    return f"""你是一个 AI 模型专家，请为以下模型生成简短的中文介绍：

**模型信息**：
- 名称：{model.get("name", "未知")}
- 厂商：{model.get("provider", "未知")}
- 类别：{model.get("category", "未知")}
- 价格：{pricing_input} (输入), {pricing_output} (输出)
- 上下文窗口：{model.get("context_window", "未知")}
- 多模态：{multimodal}

**任务**：
1. **描述**（50-80 字）：简洁介绍模型定位、核心能力、适用场景
2. **优势**（3-5 条）：列出该模型相比竞品的独特优势

**输出格式**（严格 JSON）：
{{
  "description": "简短描述...",
  "strengths": ["优势 1", "优势 2", "优势 3"]
}}

**示例**：
{{
  "description": "OpenAI 最新一代推理模型，在数学、科学和编程领域表现卓越，支持复杂的多步推理任务。",
  "strengths": ["强大的推理能力", "优秀的数学和科学表现", "支持复杂任务分解"]
}}"""


async def call_ai(prompt: str, service: AIService) -> dict:
    """调用 AI API
    
    Args:
        prompt: 提示词
        service: AI 服务配置
    
    Returns:
        dict: 包含 description 和 strengths 的结果
    """
    api_key = get_api_key(service)
    url = f"{service.base_url}/chat/completions"
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "model": service.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是一个专业的 AI 模型分析师，擅长用简洁准确的语言描述模型特点。",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "temperature": 0.7,
                "max_tokens": 300,
            },
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if not response.ok:
                error_text = await response.text()
                raise Exception(f"AI API 调用失败：{response.status} {error_text}")
            
            data = await response.json()
            content = data["choices"][0]["message"]["content"].strip()
            
            # 解析 JSON（处理可能的 markdown 代码块）
            json_match = re.search(r"\{[\s\S]*\}", content)
            if not json_match:
                raise Exception(f"AI 返回格式错误：{content}")
            
            try:
                result = eval(json_match.group())  # 使用 eval 处理可能的单引号 JSON
                return {
                    "description": result.get("description", ""),
                    "strengths": result.get("strengths", []) if isinstance(result.get("strengths"), list) else [],
                }
            except Exception as e:
                raise Exception(f"JSON 解析失败：{content}, 错误：{e}")


class AIGenerateService:
    """AI 生成服务"""
    
    def __init__(self):
        self.primary_service = PRIMARY_SERVICE
        self.fallback_service = FALLBACK_SERVICE
    
    async def generate_model_description(self, model: dict) -> Optional[dict]:
        """生成单个模型的描述和优势
        
        Args:
            model: 模型数据字典
        
        Returns:
            Optional[dict]: 生成的描述和优势，失败返回 None
        """
        model_name = model.get("name", "未知")
        provider = model.get("provider", "未知")
        
        try:
            prompt = generate_prompt(model)
            
            # 优先使用 Agnes
            try:
                result = await call_ai(prompt, self.primary_service)
                logger.info(f"✅ {model_name} ({provider}) - Agnes 生成成功")
                return result
            except Exception as agnes_error:
                # Agnes 失败，切换到 GLM
                logger.warning(f"⚠️ {model_name} - Agnes 失败 ({agnes_error})，切换 GLM...")
                try:
                    result = await call_ai(prompt, self.fallback_service)
                    logger.info(f"✅ {model_name} ({provider}) - GLM 生成成功")
                    return result
                except Exception as glm_error:
                    # GLM 也失败
                    logger.error(f"❌ {model_name} - GLM 也失败 ({glm_error})")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ {model_name} ({provider}) - 生成失败：{e}")
            return None
    
    async def get_pending_models(self, limit: int = 100) -> list:
        """获取待处理的模型（description 或 strengths 为空）
        
        Args:
            limit: 限制数量
        
        Returns:
            list: 待处理的模型列表
        """
        pool = await get_pool()
        if pool is None:
            logger.error("数据库未连接")
            return []
        
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    id, name, slug, provider, logo, description, category,
                    pricing_input, pricing_output, pricing_unit,
                    context_window, multimodal, strengths,
                    benchmark_score, released, url, free_tier, updated_at
                FROM spider_ai_models
                WHERE description IS NULL OR description = '' OR strengths IS NULL OR array_length(strengths, 1) IS NULL
                ORDER BY updated_at DESC
                LIMIT $1
                """,
                limit
            )
            
            # 转换为字典列表
            models = []
            for row in rows:
                model = dict(row)
                # strengths 数组转列表
                if model.get("strengths"):
                    model["strengths"] = list(model["strengths"])
                models.append(model)
            
            return models
    
    async def update_model_description(self, model_id: str, description: str, strengths: list):
        """更新模型描述和优势到数据库
        
        Args:
            model_id: 模型 ID
            description: 描述
            strengths: 优势列表
        """
        pool = await get_pool()
        if pool is None:
            logger.error("数据库未连接")
            return
        
        async with pool.acquire() as conn:
            # PostgreSQL 使用数组类型
            await conn.execute(
                """
                UPDATE spider_ai_models SET
                    description = $1,
                    strengths = $2,
                    is_published = TRUE,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = $3
                """,
                description, strengths, model_id
            )
    
    async def generate_batch(self, limit: int = 100) -> dict:
        """批量生成模型描述和优势
        
        Args:
            limit: 每次处理的最大数量
        
        Returns:
            dict: 统计信息 {success: int, fail: int, agnes_success: int, glm_success: int}
        """
        logger.info("=" * 60)
        logger.info("🤖 开始 AI 生成任务")
        logger.info(f"AI 服务：Agnes 2.5 Flash (主) + GLM-4.7 Flash (备)")
        logger.info("=" * 60)
        
        # 检查配置
        agnes_key = os.environ.get("AGNES_API_KEY")
        glm_key = os.environ.get("GLM_API_KEY")
        if not agnes_key and not glm_key:
            logger.error("AI 服务配置未就绪，请设置 AGNES_API_KEY 或 GLM_API_KEY")
            return {"success": 0, "fail": 0, "error": "配置缺失"}
        
        # 获取待处理的模型
        pending_models = await self.get_pending_models(limit)
        
        if not pending_models:
            logger.info("✅ 没有待处理的模型")
            return {"success": 0, "fail": 0}
        
        logger.info(f"📋 待处理模型：{len(pending_models)} 个")
        
        # 批量生成
        success_count = 0
        fail_count = 0
        agnes_success = 0
        glm_success = 0
        
        for i, model in enumerate(pending_models):
            model_name = model.get("name", "未知")
            provider = model.get("provider", "未知")
            
            logger.info(f"[{i + 1}/{len(pending_models)}] 生成：{model_name} ({provider})")
            
            result = await self.generate_model_description(model)
            
            if result:
                # 更新数据库
                await self.update_model_description(
                    model["id"],
                    result["description"],
                    result["strengths"]
                )
                
                success_count += 1
                # 统计使用哪个服务成功的（通过日志判断）
            else:
                fail_count += 1
                logger.error(f"❌ 跳过：{model_name}")
            
            # 避免 API 限流
            await asyncio.sleep(0.5)
        
        logger.info("=" * 60)
        logger.info("📊 生成统计：")
        logger.info(f"   成功：{success_count} 个")
        logger.info(f"   失败：{fail_count} 个")
        logger.info(f"   有效结果：{success_count} 个")
        logger.info("=" * 60)
        
        return {
            "success": success_count,
            "fail": fail_count,
        }


# 全局单例
_ai_generate_service: Optional[AIGenerateService] = None


def get_ai_generate_service() -> AIGenerateService:
    """获取 AI 生成服务单例"""
    global _ai_generate_service
    if _ai_generate_service is None:
        _ai_generate_service = AIGenerateService()
    return _ai_generate_service


async def run_ai_generate_task(limit: int = 100) -> dict:
    """运行 AI 生成任务（便捷函数）
    
    Args:
        limit: 每次处理的最大数量
    
    Returns:
        dict: 统计信息
    """
    service = get_ai_generate_service()
    return await service.generate_batch(limit)
