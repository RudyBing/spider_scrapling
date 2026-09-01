"""爬虫调度器：遍历 sites.yaml，调用对应 spider 的 crawl() 完成抓取入库"""
import asyncio
from loguru import logger
from config import load_settings, load_sites
from storage.db import get_pool, insert_site, insert_news, update_last_crawled
from spiders.techcrunch import SPIDER_REGISTRY as TC_REGISTRY

# 合并所有 spider registry
SPIDER_REGISTRY = {**TC_REGISTRY}


class Crawler:
    def __init__(self):
        self.settings = load_settings()
        self.sites = load_sites()
        self.concurrency = self.settings.get("CONCURRENCY", 5)
        self.delay = self.settings.get("REQUEST_DELAY", 2)

    async def run(self):
        pool = await get_pool()
        if pool is None:
            logger.error("数据库未连接，退出")
            return

        logger.info(f"开始爬取，共 {len(self.sites)} 个站点")

        semaphore = asyncio.Semaphore(self.concurrency)

        async def crawl_site(site: dict):
            async with semaphore:
                await self._crawl_one(site, pool)
                await asyncio.sleep(self.delay)

        tasks = [asyncio.create_task(crawl_site(site)) for site in self.sites]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = sum(1 for r in results if not isinstance(r, Exception))
        failed = sum(1 for r in results if isinstance(r, Exception))
        logger.info(f"爬取完成：成功 {success}，失败 {failed}")

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error(f"站点 {self.sites[i]['name']} 失败：{r}")

    async def _crawl_one(self, site: dict, pool):
        name = site["name"]

        spider = SPIDER_REGISTRY.get(name)
        if spider is None:
            logger.warning(f"[{name}] 未找到对应的 Spider 子类，跳过")
            return

        logger.info(f"[{name}] 开始抓取，start_urls={spider.start_urls}")

        # spider 自己管理起始 URL、请求、翻页、解析、去重
        items = await spider.crawl()
        if not items:
            logger.warning(f"[{name}] 无数据返回，跳过入库")
            return

        async with pool.acquire() as conn:
            # 注册站点并获取 site_id
            site_id = await insert_site(conn, {
                "name": name,
                "url": ", ".join(spider.start_urls),
                "type": spider.__class__.__name__,
                "fetcher_type": spider.fetcher_type,
                "schedule_interval": spider.schedule_interval,
            })

            # 入库新闻数据
            for item in items:
                await insert_news(conn, item)

            await update_last_crawled(conn, site_id)

        logger.info(f"[{name}] 成功保存 {len(items)} 条新闻到 spider_news 表")
