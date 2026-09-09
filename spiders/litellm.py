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

            # 构建模型数据（description 和 strengths 留空，等后续 AI 生成）
            model_slug = model_key.lower().replace("/", "-").replace(":", "-")
            ##TODO 新增hotness
            '''
            hotness = (
                provider_count * 10           # 多少个提供商提供此模型
                + min(log2(context_window) * 2, 20)  # 上下文越长分越高
                + vision_score * 3            # supports_vision=True +3
                + function_calling * 2        # +2
                + tool_choice * 2             # +2
                + reasoning * 3               # +3
                + free_tier * 10              # 免费 +10
                - deprecation_penalty         # 已废弃 -50
            )
            '''
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
            m = re.match(r"^(\d+)([a-z]+)$", tok)
            if m:
                suffix = m.group(2).upper()
                if suffix in _NUM_SUFFIXES or len(suffix) == 1 and suffix in "BKMT":
                    return [m.group(1), suffix]
            return [tok.capitalize()]

        # 需要剥离的地域/区域前缀
        _REGION_PREFIXES = frozenset([
            "us", "eu", "ap-southeast", "ap-northeast",
            "us-gov-west", "us-gov-east",
        ])

        name = re.sub(r"[-_:/\\.]+", " ", name_base)
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

        支持两种模式：
        1. PROVIDER_URL_MAP 中有模板的，直接替换 {slug}
        2. 否则查 PROVIDER_URL_FORMAT，按 pattern 特殊处理 model_key（如 OpenRouter、DeepInfra 等）
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
        if not fmt:
            return ""

        base = fmt.get("base", "")
        pattern = fmt.get("pattern", "")
        if not base:
            return ""

        # 对需要剥离 provider 前缀的平台处理 key
        if pattern in ("model", "owner/model", "models"):
            key = self._strip_provider_prefix(model_key, provider)
        else:
            key = model_key

        if pattern == "model":
            # OpenRouter: /model/{author}/{model_name}
            return f"{base}/model/{key}" if key else base

        if pattern == "owner/model":
            # DeepInfra: /{owner}/{model_name}
            return f"{base}/{key}" if "/" in key else base

        if pattern == "models/{provider}/{slug}":
            # Fal AI: /models/{provider}/{model_id}
            return f"{base}/models/{key}"

        if pattern == "models":
            # Replicate: /{owner}/{model_name}
            return f"{base}/{key}" if "/" in key else f"{base}/models"

        # list 类型：直接返回基础 URL
        return base

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
