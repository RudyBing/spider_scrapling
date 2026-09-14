"""OpenRouter AI 模型数据采集爬虫

从 OpenRouter 公开 API（/api/v1/models）采集 AI 模型数据，
解析并转换为 spider_ai_models 表结构。

相比 LiteLLM，OpenRouter 额外提供准确的：
- released（发布日期，由 created 时间戳转换）
- benchmark_score（Artificial Analysis 智能指数）
- description（官方英文简介，直接入库，免 AI 生成）

用法:
    运行爬虫后，返回的数据可用于入库 spider_ai_models 表
    - 已存在的数据：更新采集字段（有值的 released/benchmark/description 也会更新）
    - strengths 和 free_tier/url 留空，等后续 AI 生成
"""
from loguru import logger
from .base import SpiderBase
import json
import math
from datetime import datetime, timezone

# OpenRouter 免费公开接口，无需认证
API_URL = "https://openrouter.ai/api/v1/models"

HEADERS = {
    "accept": "application/json",
    "accept-language": "zh-CN,zh;q=0.9",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}


class OpenRouterSpider(SpiderBase):
    name = "openrouter"
    fetcher_type = "http"
    schedule_interval = "daily"

    start_urls = [API_URL]

    async def crawl(self) -> list[dict]:
        """从 OpenRouter API 采集 AI 模型数据

        流程：
        1. 拉取 /api/v1/models JSON
        2. 解析模型数据，转换为 spider_ai_models 表结构
        3. strengths / free_tier / url 留空，等后续 AI 生成

        返回：
            list[dict]: 待入库的模型数据列表
        """
        current_url = self.start_urls[0]
        all_models = []

        logger.info(f"[{self.name}] 正在从 OpenRouter API 拉取数据...")
        html = await self.fetch(current_url, headers=HEADERS, fetcher_type="requests")
        if not html:
            logger.warning("无内容，停止")
            return []

        try:
            payload = json.loads(html)
        except json.JSONDecodeError as e:
            logger.error(f"[{self.name}] OpenRouter 返回非 JSON：{e}")
            return []

        records = payload.get("data", [])
        logger.info(f"[{self.name}] ✅ 拉取成功，共 {len(records)} 个模型条目")

        for record in records:
            model = self._parse_model(record)
            if model:
                all_models.append(model)

        logger.info(f"[{self.name}] 共抓取 {len(all_models)} 条 ai 模型，已分析完成")
        return all_models

    def _parse_model(self, record: dict) -> dict | None:
        """将 OpenRouter 模型记录转换为 spider_ai_models 表结构

        注意：id/slug 与 litellm 保持一致（直接使用 openrouter_id，不加前缀），
        以便 upsert 能命中同一行，实现字段补充而非重复插入。

        Args:
            record: OpenRouter /api/v1/models 中的单条模型字典

        Returns:
            符合 spider_ai_models 表结构的字典，或 None（解析失败时）
        """
        try:
            openrouter_id = record.get("id", "")
            if not openrouter_id:
                return None

            # id/slug 与 litellm 对齐：直接使用 openrouter_id（如 "openai/gpt-4o"）
            # 不加前缀，确保 upsert 能命中 litellm 已存的同一行
            raw_id = openrouter_id
            # slug：仅替换斜杠和冒号，保留点号以区分不同版本（如 claude-3-opus-4-5-v1:0 vs claude-3-opus-4-5）
            slug = openrouter_id.lower().replace("/", "-").replace(":", "-")

            # 厂商取自 id 前缀（openai/anthropic/google/...）
            provider_id = openrouter_id.split("/")[0]
            provider = self._normalize_provider(provider_id)

            # 名称去掉 "OpenAI: " 前缀，保留模型名
            name = record.get("name", openrouter_id)
            if ": " in name:
                name = name.split(": ", 1)[-1]

            # 价格（USD / token），转 /1M tokens
            pricing = record.get("pricing") or {}
            pricing_input = self._format_price(pricing.get("prompt"))
            pricing_output = self._format_price(pricing.get("completion"))

            # 上下文窗口
            context_length = record.get("context_length")
            context_window = f"{context_length} tokens" if context_length else "-"

            # 多模态：输入模态含 image/image+file 等
            architecture = record.get("architecture") or {}
            input_modalities = architecture.get("input_modalities") or []
            multimodal = bool(input_modalities and any(
                m in ("image", "video", "file") for m in input_modalities
            ))

            # released：created 时间戳转日期
            released = None
            created = record.get("created")
            if created:
                try:
                    released = datetime.fromtimestamp(
                        int(created), tz=timezone.utc
                    ).strftime("%Y-%m-%d")
                except (ValueError, TypeError, OSError):
                    released = None

            # benchmark_score：Artificial Analysis 智能指数
            benchmark_score = None
            benchmarks = (record.get("benchmarks") or {}).get("artificial_analysis") or {}
            if benchmarks.get("intelligence_index") is not None:
                benchmark_score = benchmarks["intelligence_index"]

            # description：OpenRouter 官方简介，直接入库（免 AI 生成）
            description = record.get("description", "") or ""

            # composite_score：从 openrouter 可用字段推导（coverage 固定 6 分）
            composite_score = self._compute_composite_score(record)

            model = {
                "id": raw_id,
                "name": name,
                "slug": slug,
                "provider": provider,
                "logo": "",
                "description": description,  # 官方简介，直接入库
                "category": self._infer_category(name, openrouter_id, input_modalities),
                "pricing_input": pricing_input,
                "pricing_output": pricing_output,
                "pricing_unit": "USD / 1M tokens",
                "context_window": context_window,
                "multimodal": multimodal,
                "strengths": [],  # 留空，等 AI 生成
                "benchmark_score": benchmark_score,  # AA 智能指数
                "released": released,  # 由 created 转换
                "composite_score": composite_score,  # openrouter 可计算维度
                # "url": "",  # OpenRouter 无官方 url，留空
                # "free_tier": "",  # 留空，等 AI 生成
                "updated_at": datetime.now().strftime("%Y-%m-%d")
            }
            return model
        except Exception as e:
            logger.error(f"[{self.name}] 解析模型 {record.get('id', '?')} 失败：{e}")
            return None

    def _normalize_provider(self, provider_id: str) -> str:
        """将 OpenRouter 厂商 id 规范为显示名"""
        provider_map = {
            "openai": "OpenAI",
            "anthropic": "Anthropic",
            "google": "Google",
            "meta": "Meta",
            "mistralai": "Mistral",
            "deepseek": "DeepSeek",
            "qwen": "Alibaba",
            "z-ai": "Zhipu AI",
            "x-ai": "xAI",
            "cohere": "Cohere",
            "amazon": "Amazon",
            "nvidia": "NVIDIA",
            "microsoft": "Microsoft",
            "moonshotai": "Moonshot AI",
            "minimax": "MiniMax",
        }
        return provider_map.get(provider_id, provider_id.capitalize())

    def _infer_category(self, name: str, openrouter_id: str, input_modalities: list) -> str:
        """推断模型类别（与 litellm 保持一致）

        注意：OpenRouter 无 mode 字段，优先根据 input_modalities 判断多模态，
        其次再用关键字匹配。litellm 的 mode 字段优先级高于关键字，此处与之对齐。
        """
        text = f"{name} {openrouter_id}".lower()

        # 先检查实际输入模态（优先级最高，与 litellm 的 mode 字段对齐）
        if input_modalities:
            if any(m in ("image", "video", "file") for m in input_modalities):
                return "multimodal"
            if "audio" in input_modalities:
                return "audio"
            if "text" not in input_modalities and len(input_modalities) == 1:
                return input_modalities[0]

        # 视频生成（关键字检测，但排在 modalities 之后）
        if any(kw in text for kw in ["sora", "veo", "kling"]):
            return "video"
        # 图像生成
        if any(kw in text for kw in ["dall-e", "imagen", "flux", "diffusion"]):
            return "image"
        # 音频模型
        if any(kw in text for kw in ["whisper", "tts", "speech"]):
            return "audio"
        # 代码专用
        if any(kw in text for kw in ["codex", "codestral", "coder", "code-"]):
            return "code"
        # 开源模型
        if any(kw in text for kw in ["llama", "qwen", "glm", "gemma", "deepseek"]):
            return "open-source"
        # 默认文本
        return "text"

    def _compute_composite_score(self, record: dict) -> int:
        """计算模型综合评分（0-100），参考 litellm 的同名函数。

        openrouter 数据限制：
        - 无跨 provider 统计，coverage 维度固定为 6 分（1/5）
        - 其余维度从 supported_parameters / input_modalities / context_length / pricing 推导

        评分维度与权重（总计 100）：
        - context（25）：log2(context_length)，128k 接近满分
        - multimodal（10）：input_modalities 含 image/video/file
        - tool（15）：supported_parameters 含 tools/tool_choice（8）+ structured_outputs（7）
        - reasoning（10）：supported_parameters 含 reasoning
        - coverage（6）：openrouter 单 provider，固定 6 分
        - free（10）：prompt 和 completion 均为 0
        """
        # context
        ctx = record.get("context_length") or 0
        if ctx and ctx > 0:
            context_pts = min(math.log2(ctx) / 17.0, 1.0) * 25
        else:
            context_pts = 0.0

        # multimodal
        input_mods = (record.get("architecture") or {}).get("input_modalities") or []
        multimodal_pts = 10.0 if any(m in ("image", "video", "file") for m in input_mods) else 0.0

        # tool：supported_parameters 为字符串列表
        supported_params = record.get("supported_parameters") or []
        param_names = [p.lower() if isinstance(p, str) else "" for p in supported_params]
        tool_pts = 0.0
        if "tools" in param_names or "tool_choice" in param_names:
            tool_pts += 8.0
        if "structured_outputs" in param_names or "response_format" in param_names:
            tool_pts += 7.0

        # reasoning
        reasoning_pts = 10.0 if "reasoning" in param_names else 0.0

        # coverage：openrouter 单 provider，固定 6 分（满分 30 的 1/5）
        coverage_pts = 6.0

        # free：pricing.prompt 和 pricing.completion 均为 0
        pricing = record.get("pricing") or {}
        try:
            prompt_cost = float(pricing.get("prompt") or 0)
            completion_cost = float(pricing.get("completion") or 0)
        except (TypeError, ValueError):
            prompt_cost = completion_cost = 0.0
        # 图像/音频类模型不参与 free 评分（与 litellm 逻辑一致）
        if any(m in ("image_generation", "audio_transcription", "audio_speech", "embedding")
               for m in input_mods):
            free_pts = 0.0
        else:
            free_pts = 10.0 if (prompt_cost == 0 and completion_cost == 0) else 0.0

        return max(0, min(round(context_pts + multimodal_pts + tool_pts + reasoning_pts
                                + coverage_pts + free_pts), 100))

    def _format_price(self, cost_value) -> str:
        """格式化价格字符串（USD/token → /1M tokens）

        Args:
            cost_value: OpenRouter 中的每 token 价格（如 "0.00001"）

        Returns:
            格式化后的价格字符串（如 '$10.00 / 1M tokens'）或空串
        """
        if cost_value is None or cost_value == "":
            return ""

        try:
            cost = float(cost_value)
            if cost == 0:
                return "$0.00 / 1M tokens"
            cost_per_million = cost * 1_000_000

            # 免费模型直接显示 0
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

        由于 OpenRouter 使用 crawl() 直接返回数据，此方法不需要使用
        """
        return []


SPIDER_REGISTRY = {
    "openrouter": OpenRouterSpider()
}