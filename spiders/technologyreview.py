"""TechnologyReview AI 新闻列表爬虫示例

基类提供 fetch() / _fetch_http() / _fetch_stealth() 通用请求方法，
子类直接调用，无需重复实现请求逻辑。
"""
from services.news_analyzer import NewsAnalyzer
from loguru import logger
from scrapling import Selector
from .base import SpiderBase
from datetime import datetime
import hashlib
import json

HEADERS = {
    "accept": "application/json",
    "accept-language": "zh-CN,zh;q=0.9",
    "accept-encoding": "gzip, deflate",
    "cache-control": "no-cache",
    "origin": "https://www.technologyreview.com",
    "pragma": "no-cache",
    "priority": "u=1, i",
    "referer": "https://www.technologyreview.com/",
    "sec-ch-ua": "\"Google Chrome\";v=\"125\", \"Chromium\";v=\"125\", \"Not.A/Brand\";v=\"24\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

class TechnologyReviewSpider(SpiderBase):
    name = "technologyreview"
    fetcher_type = "http"
    schedule_interval = "daily"

    start_urls = [
        "https://wp.technologyreview.com/wp-json/irving/v1/data/topic_feed?page=#page#&orderBy=date&topic=9&requestType=topic",
    ]
    max_page = 1
    async def parse(self, url: str) -> dict | None:
        """解析新闻详情页，返回新闻数据字典"""
        html = await self.fetch(url)
        if not html:
            logger.warning(f"{url} 请求内容错误")
            return None
        page = Selector(content=html)
        title = page.xpath("//h1/text()").get()
        publish_time = page.xpath("//div[contains(@class, 'publishDate')]/text()").get()
        if publish_time:
            publish_time = datetime.strptime(publish_time, "%B %d, %Y")
            # publish_time = publish_time.strftime("%Y-%m-%d %H:%M:%S")
        content_list = page.xpath("//div[starts-with(@class, 'contentBody')]//p/text()").getall()
        content = "".join(content_list)
        # topic_list = page.xpath("//div[@class='tc23-post-relevant-terms__terms']/a/text()").getall()
        
        # 生成唯一标识
        url_hash = hashlib.md5(url.encode()).hexdigest()
        news_id = f"news-{url_hash[:24]}"  # 限制长度为 32 字符
        slug = url.split("/")[-2] if "/" in url else url_hash[:16]
        
        news_data = {
            "id": news_id,
            "slug": slug,
            "title": title,
            "title_cn": None,
            "content": content,
            "content_cn": None,
            "source": self.name,
            "original_url": url,
            "published_at": publish_time,
            "category": "行业动态",
            # "tags": topic_list,
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
        page_url = self.start_urls[0]
        all_news = []

        for page_num in range(1, self.max_page + 1):
            current_url = page_url.replace("#page#", str(page_num))
            logger.info(f"[{self.name}] 第 {page_num} 页：{current_url}")
            html = await self.fetch(current_url, headers=HEADERS, fetcher_type="requests")
            if not html:
                logger.warning(f"[{self.name}] 第 {page_num} 页无内容，停止")
                break
            json_data = json.loads(html)
            news_list = json_data[0].get("feedPosts", []) if json_data else []
            for news_data in news_list:
                news_url = news_data["config"].get("link") or ''
                logger.info(f"解析新闻：{news_url}")
                news_data = await self.parse(news_url)
                if news_data:
                    print(news_data)
                    return
                    # all_news.append(news_data)
            logger.info(f"page {page_num} parse {len(news_list)} news")
            if  page_num >= self.max_page:
                logger.warning(f"[{self.name}] 已达最大页数限制，停止")
                break
            if not news_list:
                break
        
        logger.info(f"[{self.name}] 共抓取 {len(all_news)} 条新闻")
        return all_news


SPIDER_REGISTRY = {
    "technologyreview": TechnologyReviewSpider()
}
