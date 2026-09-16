"""VentureBeat AI 新闻爬虫

基类提供 fetch() / _fetch_http() / _fetch_stealth() 通用请求方法，
子类直接调用，无需重复实现请求逻辑。

VentureBeat 使用 Next.js + Contentful CMS，列表数据通过 Algolia 索引提供，
详情页面为标准 SSR HTML，可直接 HTTP 抓取。
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
    "referer": "https://venturebeat.com/",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}

# Algolia 搜索配置（公开密钥，来自站点前端嵌入）
ALGOLIA_APP_ID = "PDL00DD2OY"
ALGOLIA_API_KEY = "fe200a0ace7ab1f1df0d7682e3511fe6"
ALGOLIA_INDEX = "develop_index_flat"
ALGOLIA_URL = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"
ALGOLIA_HEADERS = {
    "X-Algolia-API-Key": ALGOLIA_API_KEY,
    "X-Algolia-Application-Id": ALGOLIA_APP_ID,
    "Content-Type": "application/json",
}


class VentureBeatSpider(SpiderBase):
    name = "venturebeat"
    fetcher_type = "http"
    schedule_interval = "daily"

    start_urls = [
        "https://venturebeat.com/ai/",
    ]
    max_page = 20
    algolia_hits_per_page = 20

    # ===== Algolia 列表抓取 =====

    async def _fetch_algolia(self, query: str, page: int) -> list[dict]:
        """通过 Algolia API 获取文章列表（分页）"""
        body = {
            "query": query,
            "hitsPerPage": self.algolia_hits_per_page,
            "page": page,
            "attributesToRetrieve": "objectID,slug,title,excerpt,bodyText,publishDate,contentType,path",
        }
        import requests as sync_requests
        try:
            resp = sync_requests.post(
                ALGOLIA_URL,
                headers=ALGOLIA_HEADERS,
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits", [])
            logger.debug(f"Algolia page={page} hits={len(hits)}")
            return hits
        except Exception as e:
            logger.warning(f"[{self.name}] Algolia 请求失败 (page={page}): {e}")
            return []

    # ===== 列表页解析 =====

    async def parse_list(self, query: str, page: int) -> list[str]:
        """从 Algolia 结果中提取新闻详情页 URL"""
        hits = await self._fetch_algolia(query, page)
        if not hits:
            return []
        urls = []
        for hit in hits:
            slug = hit.get("slug") or ""
            path = hit.get("path") or f"/ai/{slug}/"
            if not path.startswith("http"):
                path = f"https://venturebeat.com{path}"
            urls.append(path)
        return list(dict.fromkeys(urls))

    # ===== 详情页解析 =====

    async def parse(self, url: str) -> dict | None:
        """解析新闻详情页，返回新闻数据字典"""
        html = await self.fetch(url)
        if not html:
            logger.warning(f"{url} 请求内容错误")
            return None
        page = Selector(content=html)

        # 标题：优先 h1，备用 JSON-LD
        title = page.xpath("//h1/text()").get()
        if not title:
            json_ld = page.xpath("//script[@type='application/ld+json']/text()").get()
            if json_ld:
                try:
                    title = json.loads(json_ld).get("headline")
                except (json.JSONDecodeError, AttributeError):
                    pass
        if not title:
            logger.warning(f"{url} 未找到标题")
            return None

        # 发布时间：meta tag > <time> > JSON-LD
        publish_time = page.xpath("//meta[@property='article:published_time']/@content").get()
        if not publish_time:
            publish_time = page.xpath("//time[@datetime]/@datetime").get()
        if not publish_time:
            json_ld = page.xpath("//script[@type='application/ld+json']/text()").get()
            if json_ld:
                try:
                    publish_time = json.loads(json_ld).get("datePublished")
                except (json.JSONDecodeError, AttributeError):
                    pass
        if publish_time:
            try:
                publish_time = datetime.fromisoformat(publish_time.replace("Z", "+00:00"))
                publish_time = publish_time.replace(tzinfo=None)
            except ValueError:
                publish_time = None

        # 正文：article 容器内段落
        paragraphs = page.xpath(
            "//article[@id]//div[contains(@class,'article-body')]//p//text()"
        ).getall()
        if not paragraphs:
            paragraphs = page.xpath(
                "//article[@id]//div[contains(@class,'prose')]//p//text()"
            ).getall()
        if not paragraphs:
            paragraphs = page.xpath("//article//p//text()").getall()
        content = "\n".join(paragraphs).strip()

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

        # 立即分析新闻
        analysis_result = NewsAnalyzer.analyze(news_data)
        news_data.update(analysis_result)
        # logger.debug(
        #     f"新闻分析完成：{title[:30] if title else 'Unknown'}... "
        #     f"hotness={news_data.get('hotness')}, "
        #     f"sentiment={news_data.get('sentiment')}, "
        #     f"category={news_data.get('category')},"
        #     f"related_models={news_data.get('related_models')}"
        # )
        # print(news_data)

        return news_data

    # ===== 爬虫主入口 =====

    async def crawl(self) -> list[dict]:
        """翻页抓取：通过 Algolia API 分页获取文章链接，再逐一解析详情页"""
        all_news = []
        query = "ai"
        end_date = datetime.now() - timedelta(days=30)

        for page_num in range(0, self.max_page):
            logger.info(f"[{self.name}] Algolia 第 {page_num + 1} 页（skip={page_num * self.algolia_hits_per_page}）")
            urls = await self.parse_list(query, page_num)
            if not urls:
                logger.warning(f"[{self.name}] 第 {page_num + 1} 页无数据，停止")
                break
            logger.info(f"page {page_num + 1} 找到 {len(urls)} 条新闻链接")

            for news_url in urls:
                news_data = await self.parse(news_url)
                if news_data:
                    all_news.append(news_data)
                    if news_data.get("published_at") and news_data["published_at"] < end_date:
                        logger.info(f"page {page_num + 1} news published_at = {news_data['published_at']}")
                        logger.info(f"[{self.name}] 共抓取 {len(all_news)} 条新闻")
                        return all_news

            logger.info(f"page {page_num + 1} 解析完成，累计 {len(all_news)} 条")

        logger.info(f"[{self.name}] 共抓取 {len(all_news)} 条新闻")
        return all_news


SPIDER_REGISTRY = {
    "venturebeat": VentureBeatSpider()
}
