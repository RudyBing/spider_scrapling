"""OpenAI Blog AI 新闻爬虫

基类提供 fetch() / _fetch_http() / _fetch_stealth() 通用请求方法，
子类直接调用，无需重复实现请求逻辑。

OpenAI Blog 提供公开 RSS feed，无需反爬，直接抓详情页补充正文。
"""
from services.news_analyzer import NewsAnalyzer
from loguru import logger
from scrapling import Selector
from .base import SpiderBase
from datetime import datetime, timedelta
import hashlib
from email.utils import parsedate_to_datetime


class OpenAISpider(SpiderBase):
    name = "openai"
    fetcher_type = "http"
    schedule_interval = "daily"

    start_urls = [
        "https://openai.com/news/",
    ]
    rss_url = "https://openai.com/news/rss.xml"

    async def parse(self, url: str) -> dict | None:
        """解析新闻详情页，返回新闻数据字典"""
        html = await self.fetch(url)
        if not html:
            logger.warning(f"{url} 请求内容错误")
            return None
        page = Selector(content=html)
        title = page.xpath("//h1/text()").get()
        if not title:
            return None
        publish_time = None
        publish_time_str = page.xpath("//p[@class='text-meta text-primary-100']/text()").get()
        if publish_time_str:
            try:
                dt = datetime.strptime(publish_time_str, "%B %d, %Y")
                publish_time = datetime.fromisoformat(dt.strftime("%Y-%m-%d %H:%M:%S")).replace(tzinfo=None)
            except ValueError:
                pass
        if not publish_time:
            return None

        paragraphs = page.xpath("//div[starts-with(@class, 'col-span-full')]/p//text()").getall()
        content = "\n".join(paragraphs).strip()

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
        analysis_result = NewsAnalyzer.analyze(news_data)
        news_data.update(analysis_result)
        return news_data

    async def crawl(self) -> list[dict]:
        """通过 RSS 获取文章链接，再逐一解析详情页"""
        import requests as sync_requests

        all_news = []
        end_date = datetime.now() - timedelta(days=30)

        logger.info(f"[{self.name}] 拉取 RSS：{self.rss_url}")
        try:
            resp = sync_requests.get(self.rss_url, timeout=15)
            resp.raise_for_status()
            items = self._parse_rss(resp.text)
            logger.info(f"[{self.name}] RSS 返回 {len(items)} 条文章")
        except Exception as e:
            logger.warning(f"[{self.name}] RSS 请求失败: {e}")
            return all_news

        for item in items:
            url = item.get("link", "").strip()
            if not url or "http" not in url:
                continue
            # 日期过滤
            pub_date = item.get("pub_date")
            if pub_date and pub_date < end_date:
                logger.info(f"[{self.name}] 文章过期，停止：{item.get('title')}")
                break

            news_data = await self.parse(url)
            if news_data:
                all_news.append(news_data)
                if news_data.get("published_at") and news_data["published_at"] < end_date:
                    logger.info(f"[{self.name}] 共抓取 {len(all_news)} 条新闻（截止日期已到）")
                    return all_news

        logger.info(f"[{self.name}] 共抓取 {len(all_news)} 条新闻")
        return all_news

    @staticmethod
    def _parse_rss(html: str) -> list[dict]:
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(html)
        except ET.ParseError as e:
            logger.warning(f"RSS 解析失败: {e}")
            return []
        items = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "item":
                title = (elem.find("title") or elem).text or ""
                link_el = elem.find("link")
                link = link_el.text if link_el is not None else ""
                pub = elem.find("pubDate")
                pub_date = None
                if pub is not None and pub.text:
                    try:
                        pub_date = parsedate_to_datetime(pub.text.replace("Z", "+00:00")).replace(tzinfo=None)
                    except Exception:
                        pass
                items.append({"title": title, "link": link, "pub_date": pub_date})
        return items


SPIDER_REGISTRY = {
    "openai": OpenAISpider()
}
