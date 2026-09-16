"""量子位 (QbitAI) AI 新闻爬虫

基类提供 fetch() / _fetch_http() / _fetch_stealth() 通用请求方法，
子类直接调用，无需重复实现请求逻辑。
"""
from services.news_analyzer import NewsAnalyzer
from loguru import logger
from scrapling import Selector
from .base import SpiderBase
from datetime import datetime, timedelta
import hashlib
import re


HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "referer": "https://www.qbitai.com/",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}


class QbitAISpider(SpiderBase):
    name = "qbitai"
    fetcher_type = "http"
    schedule_interval = "daily"

    start_urls = [
        "https://www.qbitai.com/",
    ]
    max_page = 20

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
            logger.warning(f"{url} 未找到标题")
            return None
        title = title.strip()

        # 发布时间：优先 <time> 标签，备用 meta og:article
        publish_date = page.xpath("//span[@class='date']/text()").get()
        publish_time = page.xpath("//span[@class='time']/text()").get()
        if publish_date:
            publish_time = f"{publish_date} {publish_time}" if publish_time else f"{publish_date} 00:00:000"
            try:
                publish_time = datetime.strptime(publish_time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    publish_time = datetime.fromisoformat(publish_time)
                except ValueError:
                    publish_time = None
        if not publish_time:
            return None

        # 正文：主内容区 <article class="entry-content">
        paragraphs = page.xpath("//p[@data-track]//text()").getall()
        if not paragraphs:
            paragraphs = page.xpath("//p[contains(@class, 'js_darkmode')]//text()").getall()
        if not paragraphs:
            paragraphs = page.xpath("//div[@class='zhaiyao']/following-sibling::p//text()").getall()
        content = "\n".join(p for p in paragraphs if p.strip()).strip()
        if not content:
            logger.warning(f"{url} 未找到正文")
            return None
        tags = page.xpath("//div[@class='tags']/a/text()").getall()
        # 生成唯一标识
        url_hash = hashlib.md5(url.encode()).hexdigest()
        news_id = f"news-{url_hash[:24]}"
        slug = re.search(r'(\d+)\.html', url).group(1) if re.search(r'(\d+)\.html', url) else url_hash[:16]

        news_data = {
            "id": news_id,
            "slug": slug,
            "title": title,
            "content": content,
            "source": '量子位',
            "original_url": url,
            "published_at": publish_time,
            "tags": tags,
            "category": "行业动态",
            "sentiment": "neutral",
            "hotness": 50,
            "language": "zh",
        }

        # 立即分析新闻
        analysis_result = NewsAnalyzer.analyze(news_data)
        news_data.update(analysis_result)

        return news_data

    async def crawl(self) -> list[dict]:
        """翻页抓取：WordPress 传统分页 /page/{N}"""
        all_news = []
        end_date = datetime.now() - timedelta(days=30)

        for page_num in range(1, self.max_page + 1):
            current_url = f"https://www.qbitai.com/page/{page_num}" if page_num > 1 else self.start_urls[0]
            logger.info(f"[{self.name}] 第 {page_num} 页：{current_url}")
            html = await self.fetch(current_url, headers=HEADERS)
            if not html:
                logger.warning(f"[{self.name}] 第 {page_num} 页无内容，停止")
                break
            page = Selector(content=html)
            # 列表页提取文章链接：<h2 class="entry-title"><a href="...">
            news_links = page.xpath("//h4/a[@href]/@href").getall()
            if not news_links:
                logger.warning(f"[{self.name}] 第 {page_num} 页未找到新闻链接，停止")
                break
            for news_url in news_links:
                if 'http' not in news_url:
                    news_url = f"https://www.qbitai.com{news_url}"
                news_data = await self.parse(news_url)
                if news_data:
                    all_news.append(news_data)
                    if news_data.get("published_at") and news_data["published_at"] < end_date:
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
    "qbitai": QbitAISpider()
}
