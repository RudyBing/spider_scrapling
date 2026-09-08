"""LiteLLM AI 模型数据采集爬虫

从 LiteLLM GitHub 仓库采集 model_prices_and_context_window.json，
解析 AI 模型数据并转换为 spider_ai_models 表结构。

用法:
    运行爬虫后，返回的数据可用于入库 spider_ai_models 表
    - 已存在的数据：更新
    - description 和 strengths 留空，等后续 AI 生成
"""
from loguru import logger
from .base import SpiderBase
import json
from datetime import datetime


HEADERS = {
    "accept": "application/json",
    "accept-language": "zh-CN,zh;q=0.9",
    "accept-encoding": "gzip, deflate",
    "cache-control": "no-cache",
    "origin": "https://raw.githubusercontent.com",
    "pragma": "no-cache",
    "priority": "u=1, i",
    "referer": "https://raw.githubusercontent.com/",
    "sec-ch-ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}


class LiteLLMSpider(SpiderBase):
    name = "litellm"
    fetcher_type = "http"
    schedule_interval = "daily"

    start_urls = [
        "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
    ]
    
    # 需要排除的特殊键
    EXCLUDE_KEYS = {"sample_spec", "fallback_generalizations"}

    async def crawl(self) -> list[dict]:
        """从 LiteLLM 采集 AI 模型数据
        
        流程：
        1. 从 LiteLLM GitHub 拉取 model_prices_and_context_window.json
        2. 解析模型数据，转换为 spider_ai_models 表结构
        3. description 和 strengths 留空，等后续 AI 生成
        
        返回：
            list[dict]: 待入库的模型数据列表
        """
        current_url = self.start_urls[0]
        all_models = []
        
        # 1. 拉取 LiteLLM 数据
        logger.info(f"[{self.name}] 正在从 LiteLLM GitHub 拉取价格数据...")
        html = await self.fetch(current_url, headers=HEADERS, fetcher_type="requests")
        if not html:
            logger.warning("无内容，停止")
            return []
        
        json_data = json.loads(html)
        model_count = len([k for k in json_data.keys() if k not in self.EXCLUDE_KEYS])
        logger.info(f"[{self.name}] ✅ 拉取成功，共 {model_count} 个模型条目")
        
        # 2. 解析并转换数据
        for model_key, model_data in json_data.items():
            # 跳过特殊键
            if model_key in self.EXCLUDE_KEYS:
                continue
            
            model = self._parse_model(model_key, model_data)
            if model:
                all_models.append(model)
                print(model)
        
        logger.info(f"[{self.name}] 共抓取 {len(all_models)} 条 ai 模型，已分析完成")
        return all_models
    
    def _parse_model(self, model_key: str, model_data: dict) -> dict | None:
        """将 LiteLLM 模型数据转换为 spider_ai_models 表结构
        
        Args:
            model_key: 模型 ID（如 'gpt-4', 'claude-3-opus' 等）
            model_data: LiteLLM 中的模型数据字典
            
        Returns:
            符合 spider_ai_models 表结构的字典，或 None（解析失败时）
        """
        try:
            # 提取模型名称（从 model_key 转换）
            name = self._extract_model_name(model_key)
            
            # 提取提供商（从 model_key 推断）
            provider = self._extract_provider(model_key)
            
            # 提取价格信息
            pricing_input = self._format_price(model_data.get("input_cost_per_token"))
            pricing_output = self._format_price(model_data.get("output_cost_per_token"))
            
            # 提取上下文窗口
            max_input_tokens = model_data.get("max_input_tokens")
            context_window = f"{max_input_tokens} tokens" if max_input_tokens else "-"
            
            # 提取多模态支持
            multimodal = bool(model_data.get("supports_vision", False))
            
            # 构建模型数据（description 和 strengths 留空，等后续 AI 生成）
            model = {
                "id": model_key,
                "name": name,
                "slug": model_key.lower().replace("/", "-").replace(":", "-"),
                "provider": provider,
                "logo": "",
                "description": "",  # 留空，等后续 AI 生成
                "category": self._infer_category(model_key, model_data),
                "pricing_input": pricing_input,
                "pricing_output": pricing_output,
                "pricing_unit": "",
                "context_window": context_window,
                "multimodal": multimodal,
                "strengths": [],  # 留空，等后续 AI 生成
                "benchmark_score": None,
                "released": None,
                "url": "",
                "free_tier": "",
                "updated_at": datetime.now().strftime("%Y-%m-%d")
            }
            
            return model
        except Exception as e:
            logger.error(f"[{self.name}] 解析模型 {model_key} 失败：{e}")
            return None
    
    def _extract_model_name(self, model_key: str) -> str:
        """从 model_key 提取人类可读的模型名称
        
        示例：
            'gpt-4' -> 'GPT-4'
            'claude-3-opus' -> 'Claude 3 Opus'
            'gemini-2.5-pro' -> 'Gemini 2.5 Pro'
        """
        # 移除常见前缀
        name = model_key
        
        # 驼峰式转换（将连字符转空格，首字母大写）
        parts = name.replace("-", " ").replace("_", " ").split()
        capitalized = []
        for part in parts:
            # 特殊处理数字和缩写
            if part.upper() in ("GPT", "AI", "API", "LLM"):
                capitalized.append(part.upper())
            elif part.isdigit():
                capitalized.append(part)
            else:
                capitalized.append(part.capitalize())
        
        return " ".join(capitalized)
    
    def _extract_provider(self, model_key: str) -> str:
        """从 model_key 推断提供商名称"""
        key_lower = model_key.lower()
        
        # 常见提供商映射
        provider_map = {
            "gpt-": "OpenAI",
            "o1-": "OpenAI",
            "o3-": "OpenAI",
            "o4-": "OpenAI",
            "codex-": "OpenAI",
            "dall-e": "OpenAI",
            "whisper-": "OpenAI",
            "tts-": "OpenAI",
            "claude-": "Anthropic",
            "gemini-": "Google",
            "palm-": "Google",
            "imagen-": "Google",
            "gemma-": "Google",
            "llama-": "Meta",
            "deepseek-": "DeepSeek",
            "qwen": "Alibaba",
            "glm-": "Zhipu AI",
            "codestral": "Mistral",
            "mistral-": "Mistral",
            "mixtral-": "Mistral",
            "flux": "Black Forest Labs",
            "midjourney": "Midjourney",
            "stable-diffusion": "Stability AI",
            "sora": "OpenAI",
            "veo": "Google",
            "kling": "Kuaishou",
        }
        
        for prefix, provider in provider_map.items():
            if key_lower.startswith(prefix):
                return provider
        
        # 默认返回 Unknown 或从 key 推断
        return "Unknown"
    
    def _infer_category(self, model_key: str, model_data: dict) -> str:
        """推断模型类别"""
        key_lower = model_key.lower()
        
        # 图像生成模型
        if any(kw in key_lower for kw in ["dall-e", "imagen", "flux", "midjourney", "stable-diffusion", "gpt-image"]):
            return "image"
        
        # 视频生成模型
        if any(kw in key_lower for kw in ["sora", "veo", "kling"]):
            return "video"
        
        # 音频模型
        if any(kw in key_lower for kw in ["whisper", "tts-", "chattts"]):
            return "audio"
        
        # 代码专用模型
        if any(kw in key_lower for kw in ["codex", "codestral", "deepseek-coder"]):
            return "code"
        
        # 多模态模型（有 vision 支持）
        if model_data.get("supports_vision"):
            return "multimodal"
        
        # 开源模型
        if any(kw in key_lower for kw in ["llama", "qwen", "glm-", "gemma"]):
            return "open-source"
        
        # 默认为文本模型
        return "text"
    
    def _format_price(self, cost_value) -> str:
        """格式化价格字符串
        
        Args:
            cost_value: LiteLLM 中的价格值（如 0.0000015）
            
        Returns:
            格式化后的价格字符串（如 '$0.0015 / 1M tokens'）
        """
        if cost_value is None:
            return ""
        
        try:
            cost = float(cost_value)
            # 转换为每 1M tokens 的价格
            cost_per_million = cost * 1_000_000
            
            if cost_per_million >= 1:
                return f"${cost_per_million:.2f} / 1M tokens"
            elif cost_per_million >= 0.01:
                return f"${cost_per_million:.4f} / 1M tokens"
            else:
                return f"${cost_per_million:.6f} / 1M tokens"
        except (TypeError, ValueError):
            return ""
    
    def parse(self, html: str, url: str = None) -> list[dict]:
        """解析 HTML 内容（基类要求实现的抽象方法）
        
        由于 LiteLLM 使用 crawl() 直接返回数据，此方法不需要使用
        """
        return []


SPIDER_REGISTRY = {
    "litellm": LiteLLMSpider()
}
