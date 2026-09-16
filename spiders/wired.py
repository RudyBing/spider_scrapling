"""WIRED AI 新闻爬虫

基类提供 fetch() / _fetch_http() / _fetch_stealth() 通用请求方法，
子类直接调用，无需重复实现请求逻辑。

WIRED 首页有 Cloudflare 防护，但 RSS 端点开放，
采用 RSS 拉取文章链接 + HTTP 抓取详情页的策略。
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
    "accept-language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "referer": "https://www.wired.com/",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}

# WIRED 公开 RSS 源（Cloudflare 不拦截）
RSS_URLS = [
    "https://www.wired.com/feed/tag/ai/latest/rss",
    "https://www.wired.com/feed/category/artificial-intelligence/rss",
]


class WiredSpider(SpiderBase):
    name = "wired"
    fetcher_type = "http"
    schedule_interval = "daily"

    start_urls = [
        "https://www.wired.com/",
    ]

    # ===== RSS 列表抓取 =====

    async def _fetch_rss(self) -> list[dict]:
        """通过 RSS 获取最新文章链接和元数据（合并多个源，去重）"""
        import requests as sync_requests
        all_items: dict[str, dict] = {}  # key=link，去重
        for rss_url in RSS_URLS:
            try:
                resp = sync_requests.get(rss_url, headers=HEADERS, timeout=15)
                resp.raise_for_status()
                items = self._parse_rss(resp.text)
                logger.debug(f"[{self.name}] RSS {rss_url} 返回 {len(items)} 条")
                for item in items:
                    link = item.get("link", "").strip()
                    if link and link not in all_items:
                        all_items[link] = item
            except Exception as e:
                logger.warning(f"[{self.name}] RSS 请求失败 {rss_url}: {e}")
        return list(all_items.values())

    def _parse_rss(self, xml: str) -> list[dict]:
        """解析 RSS XML，提取文章元数据"""
        import xml.etree.ElementTree as ET
        # 注册 media 命名空间
        ET.register_namespace("media", "http://search.yahoo.com/mrss/")
        try:
            root = ET.fromstring(xml)
        except Exception:
            # fallback：正则提取
            return self._parse_rss_regex(xml)

        items = root.findall(".//item")
        results = []
        for item in items:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            description = (item.findtext("description") or "").strip()
            creator = (item.findtext("{http://purl.org/dc/elements/1.1/}creator") or "").strip()
            results.append({
                "title": title,
                "link": link,
                "pubDate": pub_date,
                "description": description,
                "creator": creator,
            })
        return results

    def _parse_rss_regex(self, xml: str) -> list[dict]:
        """正则 fallback 解析 RSS"""
        results = []
        pattern = r'<item[^>]*>.*?<title>(.*?)</title>.*?<link>(.*?)</link>.*?<pubDate>(.*?)</pubDate>.*?<description>(.*?)</description>'
        for m in re.finditer(pattern, xml, re.DOTALL):
            results.append({
                "title": m.group(1).strip(),
                "link": m.group(2).strip(),
                "pubDate": m.group(3).strip(),
                "description": m.group(4).strip(),
                "creator": "",
            })
        return results

    # ===== 详情页解析 =====

    async def parse(self, url: str, rss_meta: dict = None) -> dict | None:
        """解析新闻详情页，返回新闻数据字典"""
        html = await self.fetch(url)
        if not html:
            logger.warning(f"{url} 请求内容错误")
            return None
        page = Selector(content=html)

        # 标题
        title = page.xpath("//h1/text()").get()
        if not title:
            title = page.xpath("//meta[@property='og:title']/@content").get()
        if not title:
            logger.warning(f"{url} 未找到标题")
            return None
        title = title.strip()

        # 发布时间
        publish_time = None
        rss_date = rss_meta.get("pubDate") if rss_meta else None
        if rss_date:
            try:
                publish_time = datetime.strptime(rss_date, "%a, %d %b %Y %H:%M:%S %z")
                publish_time = publish_time.replace(tzinfo=None)
            except ValueError:
                pass
        if not publish_time:
            publish_time = page.xpath("//time[@datetime]/@datetime").get()
            if publish_time:
                try:
                    publish_time = datetime.fromisoformat(publish_time)
                    if publish_time.tzinfo is not None:
                        publish_time = publish_time.replace(tzinfo=None)
                except ValueError:
                    publish_time = None
        if not publish_time:
            publish_time = page.xpath("//meta[@property='article:published_time']/@content").get()
            if publish_time:
                try:
                    publish_time = datetime.fromisoformat(publish_time.replace("Z", "+00:00"))
                    publish_time = publish_time.replace(tzinfo=None)
                except ValueError:
                    publish_time = None

        # 正文
        paragraphs = page.xpath(
            "//article//div[contains(@class,'content')]/p//text()"
        ).getall()
        if not paragraphs:
            paragraphs = page.xpath("//article//p//text()").getall()
        if not paragraphs:
            paragraphs = page.xpath(
                "//div[contains(@class,'article-body')]//p//text()"
            ).getall()
        content = "\n".join(p.strip() for p in paragraphs if p.strip()).strip()

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

        analysis_result = NewsAnalyzer.analyze(news_data)
        news_data.update(analysis_result)
        return news_data

    # ===== 爬虫主入口 =====

    async def crawl(self) -> list[dict]:
        """通过 RSS 拉取文章链接，再逐一解析详情页"""
        all_news = []
        end_date = datetime.now() - timedelta(days=30)

        logger.info(f"[{self.name}] 拉取 RSS（{len(RSS_URLS)} 个源）")
        rss_items = await self._fetch_rss()
        if not rss_items:
            logger.warning(f"[{self.name}] RSS 无数据，停止")
            return all_news

        logger.info(f"[{self.name}] RSS 共获取 {len(rss_items)} 条文章")

        for idx, item in enumerate(rss_items):
            url = item["link"]
            if not url or "http" not in url:
                continue
            logger.info(f"[{self.name}] 解析第 {idx + 1}/{len(rss_items)} 条：{url}")
            news_data = await self.parse(url, rss_meta=item)
            if news_data:
                all_news.append(news_data)
                if news_data.get("published_at") and news_data["published_at"] < end_date:
                    logger.info(f"article published_at = {news_data['published_at']}")
                    logger.info(f"[{self.name}] 共抓取 {len(all_news)} 条新闻")
                    return all_news

        logger.info(f"[{self.name}] 共抓取 {len(all_news)} 条新闻")
        return all_news


SPIDER_REGISTRY = {
    "wired": WiredSpider()
}
