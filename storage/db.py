"""数据库连接与初始化
支持 PostgreSQL（推荐生产用）和 SQLite（本地调试用）。
DATABASE_URL 以 sqlite:// 开头时自动使用 SQLite，无需安装 PostgreSQL。
"""
from __future__ import annotations

import os
from loguru import logger

# ---- 根据 DATABASE_URL 选择后端 ----
_DB_URL = os.environ.get("DATABASE_URL", "")
_USE_SQLITE = _DB_URL.startswith("sqlite://")

if _USE_SQLITE:
    import aiosqlite
    _PARAM_STYLE = "?"   # SQLite 使用 ? 占位符
else:
    import asyncpg
    _PARAM_STYLE = "$"  # PostgreSQL 使用 $1, $2...
    _TABLES_SQL = """
CREATE TABLE IF NOT EXISTS spider_news (
    id VARCHAR(32) NOT NULL,
    slug VARCHAR(256) NOT NULL,
    title VARCHAR(512) NOT NULL,
    title_cn VARCHAR(512),
    content TEXT,
    content_cn TEXT,
    source VARCHAR(128) NOT NULL,
    original_url TEXT NOT NULL,
    published_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    category VARCHAR(64) NOT NULL DEFAULT '行业动态',
    tags TEXT,
    related_models TEXT,
    sentiment VARCHAR(10) NOT NULL DEFAULT 'neutral',
    hotness INT NOT NULL DEFAULT 50,
    language VARCHAR(16) NOT NULL DEFAULT 'en',
    is_published BOOLEAN NOT NULL DEFAULT TRUE,
    priority INT NOT NULL DEFAULT 0,
    translated_at TIMESTAMP,
    translate_service VARCHAR(64),
    CONSTRAINT pk_spider_news PRIMARY KEY (id),
    CONSTRAINT uk_slug UNIQUE (slug),
    CONSTRAINT uk_original_url UNIQUE (original_url)
);
CREATE INDEX IF NOT EXISTS idx_published_at ON spider_news (published_at);
CREATE INDEX IF NOT EXISTS idx_category ON spider_news (category);
CREATE INDEX IF NOT EXISTS idx_hotness ON spider_news (hotness);
CREATE INDEX IF NOT EXISTS idx_sentiment ON spider_news (sentiment);
CREATE INDEX IF NOT EXISTS idx_language ON spider_news (language);
    """
# ---- SQLite 状态 ----
_sqlite_db: aiosqlite.Connection | None = None
_sqlite_path: str = ""

# ---- PostgreSQL 状态 ----
_pg_pool = None


# ==================== SQLite 实现 ====================
async def _get_sqlite_conn() -> aiosqlite.Connection:
    global _sqlite_db, _sqlite_path
    if _sqlite_db is not None:
        return _sqlite_db

    _sqlite_path = _DB_URL.replace("sqlite:///", "")
    if not os.path.isabs(_sqlite_path):
        _sqlite_path = os.path.join(os.path.dirname(__file__), "..", _sqlite_path)
    _sqlite_path = os.path.normpath(_sqlite_path)

    os.makedirs(os.path.dirname(_sqlite_path) or ".", exist_ok=True)
    _sqlite_db = await aiosqlite.connect(_sqlite_path)
    await _sqlite_db.execute(_TABLES_SQL)
    await _sqlite_db.commit()
    logger.info(f"SQLite 数据库已连接：{_sqlite_path}")
    return _sqlite_db


class _SQLiteConn:
    """包装 aiosqlite connection 使其兼容 asyncpg 的 acquire() 模式"""
    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, sql: str, *args):
        if _USE_SQLITE:
            await self._db.execute(sql, args)
            await self._db.commit()
            return self
        else:
            return await self._db.execute(sql, *args)

    async def fetchrow(self, sql: str, *args):
        if _USE_SQLITE:
            cursor = await self._db.execute(sql, args)
            row = await cursor.fetchone()
            if row:
                return dict(zip([d[0] for d in cursor.description], row))
            return None
        else:
            return await self._db.fetchrow(sql, *args)

    async def fetch(self, sql: str, *args):
        if _USE_SQLITE:
            cursor = await self._db.execute(sql, args)
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, r)) for r in rows]
        else:
            return await self._db.fetch(sql, *args)

    async def fetchval(self, sql: str, *args):
        if _USE_SQLITE:
            cursor = await self._db.execute(sql, args)
            row = await cursor.fetchone()
            if row:
                return row[0]
            return None
        else:
            return await self._db.fetchval(sql, *args)

    @property
    def lastrowid(self):
        return self._db.lastrowid


