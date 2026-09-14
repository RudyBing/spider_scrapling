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
import math
import re
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

    # litellm_provider 字段值 -> 友好名称映射
    PROVIDER_MAP = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "azure": "Azure",
        "azure_ai": "Azure AI",
        "bedrock": "AWS Bedrock",
        "bedrock_converse": "AWS Bedrock",
        "google_ai": "Google AI",
        "vertex_ai": "Google Vertex AI",
        "cohere": "Cohere",
        "mistral": "Mistral",
        "google": "Google",
        "meta": "Meta",
        "deepseek": "DeepSeek",
        "qwen": "Alibaba Qwen",
        "zhipuai": "Zhipu AI",
        "replicate": "Replicate",
        "fireworks_ai": "Fireworks AI",
        "groq": "Groq",
        "perplexity": "Perplexity",
        "nvidia": "NVIDIA",
        "voyage": "Voyage AI",
        "huggingface": "Hugging Face",
        "together": "Together AI",
        "sambanova": "SambaNova",
        "minimax": "MiniMax",
        "aiml": "AI/ML",
        "yi": "01.AI",
        "stepfun": "StepFun",
        "suno": "Suno",
    }

    # 按 model_key 前缀匹配兜底（在 litellm_provider 缺失时使用）
    KEY_PREFIX_MAP = {
        "gpt-": "OpenAI",
        "o1-": "OpenAI",
        "o3-": "OpenAI",
        "o4-": "OpenAI",
        "codex-": "OpenAI",
        "dall-e": "OpenAI",
        "whisper-": "OpenAI",
        "tts-": "OpenAI",
        "sora": "OpenAI",
        "claude-": "Anthropic",
        "gemini-": "Google",
        "palm-": "Google",
        "imagen-": "Google",
        "gemma-": "Google",
        "veo": "Google",
        "llama-": "Meta",
        "meta-llama": "Meta",
        "deepseek-": "DeepSeek",
        "qwen": "Alibaba Qwen",
        "glm-": "Zhipu AI",
        "codestral": "Mistral",
        "mistral-": "Mistral",
        "mixtral-": "Mistral",
        "flux": "Black Forest Labs",
        "midjourney": "Midjourney",
        "stable-diffusion": "Stability AI",
        "kling": "Kuaishou",
        "nova-": "AWS Bedrock",
        "amazon.": "AWS Bedrock",
        "ai21.": "AI21",
        "writer.": "Writer",
        "us.writer.": "Writer",
        "stability.": "Stability AI",
    }

    # provider 友好名称 -> 官方文档/模型页面 URL 模板（{slug} 为占位符）
    PROVIDER_URL_MAP: dict[str, str] = {
        "OpenAI":      "https://platform.openai.com/docs/models/{slug}",
        "Anthropic":   "https://docs.anthropic.com/en/docs/about-claude/models",
        "Google":      "https://ai.google.dev/gemini-api/docs/models",
        "Google AI":   "https://ai.google.dev/gemini-api/docs/models",
        "Google Vertex AI": "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Azure":       "https://learn.microsoft.com/en-us/azure/ai-services/openai/models",
        "Azure AI":    "https://learn.microsoft.com/en-us/azure/ai-services/openai/models",
        "AWS Bedrock": "https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html",
        "DeepSeek":    "https://platform.deepseek.com/api-docs/zh-cn/pricing",
        "Zhipu AI":    "https://open.bigmodel.cn/dev/api/language-models",
        "Meta":        "https://llama.meta.com/",
        "Mistral":     "https://docs.mistral.ai/getting-started/models/models_overview/",
        "Groq":        "https://console.groq.com/docs/models",
        "Cohere":      "https://docs.cohere.com/docs/models",
        "NVIDIA":      "https://build.nvidia.com/explore/ai-foundation-models",
        "Voyage AI":   "https://docs.voyageai.com/docs/embeddings",
        "Hugging Face": "https://huggingface.co/models",
        "Together AI": "https://docs.together.ai/docs/inference-models",
        "Fireworks AI": "https://fireworks.ai/models",
        "OpenRouter":  "",
        "AI/ML":       "",
    }

    # 需要特殊处理 slug 格式的 provider 映射（如添加 / 分隔符等）
    PROVIDER_URL_FORMAT: dict[str, dict[str, str]] = {
        "OpenRouter":  {"pattern": "model", "base": "https://openrouter.ai"},
        "Deepinfra":   {"pattern": "owner/model", "base": "https://deepinfra.com"},
        "Fal Ai":      {"pattern": "models/{provider}/{slug}", "base": "https://fal.ai"},
        "Replicate":   {"pattern": "models", "base": "https://replicate.com"},
        "Novita":      {"pattern": "list", "base": "https://novita.ai"},
        "Perplexity":  {"pattern": "list", "base": "https://docs.perplexity.ai"},
        "Vercel Ai Gateway": {"pattern": "list", "base": ""},
        "Sambanova":   {"pattern": "list", "base": "https://cloud.sambanova.ai"},
        "Groq":        {"pattern": "list", "base": "https://console.groq.com"},
        "Cohere":      {"pattern": "list", "base": "https://docs.cohere.com"},
        "NVIDIA":      {"pattern": "list", "base": "https://build.nvidia.com"},
        "Voyage AI":   {"pattern": "list", "base": "https://docs.voyageai.com"},
        "Mistral":     {"pattern": "list", "base": "https://docs.mistral.ai"},
        "Google":      {"pattern": "list", "base": "https://ai.google.dev"},
        "Google AI":   {"pattern": "list", "base": "https://ai.google.dev"},
        "Google Vertex AI": {"pattern": "list", "base": "https://cloud.google.com"},
        "Azure":       {"pattern": "list", "base": "https://learn.microsoft.com"},
        "Azure AI":    {"pattern": "list", "base": "https://learn.microsoft.com"},
        "AWS Bedrock": {"pattern": "list", "base": "https://docs.aws.amazon.com"},
        "DeepSeek":    {"pattern": "list", "base": "https://platform.deepseek.com"},
        "Zhipu AI":    {"pattern": "list", "base": "https://open.bigmodel.cn"},
        "Meta":        {"pattern": "list", "base": "https://llama.meta.com"},
        "Anthropic":   {"pattern": "list", "base": "https://docs.anthropic.com"},
        "OpenAI":      {"pattern": "list", "base": "https://platform.openai.com"},
        "Hugging Face": {"pattern": "list", "base": "https://huggingface.co"},
        "Together AI": {"pattern": "list", "base": "https://docs.together.ai"},
        "Fireworks AI": {"pattern": "list", "base": "https://fireworks.ai"},
    }

    # provider 兜底 URL 映射（当 PROVIDER_URL_MAP 和 PROVIDER_URL_FORMAT 均无匹配时使用）
    PROVIDER_FALLBACK_URLS: dict[str, str] = {
        # 大平台 / 云服务商
        "Vercel Ai Gateway":       "https://sdk.vercel.ai/providers/community-providers",
        "Gemini":                  "https://ai.google.dev/gemini-api/docs/models",
        "Databricks":              "https://docs.databricks.com/en/generative-ai/external-models/index.html",
        "Nebius":                  "https://docs.nebius.com/studio/models",
        "Xai":                     "https://docs.x.ai/docs/models",
        "Vertex Ai-Language-Models": "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Anthropic Models": "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Mistral Models":  "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Llama Models":    "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Embedding-Models": "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Image-Models":    "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Video-Models":    "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Qwen Models":     "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Deepseek Models": "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Openai Models":   "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Ai21 Models":     "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Minimax Models":  "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Moonshot Models": "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Text-Models":     "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Vertex Ai-Zai Models":      "https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        "Dashscope":               "https://help.aliyun.com/zh/model-studio/developer-reference/token-api",
        "Qwencloud":               "https://help.aliyun.com/zh/model-studio/developer-reference/token-api",
        "Qwen Ai Platform":        "https://help.aliyun.com/zh/model-studio/developer-reference/token-api",
        "Oci":                     "https://docs.oracle.com/en-us/iaas/Content/AI/TF/home.htm",
        "Snowflake":               "https://docs.snowflake.com/en/user-guide/snowflake-ai/framework-models",
        "Wandb":                   "https://docs.wandb.ai/guides/prompts/library-providers",
        "Deepgram":                "https://developers.deepgram.com/docs/models",
        "Github Copilot":          "https://github.com/features/copilot",
        "Anyscale":                "https://docs.endpoints.anyscale.com/",
        "AI/ML":                   "https://deepinfra.com/models",
        # 中小平台
        "Cloudflare":              "https://developers.cloudflare.com/api/agents/large-language-models/",
        "Watsonx":                 "https://www.ibm.com/products/watsonx-ai",
        "Bedrock Mantle":          "https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html",
        "Ollama":                  "https://ollama.com/library",
        "Moonshot":                "https://platform.moonshot.cn/",
        "Stability":               "https://platform.stability.ai/docs/api-reference",
        "Lambda Ai":               "https://lambdalabs.com/",
        "Scaleway":                "https://www.scaleway.com/en/docs/ai-data/studio/models/",
        "Cohere Chat":             "https://docs.cohere.com/docs/models",
        "Fireworks Ai-Embedding-Models": "https://fireworks.ai/models",
        "Gigachat":                "https://developers.sber.ru/gigachat",
        "Crusoe":                  "https://cloud.crusoe.com/",
        "Elevenlabs":              "https://elevenlabs.io/",
        "Lemonade":                "https://lemonade.ai/",
        "Volcengine":              "https://www.volcengine.com/docs/6218",
        "Baseten":                 "https://docs.baseten.ai/",
        "Ai21":                    "https://docs.ai21.com/",
        "Libertai":                "https://libertai.com/",
        "Friendliai":              "https://friendliai.medium.com/",
        "MiniMax":                 "https://www.minimaxi.com/",
        "Tensormesh":              "https://tensormesh.com/",
        "Cerebras":                "https://cerebras.ai/",
        "Publicai":                "https://publicai.ai/",
        "Black Forest Labs":       "https://blackforestlabs.ai/",
        "Hyperbolic":              "https://hyperbolic.xyz/",
        "Nscale":                  "https://nscale.ai/",
        "Zai":                     "https://z.ai/",
        "Llamagate":               "https://llamagate.com/",
        "Ovhcloud":                "https://www.ovhcloud.com/",
        "Chatgpt":                 "https://platform.openai.com/",
        "Runwayml":                "https://runwayml.com/",
        "Gradient Ai":             "https://gradient.ai/",
        "Gmi":                     "https://gminference.com/",
        "Pinstripes":              "https://pinstripes.ai/",
        "Text-Completion-Openai":  "https://platform.openai.com/",
        "Palm":                    "https://ai.google.dev/gemini-api/docs/models",
        "Sagemaker":               "https://docs.aws.amazon.com/sagemaker/",
        "Meta Llama":              "https://llama.meta.com/",
        "Parallel Ai":             "https://parallel.ai/",
        "Aws Polly":               "https://docs.aws.amazon.com/polly/",
        "Azure Text":              "https://learn.microsoft.com/en-us/azure/ai-services/",
        "Nvidia Nim":              "https://build.nvidia.com/",
        "V0":                      "https://v0.ai/",
        "Tencent":                 "https://cloud.tencent.com/document/product/1729",
        "Cognition":               "https://cognition.ai/",
        "Assemblyai":              "https://www.assemblyai.com/",
        "Nlp Cloud":               "https://nlpcloud.io/",
        "Codestral":               "https://console.mistral.ai/models/",
        "Apiserpent":              "https://apiserpent.ai/",
        "Featherless Ai":          "https://featherless.ai/",
        "Inception":               "https://inception.ai/",
        "Morph":                   "https://morph.sh/",
        "Reducto":                 "https://reducto.ai/",
        "Recraft":                 "https://www.recraft.ai/",
        "Scx-Ai":                  "https://scx.ai/",
        "Linkup":                  "https://linkup.ai/",
        "Tavily":                  "https://tavily.com/",
        "Text-Completion-Codestral": "https://console.mistral.ai/models/",
        "Soniox":                  "https://soniox.com/",
        "Darkbloom":               "https://darkbloom.ai/",
        "Dataforseo":              "https://dataforseo.com/",
        "Exa Ai":                  "https://exa.ai/",
        "Firecrawl":               "https://firecrawl.dev/",
        "Searxng":                 "https://searxng.org/",
        "Serper":                  "https://serper.dev/",
        "Agentcore":               "https://agentcore.ai/",
        "Bing Grounding":          "https://www.bing.com/",
        "Tinyfish":                "https://tinyfish.io/",
        "Nimble":                  "https://nimble.ai/",
        "Google Pse":              "https://programmablesearchengine.google.com/",
        "Jina Ai":                 "https://jina.ai/",
        "Text-Completion-Inception": "https://inception.ai/",
        "You Com":                 "https://you.com/",
        "Sarvam":                  "https://sarvam.ai/",
        "Duckduckgo":              "https://duckduckgo.com/",
        "Amazon Nova":             "https://docs.aws.amazon.com/bedrock/latest/userguide/models-unsupported.html",
        "Heroku":                  "https://devcenter.heroku.com/articles/model-farm",
    }


    async def crawl(self) -> list[dict]:
        """从 LiteLLM 采集 AI 模型数据

        流程：
        1. 从 LiteLLM GitHub 拉取 model_prices_and_context_window.json
        2. 全量预统计每个"基模型"被多少家提供商支持（用于 composite_score）
        3. 解析模型数据，转换为 spider_ai_models 表结构
        4. description 和 strengths 留空，等后续 AI 生成

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

        # 2. 全量预统计：每个"基模型"被多少家提供商支持（用于 composite_score）
        provider_count_map = self._precompute_provider_count(json_data)

        # 3. 解析并转换数据
        for model_key, model_data in json_data.items():
            # 跳过特殊键
            if model_key in self.EXCLUDE_KEYS:
                continue

            provider_count = provider_count_map.get(self._model_base_name(model_key), 1)
            model = self._parse_model(model_key, model_data, provider_count)
            if model:
                all_models.append(model)
                # print(model)

        logger.info(f"[{self.name}] 共抓取 {len(all_models)} 条 ai 模型，已分析完成")
        return all_models

    def _model_base_name(self, model_key: str) -> str:
        """从 model_key 提取"基模型"名称（剥离首段 provider 前缀）

        例如 'azure/gpt-4o' -> 'gpt-4o'；'gpt-4o' -> 'gpt-4o'。
        用于跨 provider 归并统计同一底层模型被多少家提供商支持。
        """
        if "/" in model_key:
            return model_key.split("/", 1)[1]
        return model_key

    def _precompute_provider_count(self, json_data: dict) -> dict:
        """全量预统计：每个基模型被多少家不同提供商支持

        返回 dict[base_model_name -> int]，供 _parse_model 计算 composite_score 使用。
        provider 以 litellm_provider 字段（或 model_key 兜底提取）为准。
        """
        base_providers: dict[str, set] = {}
        for model_key, model_data in json_data.items():
            if model_key in self.EXCLUDE_KEYS:
                continue
            base = self._model_base_name(model_key)
            provider = self._extract_provider(model_key, model_data)
            base_providers.setdefault(base, set()).add(provider)
        return {base: len(providers) for base, providers in base_providers.items()}

    def _compute_composite_score(self, model_data: dict, provider_count: int) -> int:
        """计算模型综合评分（0-100 整数，clamp 到 [0,100]）

        基于可获得的客观信号，从能力、覆盖面、可负担三个维度加权：
        - context:    上下文长度（log2 对数尺度，最多 25 分，约 128k 接近满分）
        - multimodal: 支持视觉/音频/视频输入（0-10 分）
        - tool:       函数调用/tool_choice 二选一（8 分）+ 结构化输出（7 分），合计最高 15 分
        - reasoning:  支持推理（0-10 分）
        - coverage:   被多少家提供商支持（归一化 provider_count，最多 30 分）
        - free:       按 token 计价且输入输出均为 0（0-10 分；图像/音频类模型不参与此项）
        权重合计 100，避免任意无上界累加。
        """
        # context：log2(max_input_tokens) 对数缩放，17 -> 128k 取 0.85
        max_tokens = model_data.get("max_input_tokens") or 0
        context_pts = 0.0
        if max_tokens and max_tokens > 0:
            log_ctx = math.log2(max_tokens)
            context_pts = min(log_ctx / 17.0, 1.0) * 25

        # multimodal
        multimodal_pts = 10.0 if (
            model_data.get("supports_vision")
            or model_data.get("supports_audio_input")
            or model_data.get("supports_video_input")
        ) else 0.0

        # tool：function_calling 与 tool_choice 二选一（避免重复计分），再加结构化输出
        tool_pts = 0.0
        if model_data.get("supports_function_calling") or model_data.get("supports_tool_choice"):
            tool_pts += 8.0
        if model_data.get("supports_response_schema"):
            tool_pts += 7.0

        # reasoning
        reasoning_pts = 10.0 if model_data.get("supports_reasoning") else 0.0

        # coverage：provider_count 越多覆盖越高，5 家及以上取满分
        coverage_pts = min(provider_count, 5) / 5.0 * 30

        # free：仅对按 token 计价的文本/多模态模型生效，图像/音频类模型跳过
        mode = model_data.get("mode", "")
        if mode in ("image_generation", "audio_transcription", "audio_speech", "embedding"):
            free_pts = 0.0
        else:
            try:
                in_cost = float(model_data.get("input_cost_per_token") or 0)
                out_cost = float(model_data.get("output_cost_per_token") or 0)
            except (TypeError, ValueError):
                in_cost = out_cost = 0.0
            free_pts = 10.0 if (in_cost == 0 and out_cost == 0) else 0.0

        score = round(
            context_pts + multimodal_pts + tool_pts + reasoning_pts
            + coverage_pts + free_pts
        )
        return max(0, min(score, 100))

    def _parse_model(self, model_key: str, model_data: dict, provider_count: int = 1) -> dict | None:
        """将 LiteLLM 模型数据转换为 spider_ai_models 表结构

        Args:
            model_key: 模型 ID（如 'gpt-4', 'claude-3-opus'、'azure/gpt-4o' 等）
            model_data: LiteLLM 中的模型数据字典
            provider_count: 该基模型被多少家提供商支持（用于 composite_score）

        Returns:
            符合 spider_ai_models 表结构的字典，或 None（解析失败时）
        """
        try:
            # 提取模型名称（从 model_key 转换，先剥离 provider 前缀）
            name = self._extract_model_name(model_key)

            # 提取提供商（优先读取 litellm_provider 字段）
            provider = self._extract_provider(model_key, model_data)

            # 提取价格信息（根据不同 mode 使用不同字段）
            pricing_input = self._format_price(model_data.get("input_cost_per_token"), model_data)
            pricing_output = self._format_price(model_data.get("output_cost_per_token"), model_data)

            # 提取上下文窗口
            max_input_tokens = model_data.get("max_input_tokens")
            context_window = f"{max_input_tokens} tokens" if max_input_tokens else "-"

            # 提取多模态支持
            multimodal = bool(model_data.get("supports_vision", False))

            # 综合评分
            composite_score = self._compute_composite_score(model_data, provider_count)

            # 构建模型数据（description 和 strengths 留空，等后续 AI 生成）
            model_slug = model_key.lower().replace("/", "-").replace(":", "-")
            model = {
                "id": model_key,
                "name": name,
                "slug": model_slug,
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
                "url": self._build_model_url(provider, model_key),
                "free_tier": "",
                "composite_score": composite_score,
                "updated_at": datetime.now().strftime("%Y-%m-%d")
            }

            return model
        except Exception as e:
            logger.error(f"[{self.name}] 解析模型 {model_key} 失败：{e}")
            return None

    def _extract_model_name(self, model_key: str) -> str:
        """从 model_key 提取人类可读的模型名称

        策略：
        1. 根据路径段数智能选取 name 基串：
           - 3段 且末段像版本号（如 v1.1、v0.1）→ 合并中间+末段（如 flux-pro/v1.1）
           - 4+段（含维度规格前缀如 1024-x-1024/）→ 取最后一段（已含完整模型名）
           - 其余 → 取最后一段
        2. 用正则将分隔符（-_.:）替换为空格
        3. 数字+大写字母处插入空格（如 70B→70 B）
        4. 对每个 token 进行大小写格式化

        示例：
            'gpt-4' -> 'GPT 4'
            'claude-3-opus' -> 'Claude 3 Opus'
            'azure/gpt-4o' -> 'GPT 4o'
            'aiml/flux-pro/v1.1' -> 'Flux Pro V1 1'
            'fireworks_ai/accounts/fireworks/models/internvl3-38b' -> 'InternVL3 38 B'
            '1024-x-1024/50-steps/bedrock/amazon.nova-canvas-v1:0' -> 'Amazon Nova Canvas V1 0'
        """
        segs = model_key.split("/")

        # ── 步骤1：选取 name 基串 ──────────────────────────────────────────
        VERSION_RE = re.compile(r"^v?\d+[\d.-]*")  # 匹配 "v1"、"v1.1"、"0.1" 等

        if len(segs) == 3:
            # 3段：始终合并中间+末段（中间=模型族，末段=具体型号/版本）
            # 如 aiml/flux-pro/v1.1 -> "flux-pro v1.1"；aiml/flux/dev -> "flux dev"
            name_base = f"{segs[-2]} {segs[-1]}"
        elif len(segs) >= 4:
            # 4+段：判断末段是否为通用功能描述词
            _FUNC_SUFFIXES = frozenset([
                "text-to-image", "image-generation", "text-generation",
                "image-to-image", "tts", "speech", "audio",
            ])
            if segs[-1].lower() in _FUNC_SUFFIXES:
                # 末段是功能词，取倒数第二段作为主模型名
                name_base = segs[-2]
            else:
                # 末段已含完整模型名（如 Llama-2-70b-chat-hf, amazon.nova-canvas-v1:0）
                name_base = segs[-1]
        else:
            # 2段：直接取末段
            name_base = segs[-1]

        # ── 步骤2-4：格式化 ───────────────────────────────────────────────
        # 已知缩写
        _ABBREV_MAP = {
            "gpt": "GPT", "ai": "AI", "api": "API", "llm": "LLM",
            "vl": "VL", "cv": "CV", "ocr": "OCR", "nlp": "NLP",
            "stt": "STT", "tts": "TTS", "rlhf": "RLHF", "lora": "LoRA", "rag": "RAG",
            "internvl": "InternVL",
        }
        _NUM_SUFFIXES = frozenset(["B", "KB", "MB", "GB", "TB", "MHZ", "GHZ"])

        def _format_token(tok: str) -> list[str]:
            t_lower = tok.lower()
            for abbr, formatted in _ABBREV_MAP.items():
                if t_lower == abbr:
                    return [formatted]
                if t_lower.startswith(abbr) and t_lower[len(abbr):].isdigit():
                    return [formatted + t_lower[len(abbr):]]
            if tok.isdigit():
                return [tok]
            # 数字在前字母在后（35b -> 35 B）
            m = re.match(r"^(\d+)([a-z]+)$", tok)
            if m:
                suffix = m.group(2).upper()
                if suffix in _NUM_SUFFIXES or len(suffix) == 1 and suffix in "BKMT":
                    return [m.group(1), suffix]
            # 字母在前数字在后的混合结构（a3b -> A3B，整体大写不拆分）
            if re.match(r"^([a-z]+)\d+[a-z]+$", tok):
                return [tok.upper()]
            return [tok.capitalize()]

        # 需要剥离的地域/区域前缀
        _REGION_PREFIXES = frozenset([
            "us", "eu", "ap-southeast", "ap-northeast",
            "us-gov-west", "us-gov-east",
        ])

        # 仅替换分隔符，保留小数点（避免将 3.7 拆成 3 7）
        name = re.sub(r"[-_:/\\]+", " ", name_base)
        # 数字→大写字母之间插入空格（如 GPT4→GPT 4，但不破坏 qwen3.7）
        name = re.sub(r"(\d)([A-Z])", r"\1 \2", name)

        parts = [p for p in name.split() if p]
        # 过滤掉地域前缀词（如 "us", "eu" 等单独出现的词）
        parts = [p for p in parts if p.lower() not in _REGION_PREFIXES]
        result = []
        for part in parts:
            result.extend(_format_token(part))
        return " ".join(result)

    def _extract_provider(self, model_key: str, model_data: dict) -> str:
        """提取提供商名称

        优先级：litellm_provider 字段 > model_key 前缀匹配 > 返回 Unknown
        """
        # 优先从 litellm_provider 字段读取
        litellm_provider = model_data.get("litellm_provider", "")
        if litellm_provider:
            mapped = self.PROVIDER_MAP.get(litellm_provider)
            if mapped:
                return mapped
            return litellm_provider.replace("_", " ").title()

        # 降级：从 model_key 前缀匹配
        key_lower = model_key.lower()
        for prefix, provider in self.KEY_PREFIX_MAP.items():
            if key_lower.startswith(prefix):
                return provider

        return "Unknown"

    def _strip_provider_prefix(self, model_key: str, provider: str) -> str:
        """从 model_key 中剥离 provider 前缀，避免 URL 路径重复"""
        p = provider.lower().replace(" ", "_")
        if model_key.lower().startswith(p + "/"):
            return model_key[len(p) + 1:]
        return model_key

    def _build_model_url(self, provider: str, model_key: str) -> str:
        """根据 provider 和 model_key 构造官方文档 URL

        支持三种模式，按优先级从低到高：
        1. PROVIDER_URL_MAP 中有模板的，直接替换 {slug}
        2. 否则查 PROVIDER_URL_FORMAT，按 pattern 特殊处理 model_key
        3. 以上均无匹配时，查 PROVIDER_FALLBACK_URLS 兜底
        """
        # 大小写不敏感查找 PROVIDER_URL_MAP
        template = next(
            (v for k, v in self.PROVIDER_URL_MAP.items() if k.lower() == provider.lower()),
            "",
        )
        if template:
            return template.replace("{slug}", model_key)

        # 大小写不敏感查找 PROVIDER_URL_FORMAT
        fmt = next(
            (v for k, v in self.PROVIDER_URL_FORMAT.items() if k.lower() == provider.lower()),
            {},
        )
        if fmt:
            base = fmt.get("base", "")
            pattern = fmt.get("pattern", "")
            if not base:
                pass  # base 为空，继续尝试兜底
            elif pattern == "model":
                # OpenRouter: /model/{author}/{model_name}
                key = self._strip_provider_prefix(model_key, provider)
                return f"{base}/model/{key}" if key else base
            elif pattern == "owner/model":
                # DeepInfra: /{owner}/{model_name}
                key = self._strip_provider_prefix(model_key, provider)
                return f"{base}/{key}" if "/" in key else base
            elif pattern == "models/{provider}/{slug}":
                # Fal AI: /models/{provider}/{model_id}
                return f"{base}/models/{key}" if (key := self._strip_provider_prefix(model_key, provider)) else base
            elif pattern == "models":
                # Replicate: /{owner}/{model_name}
                return f"{base}/{model_key}" if "/" in model_key else f"{base}/models"
            else:
                # list 类型：直接返回基础 URL
                return base

        # 兜底：PROVIDER_FALLBACK_URLS
        fallback = next(
            (v for k, v in self.PROVIDER_FALLBACK_URLS.items() if k.lower() == provider.lower()),
            "",
        )
        return fallback

    def _infer_category(self, model_key: str, model_data: dict) -> str:
        """推断模型类别

        优先级：mode 字段 > model_key 关键字匹配 > supports_vision > 默认文本
        """
        mode = model_data.get("mode", "")
        key_lower = model_key.lower()

        # 1. 优先根据 mode 字段判断
        if mode == "image_generation":
            return "image"
        if mode == "audio_transcription":
            return "audio"
        if mode == "audio_speech":
            return "audio"
        if mode == "embedding":
            return "embedding"
        if mode == "rerank":
            return "rerank"
        if mode == "search":
            return "search"

        # 2. 根据 model_key 关键字推断
        # 图像生成
        if any(kw in key_lower for kw in ["dall-e", "imagen", "flux", "midjourney", "stable-diffusion", "gpt-image", "nova-canvas"]):
            return "image"
        # 视频生成
        if any(kw in key_lower for kw in ["sora", "veo", "kling", "nova-reel"]):
            return "video"
        # 音频模型
        if any(kw in key_lower for kw in ["whisper", "tts-", "chattts", "nova-sonic"]):
            return "audio"
        # 嵌入模型
        if any(kw in key_lower for kw in ["embed", "titan-embed"]):
            return "embedding"
        # 重排序模型
        if any(kw in key_lower for kw in ["rerank"]):
            return "rerank"
        # 代码专用模型
        if any(kw in key_lower for kw in ["codex", "codestral", "deepseek-coder"]):
            return "code"
        # 多模态模型（有 vision 支持）
        if model_data.get("supports_vision") or model_data.get("supports_video_input"):
            return "multimodal"
        # 开源模型
        if any(kw in key_lower for kw in ["llama", "qwen", "glm-", "gemma", "intern"]):
            return "open-source"

        # 默认为文本模型
        return "text"

    def _format_price(self, cost_value, model_data: dict = None) -> str:
        """格式化价格字符串，兼容多种计价方式

        支持的字段：
        - input_cost_per_token / output_cost_per_token: 每 token 价格（默认）
        - output_cost_per_image: 图像生成按张计价
        - output_cost_per_pixel: 图像生成按像素计价（如 DALL-E 2）
        - input_cost_per_second / output_cost_per_second: 音频按秒计价

        Args:
            cost_value: LiteLLM 中的价格值
            model_data: 完整的模型数据字典（用于判断计价方式）

        Returns:
            格式化后的价格字符串
        """
        if cost_value is None:
            return ""

        try:
            cost = float(cost_value)
        except (TypeError, ValueError):
            return ""

        # 图像生成：output_cost_per_image（按张计价）
        if model_data and model_data.get("mode") == "image_generation":
            if "output_cost_per_image" in model_data:
                return f"${cost:.2f} / image"
            if "output_cost_per_pixel" in model_data:
                pixels = 1024 * 1024
                return f"${cost * pixels:.2f} / image (1024x1024)"
            # image token 计价（如 azure/gpt-image-1 的 output_cost_per_image_token）
            if "output_cost_per_image_token" in model_data:
                cost_per_million = cost * 1_000_000
                return f"${cost_per_million:.2f} / 1M image tokens"
            return ""  # mode=image_generation 但缺少价格字段

        # 图像生成：output_cost_per_pixel（按像素计价，如 DALL-E 2）
        if model_data and "output_cost_per_pixel" in model_data:
            # 计算 1024x1024 图像的价格
            pixels = 1024 * 1024
            return f"${cost * pixels:.2f} / image (1024x1024)"

        # 音频转录/语音：per_second
        if model_data and ("input_cost_per_second" in model_data or "output_cost_per_second" in model_data):
            cost_per_hour = cost * 3600
            return f"${cost_per_hour:.2f} / hour"

        # 默认：per_token，转换为每 1M tokens 价格
        cost_per_million = cost * 1_000_000

        if cost_per_million >= 1:
            return f"${cost_per_million:.2f} / 1M tokens"
        elif cost_per_million >= 0.01:
            return f"${cost_per_million:.4f} / 1M tokens"
        else:
            return f"${cost_per_million:.6f} / 1M tokens"

    def parse(self, html: str, url: str = None) -> list[dict]:
        """解析 HTML 内容（基类要求实现的抽象方法）

        由于 LiteLLM 使用 crawl() 直接返回数据，此方法不需要使用
        """
        return []


SPIDER_REGISTRY = {
    "litellm": LiteLLMSpider()
}