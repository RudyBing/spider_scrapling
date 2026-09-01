"""新闻分析服务

提供新闻热度计算、情感分析、自动分类等功能。
"""
from datetime import datetime, timezone
from loguru import logger
import re


class NewsAnalyzer:
    """新闻分析器 - 提供热度、情感、分类等分析功能"""
    
    # ========== 分类关键词 ==========
    CATEGORY_KEYWORDS = {
        '产品发布': [
            'release', 'launch', 'announce', 'unveil', 'introduce', 'debuts',
            '发布', '推出', '上线', '问世', '亮相'
        ],
        '价格调整': [
            'price', 'cost', 'pricing', 'subscription', 'fee', 'charge',
            'discount', 'cut', 'increase', 'raise', 'expensive', 'cheap',
            '价格', '费用', '降价', '涨价', '折扣', '收费', '订阅'
        ],
        '技术突破': [
            'breakthrough', 'research', 'paper', 'study', 'discovery',
            'innovation', 'novel', 'advance', 'improve', 'performance',
            'accuracy', 'efficiency', 'benchmark', 'state-of-the-art',
            '技术', '突破', '研究', '论文', '创新', '进展', '性能', '准确率'
        ],
        '行业动态': [
            'partnership', 'acquisition', 'merger', 'investment', 'funding',
            'series', 'round', 'acquired', 'invest', 'collaborate', 'deal',
            'regulation', 'policy', 'law', 'lawsuit', 'ban', 'investigation',
            '行业', '合作', '投资', '融资', '收购', '并购', '监管', '政策', '诉讼'
        ],
        '更新迭代': [
            'update', 'upgrade', 'version', 'improve', 'enhance', 'new feature',
            'release note', 'changelog', 'patch', 'fix', 'bug',
            '更新', '升级', '版本', '改进', '优化', '修复', '新功能'
        ],
    }
    
    # ========== 情感分析关键词 ==========
    POSITIVE_WORDS = [
        # 英文
        'breakthrough', 'impressive', 'powerful', 'best', 'excellent',
        'amazing', 'outstanding', 'revolutionary', 'game-changer', 'leading',
        'innovative', 'superior', 'advanced', 'successful', 'win', 'growth',
        'improve', 'enhance', 'optimize', 'accelerate', 'boost',
        'positive', 'promising', 'exciting', 'remarkable', 'significant',
        # 中文
        '突破', '强大', '优秀', '出色', '领先', '创新', '成功', '增长',
        '提升', '优化', '加速', ' boost', '积极', '令人兴奋', '显著',
        '革命性', '颠覆性', '重磅', '首发', '首创'
    ]
    
    NEGATIVE_WORDS = [
        # 英文
        'problem', 'issue', 'fail', 'failure', 'error', 'bug', 'crash',
        'delay', 'postpone', 'cancel', 'cut', 'reduce', 'layoff', 'fire',
        'lawsuit', 'investigation', 'ban', 'fine', 'penalty', 'scandal',
        'controversy', 'criticism', 'complaint', 'concern', 'risk', 'threat',
        'worst', 'terrible', 'disappointing', 'poor', 'weak', 'slow',
        # 中文
        '问题', '失败', '错误', '崩溃', '延迟', '取消', '裁员', '诉讼',
        '调查', '罚款', '丑闻', '争议', '批评', '投诉', '担忧', '风险',
        '最差', '糟糕', '失望', '薄弱', '缓慢', '下滑', '衰退'
    ]
    
    # ========== 模型名称映射 ==========
    MODEL_PATTERNS = {
        # OpenAI
        'gpt-4': ['gpt-4', 'gpt4', 'GPT-4'],
        'gpt-4-turbo': ['gpt-4-turbo', 'gpt-4-turbo-preview'],
        'gpt-4o': ['gpt-4o', 'gpt-4o-mini'],
        'gpt-3.5-turbo': ['gpt-3.5', 'gpt-3.5-turbo'],
        # Anthropic
        'claude-3-opus': ['claude-3-opus', 'claude 3 opus', 'opus'],
        'claude-3-sonnet': ['claude-3-sonnet', 'claude 3 sonnet', 'sonnet'],
        'claude-3-haiku': ['claude-3-haiku', 'claude 3 haiku', 'haiku'],
        'claude-3.5-sonnet': ['claude-3.5-sonnet', 'claude 3.5 sonnet', 'claude-3-5-sonnet'],
        # Google
        'gemini-pro': ['gemini-pro', 'gemini pro', 'gemini 1.0'],
        'gemini-ultra': ['gemini-ultra', 'gemini ultra', 'gemini 1.5'],
        # Meta
        'llama-3': ['llama-3', 'llama 3', 'llama3'],
        'llama-2': ['llama-2', 'llama 2', 'llama2'],
        # Mistral
        'mistral-large': ['mistral-large', 'mistral large'],
        'mistral-medium': ['mistral-medium', 'mistral medium'],
        'mistral-small': ['mistral-small', 'mistral small'],
        # AI21
        'jurassic-2': ['jurassic-2', 'jurassic 2'],
        # Cohere
        'command-r': ['command-r', 'command r', 'command-r-plus'],
        # 其他
        'sora': ['sora'],
        'midjourney': ['midjourney', 'mj'],
        'stable-diffusion': ['stable-diffusion', 'stable diffusion', 'sd'],
    }
    
    @classmethod
    def calculate_hotness(cls, published_at: datetime, title: str = "", 
                          content: str = "", tags: list = None) -> int:
        """
        计算新闻热度分数 (0-100)
        
        算法：
        1. 基础分 50 分
        2. 时间衰减：越新的新闻分数越高
        3. 关键词加成：包含热门关键词加分
        4. 标签加成：包含热门标签加分
        
        Args:
            published_at: 新闻发布时间
            title: 新闻标题
            content: 新闻内容
            tags: 新闻标签列表
            
        Returns:
            热度分数 (0-100)
        """
        # 确保 published_at 是带时区的 datetime
        if published_at.tzinfo is None:
            # 假设为 UTC 时间
            published_at = published_at.replace(tzinfo=timezone.utc)
        
        # 计算时间差（小时）
        now = datetime.now(timezone.utc)
        time_diff = now - published_at
        hours_since_published = time_diff.total_seconds() / 3600
        
        # 1. 基础分
        hotness = 50
        
        # 2. 时间衰减（最多 ±30 分）
        # 24 小时内：+30 分
        # 24-48 小时：+20 分
        # 48-72 小时：+10 分
        # 72 小时以上：每天 -1 分，最多 -30 分
        if hours_since_published < 24:
            hotness += 30
        elif hours_since_published < 48:
            hotness += 20
        elif hours_since_published < 72:
            hotness += 10
        else:
            days_old = hours_since_published / 24
            time_penalty = min(30, days_old * 1)  # 每天 -1 分，最多 -30 分
            hotness -= time_penalty
        
        # 3. 标题/内容关键词加成（最多 +20 分）
        text = f"{title} {content}".lower()
        hot_keywords = [
            'breaking', 'exclusive', 'first', 'new', 'just',
            '重磅', '独家', '首发', '最新', '刚刚'
        ]
        for keyword in hot_keywords:
            if keyword in text:
                hotness += 2
        
        # 4. 标签加成（最多 +10 分）
        if tags:
            hot_tags = ['AI', 'LLM', 'GPT', 'Claude', 'Gemini', '突破', '发布']
            for tag in tags:
                if any(ht in str(tag) for ht in hot_tags):
                    hotness += 1
        
        # 限制在 0-100 范围内
        return max(0, min(100, int(hotness)))
    
    @classmethod
    def analyze_sentiment(cls, title: str, content: str = "") -> str:
        """
        分析新闻情感倾向
        
        Args:
            title: 新闻标题
            content: 新闻内容
            
        Returns:
            'positive' | 'neutral' | 'negative'
        """
        text = f"{title} {content}".lower()
        
        # 统计正面和负面词数量
        positive_count = sum(1 for word in cls.POSITIVE_WORDS if word.lower() in text)
        negative_count = sum(1 for word in cls.NEGATIVE_WORDS if word.lower() in text)
        
        # 计算情感倾向
        # 需要差值达到 2 个词以上才判定为有明显倾向
        if positive_count > negative_count + 1:
            return 'positive'
        elif negative_count > positive_count + 1:
            return 'negative'
        else:
            return 'neutral'
    
    @classmethod
    def categorize_news(cls, title: str, content: str = "", 
                       existing_category: str = None) -> str:
        """
        自动分类新闻
        
        Args:
            title: 新闻标题
            content: 新闻内容
            existing_category: 现有分类（如果匹配度不高则保留）
            
        Returns:
            分类名称
        """
        text = f"{title} {content}".lower()
        
        # 统计每个分类的匹配度
        category_scores = {}
        for category, keywords in cls.CATEGORY_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword.lower() in text)
            category_scores[category] = score
        
        # 找到最佳匹配
        best_category = max(category_scores, key=category_scores.get)
        best_score = category_scores[best_category]
        
        # 如果没有匹配到任何关键词，保留原有分类
        if best_score == 0 and existing_category:
            return existing_category
        
        # 如果匹配度不高（只有 1-2 个词），谨慎处理
        if best_score <= 2 and existing_category:
            return existing_category
        
        return best_category
    
    @classmethod
    def extract_related_models(cls, title: str, content: str = "") -> list:
        """
        提取新闻中提到的 AI 模型
        
        Args:
            title: 新闻标题
            content: 新闻内容
            
        Returns:
            模型 ID 列表
        """
        text = f"{title} {content}"
        related_models = []
        
        for model_id, patterns in cls.MODEL_PATTERNS.items():
            for pattern in patterns:
                # 使用正则表达式匹配，忽略大小写
                if re.search(re.escape(pattern), text, re.IGNORECASE):
                    if model_id not in related_models:
                        related_models.append(model_id)
                    break
        
        return related_models
    
    @classmethod
    def analyze(cls, news_item: dict) -> dict:
        """
        对新闻进行全面分析
        
        Args:
            news_item: 新闻数据字典
            
        Returns:
            包含分析结果的字典
        """
        title = news_item.get('title', '')
        content = news_item.get('content', '') or ''
        published_at = news_item.get('published_at')
        tags = news_item.get('tags', [])
        existing_category = news_item.get('category')
        
        # 确保有发布时间
        if not published_at:
            logger.warning(f"新闻缺少发布时间：{title}")
            published_at = datetime.now()
        
        # 计算热度
        hotness = cls.calculate_hotness(published_at, title, content, tags)
        
        # 情感分析
        sentiment = cls.analyze_sentiment(title, content)
        
        # 自动分类
        category = cls.categorize_news(title, content, existing_category)
        
        # 提取相关模型
        related_models = cls.extract_related_models(title, content)
        
        return {
            'hotness': hotness,
            'sentiment': sentiment,
            'category': category,
            'related_models': related_models if related_models else None
        }