async def get_pool():
    """获取数据库连接（SQLite 或 PostgreSQL）"""
    if _USE_SQLITE:
        db = await _get_sqlite_conn()
        return _SQLiteConn(db)

    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool

    from config import load_settings
    settings = load_settings()
    url = settings["DATABASE_URL"]
    if not url or "USER" in url:
        logger.warning("DATABASE_URL 未配置，跳过数据库初始化")
        return None

    _pg_pool = await asyncpg.create_pool(url, min_size=2, max_size=10)
    async with _pg_pool.acquire() as conn:
        await conn.execute(_TABLES_SQL)
    logger.info("PostgreSQL 数据库连接成功")
    return _pg_pool


# ==================== 操作函数（PostgreSQL / SQLite 兼容） ====================
async def get_site_by_name(conn, name: str):
    return await conn.fetchrow(
        f"SELECT * FROM sites WHERE name = {_PARAM_STYLE}", name
    )


async def insert_site(conn, site: dict):
    selector_val = site.get("selector", {})
    if isinstance(selector_val, dict):
        selector_val = str(selector_val)

    if _USE_SQLITE:
        existing = await conn.fetchrow(
            "SELECT id FROM sites WHERE name = ?", site["name"]
        )
        if existing:
            await conn.execute("""
                UPDATE sites SET url=?, type=?, fetcher_type=?, selector=?, schedule_interval=?, last_crawled=NOW()
                WHERE name=?
            """, site["url"], site["type"], site.get("fetcher", "http"),
                               selector_val, site.get("schedule_interval", "daily"),
                               site["name"])
            return existing["id"]
        cursor = await conn.execute("""
            INSERT INTO sites (name, url, type, fetcher_type, selector, schedule_interval)
            VALUES (?, ?, ?, ?, ?, ?)
        """, site["name"], site["url"], site["type"],
               site.get("fetcher", "http"), selector_val,
               site.get("schedule_interval", "daily"))
        return cursor.lastrowid
    else:
        return await conn.fetchval("""
            INSERT INTO sites (name, url, type, fetcher_type, selector, schedule_interval)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (name) DO UPDATE SET
                url = EXCLUDED.url,
                selector = EXCLUDED.selector,
                last_crawled = NOW()
            RETURNING id
        """, site["name"], site["url"], site["type"],
                site.get("fetcher", "http"), selector_val,
                site.get("schedule_interval", "daily"))


async def insert_item(conn, site_id: int, item: dict):
    extra_val = item.get("extra", {})
    if isinstance(extra_val, dict):
        extra_val = str(extra_val)

    if _USE_SQLITE:
        await conn.execute("""
            INSERT INTO crawled_items (site_id, url, title, content, author,
                                       published_at, extra_data, raw_html)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (site_id, url) DO UPDATE SET
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                extra_data = EXCLUDED.extra_data,
                fetched_at = NOW()
        """, site_id, item["url"], item.get("title"), item.get("content"),
             item.get("author"), item.get("published_at"),
             extra_val, item.get("raw_html", "")[:50000])
    else:
        await conn.execute("""
            INSERT INTO crawled_items (site_id, url, title, content, author,
                                       published_at, extra_data, raw_html)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (site_id, url) DO UPDATE SET
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                extra_data = EXCLUDED.extra_data,
                fetched_at = NOW()
        """, site_id, item["url"], item.get("title"), item.get("content"),
             item.get("author"), item.get("published_at"),
             extra_val, item.get("raw_html", "")[:50000])


