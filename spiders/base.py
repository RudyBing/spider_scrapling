"""爬虫基类：提供通用请求能力，子类专注实现 parse() 和翻页调度。

子类只需实现：
    name          : str                    # 站点唯一标识（需与 sites.yaml 匹配）
    fetcher_type  : str = "http"           # http | stealthy | dynamic | requests
    start_urls    : list[str] = []         # 起始 URL（可选）
    headers       : dict = None            # 自定义请求头（覆盖默认浏览器指纹）

    def parse(self, html: str, url: str = None) -> list[dict]:
        # 解析单页 HTML，返回数据条目列表
        ...
"""
import asyncio
from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime
from loguru import logger
import requests as sync_requests
from scrapling import Fetcher, StealthyFetcher

# 默认浏览器指纹 headers，Fetcher 失败时自动注入
_DEFAULT_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "accept-encoding": "gzip, deflate, br, zstd",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}


class SpiderBase(ABC):
    name: str = ""
    fetcher_type: str = "http"
    schedule_interval: str = "daily"
    start_urls: list[str] = []
    headers: Optional[dict] = None

    @abstractmethod
    def parse(self, html: str, url: str = None) -> list[dict]:
        ...

 
    # ===== 请求层 =====

    async def fetch(self, url: str, headers: dict = None, fetcher_type: str = None) -> str | None:
        """根据 fetcher_type 选择请求方式，子类可覆盖。
        所有请求均在线程中执行，避免 asyncio 与同步 API 冲突。
        fetcher_type 参数可临时覆盖类级 self.fetcher_type（如 "requests"）。
        """
        effective_type = fetcher_type or self.fetcher_type
        if effective_type == "stealthy":
            return await asyncio.to_thread(self._fetch_stealth, url, headers)
        elif effective_type == "dynamic":
            return await asyncio.to_thread(self._fetch_dynamic, url, headers)
        elif effective_type == "requests":
            return await asyncio.to_thread(self._fetch_requests, url, headers)
        return await asyncio.to_thread(self._fetch_http, url, headers)

    def _fetch_http(self, url: str, headers: dict = None) -> str | None:
        """普通 HTTP 请求：先无 header 尝试，失败则注入浏览器指纹重试。"""
        effective_headers = headers or self.headers
        try:
            page = Fetcher.get(url, headers=effective_headers)
            if page.status != 200:
                logger.warning(f"[{self.name}] HTTP {page.status}: {url}")
                return None
            return page.html_content
        except Exception:
            pass
        # 失败兜底：注入浏览器指纹重试
        try:
            page = Fetcher.get(url, headers=effective_headers or _DEFAULT_HEADERS)
            return page.html_content
        except Exception as e:
            logger.error(f"[{self.name}] 请求失败 {url}: {e}")
            return None

    def _fetch_stealth(self, url: str, headers: dict = None) -> str | None:
        """Stealth 模式：Playwright 真实浏览器，绕过 Cloudflare/WAF。
        失败时自动回退到普通 Fetcher。
        """
        try:
            page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
            return page.html_content
        except Exception as e:
            logger.warning(f"[{self.name}] StealthyFetcher 失败 ({e})，回退 Fetcher")
            return self._fetch_http(url, headers)

    def _fetch_dynamic(self, url: str, headers: dict = None) -> str | None:
        """等待 JS 渲染完成后提取 HTML（失败时回退到 HTTP）。"""
        try:
            page = StealthyFetcher.fetch(url, headless=True, wait_until="networkidle")
            return page.html_content
        except Exception as e:
            logger.error(f"[{self.name}] 动态渲染失败 {url}: {e}")
            return self._fetch_http(url, headers)

    def _fetch_requests(self, url: str, headers: dict = None) -> str | None:
        """使用 requests 库发起请求，适用于 API/JSON 接口。
        失败时自动回退到普通 Fetcher。
        """
        try:
            resp = sync_requests.get(url, headers=headers or self.headers, timeout=15)
            return resp.text
        except Exception as e:
            logger.warning(f"[{self.name}] requests 请求失败 ({e})，回退 Fetcher")
            return self._fetch_http(url, headers)
