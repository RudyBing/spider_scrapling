"""TechCrunch AI 新闻列表爬虫示例

基类提供 fetch() / _fetch_http() / _fetch_stealth() 通用请求方法，
子类直接调用，无需重复实现请求逻辑。
"""
from services.news_analyzer import NewsAnalyzer
from loguru import logger
from scrapling import Selector
from .base import SpiderBase
from datetime import datetime
import hashlib


class TechCrunchSpider(SpiderBase):
    name = "techcrunch"
    fetcher_type = "http"
    schedule_interval = "daily"

    start_urls = [
        "https://techcrunch.com/category/artificial-intelligence/",
    ]
    max_page = 3
    
    async def parse(self, url: str) -> dict | None:
        """解析新闻详情页，返回新闻数据字典"""
        html = await self.fetch(url)
        if not html:
            logger.warning(f"{url} 请求内容错误")
            return None
        page = Selector(content=html)
        title = page.xpath("//h1/text()").get()
        publish_time = page.xpath("//time[@datetime]/@datetime").get()
        if publish_time:
            publish_time = datetime.fromisoformat(publish_time)
            # 移除时区信息，PostgreSQL TIMESTAMP 不带时区
            if publish_time.tzinfo is not None:
                publish_time = publish_time.replace(tzinfo=None)
        content_list = page.xpath("//div[starts-with(@class,'entry-content wp-block-post-content')]/p//text()").getall()
        content = "".join(content_list)
        topic_list = page.xpath("//div[@class='tc23-post-relevant-terms__terms']/a/text()").getall()
        
        # 生成唯一标识
        url_hash = hashlib.md5(url.encode()).hexdigest()
        news_id = f"news-{url_hash[:24]}"  # 限制长度为 32 字符
        slug = url.split("/")[-2] if "/" in url else url_hash[:16]
        
        # 构建基础新闻数据
        news_data = {
            "id": news_id,
            "slug": slug,
            "title": title,
            "title_cn": None,
            "content": content,
            "content_cn": None,
            "source": "techcrunch",
            "original_url": url,
            "published_at": publish_time,
            "category": "行业动态",
            "tags": topic_list,
            "related_models": None,
            "sentiment": "neutral",
            "hotness": 50,
            "language": "en",
            "is_published": False
        }
        # 立即分析新闻（热度计算、情感分析、自动分类、模型提取）
        analysis_result = NewsAnalyzer.analyze(news_data)
        news_data.update(analysis_result)
        logger.debug(
            f"新闻分析完成：{title[:30] if title else 'Unknown'}... "
            f"hotness={news_data.get('hotness')}, "
            f"sentiment={news_data.get('sentiment')}, "
            f"category={news_data.get('category')},"
            f"related_models={news_data.get('related_models')}"
        )
        
        return news_data

    async def crawl(self) -> list[dict]:
        """翻页抓取：调用基类 fetch() 发请求，解析后循环到无下一页"""
        current_url = self.start_urls[0]
        page_num = 1
        all_news = []

        while current_url:
            logger.info(f"[{self.name}] 第 {page_num} 页：{current_url}")
            html = await self.fetch(current_url)
            if not html:
                logger.warning(f"[{self.name}] 第 {page_num} 页无内容，停止")
                break
            page = Selector(content=html)
            next_page_url = page.xpath("//a[contains(@class,'pagination-next')]/@href").get()
            news_list = page.xpath("//h3[@class='loop-card__title']/a[contains(@class,'title')]")
            logger.info(f"page {page_num} parse {len(news_list)} news, next_page_url: {next_page_url}")
            
            for news_node in news_list:
                news_url = news_node.xpath("./@href").get()
                logger.info(f"解析新闻：{news_url}")
                news_data = await self.parse(news_url)
                if news_data:
                    all_news.append(news_data)
            
            if page_num >= self.max_page:
                logger.warning(f"[{self.name}] 已达最大页数限制，停止")
                break
            if next_page_url:
                page_num += 1
                current_url = next_page_url
            else:
                break
        
        logger.info(f"[{self.name}] 共抓取 {len(all_news)} 条新闻，已分析完成")
        return all_news


SPIDER_REGISTRY = {
    "techcrunch": TechCrunchSpider()
}