async def update_last_crawled(conn, site_id: int):
    if _USE_SQLITE:
        await conn.execute(
            "UPDATE sites SET last_crawled = NOW() WHERE id = ?", site_id
        )
    else:
        await conn.execute(
            "UPDATE sites SET last_crawled = NOW() WHERE id = $1", site_id
        )


# ==================== spider_news 表操作函数 ====================
async def insert_news(conn, news: dict):
    """插入或更新新闻数据到 spider_news 表"""
    import json
    
    tags_val = news.get("tags")
    if isinstance(tags_val, (list, dict)):
        tags_val = json.dumps(tags_val, ensure_ascii=False)
    
    related_val = news.get("related_models")
    if isinstance(related_val, (list, dict)):
        related_val = json.dumps(related_val, ensure_ascii=False)
    
    if _USE_SQLITE:
        existing = await conn.fetchrow(
            "SELECT id FROM spider_news WHERE original_url = ?", news["original_url"]
        )
        if existing:
            await conn.execute("""
                UPDATE spider_news SET 
                    title = ?, title_cn = ?, content = ?, content_cn = ?,
                    published_at = ?, category = ?, tags = ?, related_models = ?,
                    sentiment = ?, hotness = ?, language = ?, updated_at = CURRENT_TIMESTAMP
                WHERE original_url = ?
            """, news.get("title"), news.get("title_cn"), news.get("content"),
                news.get("content_cn"), news.get("published_at"),
                news.get("category", "行业动态"), tags_val, related_val,
                news.get("sentiment", "neutral"), news.get("hotness", 50),
                news.get("language", "en"), news["original_url"])
        else:
            await conn.execute("""
                INSERT INTO spider_news (
                    id, slug, title, title_cn, content, content_cn,
                    source, original_url, published_at, category,
                    tags, related_models, sentiment, hotness, language
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, news.get("id"), news.get("slug"), news.get("title"),
                news.get("title_cn"), news.get("content"), news.get("content_cn"),
                news.get("source"), news["original_url"], news.get("published_at"),
                news.get("category", "行业动态"), tags_val, related_val,
                news.get("sentiment", "neutral"), news.get("hotness", 50),
                news.get("language", "en"))
    else:
        await conn.execute("""
            INSERT INTO spider_news (
                id, slug, title, title_cn, content, content_cn,
                source, original_url, published_at, category,
                tags, related_models, sentiment, hotness, language
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            ON CONFLICT (original_url) DO UPDATE SET
                title = EXCLUDED.title,
                title_cn = EXCLUDED.title_cn,
                content = EXCLUDED.content,
                content_cn = EXCLUDED.content_cn,
                published_at = EXCLUDED.published_at,
                category = EXCLUDED.category,
                tags = EXCLUDED.tags,
                related_models = EXCLUDED.related_models,
                sentiment = EXCLUDED.sentiment,
                hotness = EXCLUDED.hotness,
                language = EXCLUDED.language,
                updated_at = CURRENT_TIMESTAMP
        """, news.get("id"), news.get("slug"), news.get("title"),
            news.get("title_cn"), news.get("content"), news.get("content_cn"),
            news.get("source"), news["original_url"], news.get("published_at"),
            news.get("category", "行业动态"), tags_val, related_val,
            news.get("sentiment", "neutral"), news.get("hotness", 50),
            news.get("language", "en"))


# ==================== 翻译相关函数 ====================
async def update_news_translation(conn, news_id: str, title_cn: str, content_cn: str, translate_service: str = "tencent"):
    """更新新闻翻译结果"""
    if _USE_SQLITE:
        await conn.execute("""
            UPDATE spider_news SET
                title_cn = ?,
                content_cn = ?,
                translated_at = CURRENT_TIMESTAMP,
                translate_service = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, title_cn, content_cn, translate_service, news_id)
    else:
        await conn.execute("""
            UPDATE spider_news SET
                title_cn = $1,
                content_cn = $2,
                translated_at = CURRENT_TIMESTAMP,
                translate_service = $3,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $4
        """, title_cn, content_cn, translate_service, news_id)
