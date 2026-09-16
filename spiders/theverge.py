"""TheVerge AI 新闻爬虫

基类提供 fetch() / _fetch_http() / _fetch_stealth() 通用请求方法，
子类直接调用，无需重复实现请求逻辑。
"""
from services.news_analyzer import NewsAnalyzer
from loguru import logger
from scrapling import Selector
from .base import SpiderBase
from datetime import datetime, timedelta
import hashlib
import json
import re


HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "referer": "https://www.theverge.com/",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}


class TheVergeSpider(SpiderBase):
    name = "theverge"
    fetcher_type = "http"
    schedule_interval = "daily"

    start_urls = [
        "https://www.theverge.com/ai-artificial-intelligence",
    ]
    max_page = 10

    async def parse(self, url: str) -> dict | None:
        """解析新闻详情页，返回新闻数据字典"""
        html = await self.fetch(url)
        if not html:
            logger.warning(f"{url} 请求内容错误")
            return None
        page = Selector(content=html)
        # 标题
        title = page.xpath("//h1/text()").get()
        if not title:
            return None
        # 发布时间：优先从 <time datetime="..."> 取 ISO 格式，备用 meta tag
        publish_time = page.xpath("//time[@datetime]/@datetime").get()
        if not publish_time:
            publish_time = page.xpath("//meta[@property='article:published_time']/@content").get()
        if publish_time:
            try:
                publish_time = datetime.fromisoformat(publish_time.replace("Z", "+00:00"))
                publish_time = publish_time.replace(tzinfo=None)  # 统一为 naive datetime
            except ValueError:
                return None

        # 正文：提取 article 内所有段落文本
        paragraphs = page.xpath("//div[contains(@class, 'article-body')]//p//text()").getall()
        content = "\n".join(paragraphs).strip() if paragraphs else ""
        # 生成唯一标识
        url_hash = hashlib.md5(url.encode()).hexdigest()
        news_id = f"news-{url_hash[:24]}"
        slug = url.rstrip("/").split("/")[-1]

        news_data = {
            "id": news_id,
            "slug": slug,
            "title": title,
            "content": content,
            "source": self.name,
            "original_url": url,
            "published_at": publish_time,
            "category": "行业动态",
            "sentiment": "neutral",
            "hotness": 50,
            "language": "en",
        }

        # 立即分析新闻（热度计算、情感分析、自动分类、模型提取）
        analysis_result = NewsAnalyzer.analyze(news_data)
        news_data.update(analysis_result)
        # print(news_data)

        return news_data

    async def crawl(self) -> list[dict]:
        """翻页抓取：调用基类 fetch() 发请求，解析后循环到无下一页"""
        page_url = self.start_urls[0]
        all_news = []

        for page_num in range(1, self.max_page + 1):
            current_url = f"{page_url}/archives/{page_num}" if page_num > 1 else page_url
            logger.info(f"[{self.name}] 第 {page_num} 页：{current_url}")
            html = await self.fetch(current_url, headers=HEADERS)
            if not html:
                logger.warning(f"[{self.name}] 第 {page_num} 页无内容，停止")
                break
            page = Selector(content=html)
            news_urls = page.xpath("//a[contains(@href, '/ai-artificial-intelligence/')]/@href").getall()
            if not news_urls:
                logger.warning(f"[{self.name}] 第 {page_num} 页未找到新闻链接，停止")
                break
            for news_url in news_urls:
                if re.match(r"/ai-artificial-intelligence/\d+/", news_url):
                    if 'http' not in news_url:
                        news_url = f"https://www.theverge.com{news_url}"
                        news_data = await self.parse(news_url)
                        if news_data:
                            all_news.append(news_data)
                            if news_data.get("published_at"):
                                end_date = datetime.now() - timedelta(days=30)
                                naive_published = news_data["published_at"].replace(tzinfo=None)
                                if naive_published < end_date:
                                    logger.info(f"page {page_num} news published_at = {news_data['published_at']}")
                                    logger.info(f"[{self.name}] 共抓取 {len(all_news)} 条新闻")
                                    return all_news
            logger.info(f"page {page_num} 解析完成，累计 {len(all_news)} 条")
            if page_num >= self.max_page:
                logger.warning(f"[{self.name}] 已达最大页数限制，停止")
                break

        logger.info(f"[{self.name}] 共抓取 {len(all_news)} 条新闻")
        return all_news


SPIDER_REGISTRY = {
    "theverge": TheVergeSpider()
}
