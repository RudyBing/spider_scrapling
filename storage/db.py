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
    is_published BOOLEAN NOT NULL DEFAULT FALSE,
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
                    title = ?, content = ?, 
                    category = ?, tags = ?, related_models = ?, sentiment = ?, 
                    hotness = ?, language = ?, updated_at = CURRENT_TIMESTAMP
                WHERE original_url = ?
            """, news.get("title"), news.get("content"), 
                news.get("category", "行业动态"), tags_val, related_val,
                news.get("sentiment", "neutral"), news.get("hotness", 50),
                news.get("language", "en"), news["original_url"])
        else:
            await conn.execute("""
                INSERT INTO spider_news (
                    id, slug, title, content, source, original_url, 
                    published_at, category, tags, related_models,
                    sentiment, hotness, language
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, news.get("id"), news.get("slug"), news.get("title"), news.get("content"), 
                news.get("source"), news["original_url"], news.get("published_at"),
                news.get("category", "行业动态"), tags_val, related_val,
                news.get("sentiment", "neutral"), news.get("hotness", 50),
                news.get("language", "en"))
    else:
        await conn.execute("""
            INSERT INTO spider_news (
                id, slug, title, content,
                source, original_url, published_at, category,
                tags, related_models, sentiment, hotness, language
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (original_url) DO UPDATE SET
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                published_at = EXCLUDED.published_at,
                category = EXCLUDED.category,
                tags = EXCLUDED.tags,
                related_models = EXCLUDED.related_models,
                sentiment = EXCLUDED.sentiment,
                hotness = EXCLUDED.hotness,
                language = EXCLUDED.language,
                updated_at = CURRENT_TIMESTAMP
        """, news.get("id"), news.get("slug"), news.get("title"), news.get("content"), 
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
                is_published = TRUE,
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
                is_published = TRUE,
                translated_at = CURRENT_TIMESTAMP,
                translate_service = $3,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $4
        """, title_cn, content_cn, translate_service, news_id)


# ==================== spider_ai_models 表操作函数 ====================
async def insert_ai_model(conn, model: dict):
    """插入或更新 AI 模型数据到 spider_ai_models 表
    
    Args:
        conn: 数据库连接
        model: AI 模型数据字典，包含以下字段：
            - id: 模型 ID（如 'gpt-4'）
            - name: 模型名称
            - slug: URL 友好的标识符
            - provider: 提供商名称
            - logo: Logo URL（可为空）
            - description: 描述（留空，等 AI 生成）
            - category: 类别
            - pricing_input: 输入价格
            - pricing_output: 输出价格
            - pricing_unit: 价格单位
            - context_window: 上下文窗口
            - multimodal: 是否多模态
            - strengths: 优势列表（留空，等 AI 生成）
            - benchmark_score: 基准分数（可为 None）
            - released: 发布日期（可为 None）
            - url: 官方 URL
            - free_tier: 免费层级信息
            - updated_at: 更新时间
    """
    import json
    
    # 处理 strengths 字段（数组转 JSON 字符串）
    strengths_val = model.get("strengths", [])
    if _USE_SQLITE:
        if isinstance(strengths_val, list):
            strengths_val = json.dumps(strengths_val, ensure_ascii=False)
    
    if _USE_SQLITE:
        # SQLite 实现
        existing = await conn.fetchrow(
            "SELECT id FROM spider_ai_models WHERE id = ? OR slug = ?",
            model.get("id"), model.get("slug")
        )
        if existing:
            # 更新已存在的模型
            await conn.execute("""
                UPDATE spider_ai_models SET
                    id = ?, name = ?, slug = ?, provider = ?, logo = ?,
                    description = ?, category = ?, pricing_input = ?,
                    pricing_output = ?, pricing_unit = ?, context_window = ?,
                    multimodal = ?, strengths = ?, benchmark_score = ?,
                    released = ?, url = ?, free_tier = ?,
                    updated_at = ?
                WHERE id = ? OR slug = ?
            """, model.get("id"), model.get("name"), model.get("slug"), model.get("provider"),
                model.get("logo", ""), model.get("description", ""),
                model.get("category"), model.get("pricing_input"),
                model.get("pricing_output"), model.get("pricing_unit", ""),
                model.get("context_window"), model.get("multimodal", False),
                strengths_val, model.get("benchmark_score"),
                model.get("released"), model.get("url", ""),
                model.get("free_tier", ""), model.get("updated_at"),
                model.get("id"), model.get("slug"))
        else:
            # 插入新模型
            await conn.execute("""
                INSERT INTO spider_ai_models (
                    id, name, slug, provider, logo, description,
                    category, pricing_input, pricing_output, pricing_unit,
                    context_window, multimodal, strengths, benchmark_score,
                    released, url, free_tier, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, model.get("id"), model.get("name"), model.get("slug"),
                model.get("provider"), model.get("logo", ""),
                model.get("description", ""), model.get("category"),
                model.get("pricing_input"), model.get("pricing_output"),
                model.get("pricing_unit", ""), model.get("context_window"),
                model.get("multimodal", False), strengths_val,
                model.get("benchmark_score"), model.get("released"),
                model.get("url", ""), model.get("free_tier", ""),
                model.get("updated_at"))
    else:
        # PostgreSQL 实现
        # 先查询是否已存在（按 id 或 slug）
        existing = await conn.fetchrow(
            "SELECT id FROM spider_ai_models WHERE id = $1 OR slug = $2",
            model.get("id"), model.get("slug")
        )
        if existing:
            # 更新已存在的记录
            await conn.execute("""
                UPDATE spider_ai_models SET
                    name = $1, slug = $2, provider = $3, logo = $4,
                    description = $5, category = $6, pricing_input = $7,
                    pricing_output = $8, pricing_unit = $9, context_window = $10,
                    multimodal = $11, strengths = $12, benchmark_score = $13,
                    released = $14, url = $15, free_tier = $16,
                    updated_at = $17
                WHERE id = $18 OR slug = $19
            """, model.get("name"), model.get("slug"), model.get("provider"),
                model.get("logo", ""), model.get("description", ""),
                model.get("category"), model.get("pricing_input"),
                model.get("pricing_output"), model.get("pricing_unit", ""),
                model.get("context_window"), model.get("multimodal", False),
                strengths_val, model.get("benchmark_score"),
                model.get("released"), model.get("url", ""),
                model.get("free_tier", ""), model.get("updated_at"),
                model.get("id"), model.get("slug"))
        else:
            # 插入新记录
            await conn.execute("""
                INSERT INTO spider_ai_models (
                    id, name, slug, provider, logo, description,
                    category, pricing_input, pricing_output, pricing_unit,
                    context_window, multimodal, strengths, benchmark_score,
                    released, url, free_tier, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
                ON CONFLICT (slug) DO UPDATE SET
                    id = EXCLUDED.id,
                    name = EXCLUDED.name,
                    slug = EXCLUDED.slug,
                    provider = EXCLUDED.provider,
                    logo = EXCLUDED.logo,
                    description = EXCLUDED.description,
                    category = EXCLUDED.category,
                    pricing_input = EXCLUDED.pricing_input,
                    pricing_output = EXCLUDED.pricing_output,
                    pricing_unit = EXCLUDED.pricing_unit,
                    context_window = EXCLUDED.context_window,
                    multimodal = EXCLUDED.multimodal,
                    strengths = EXCLUDED.strengths,
                    benchmark_score = EXCLUDED.benchmark_score,
                    released = EXCLUDED.released,
                    url = EXCLUDED.url,
                    free_tier = EXCLUDED.free_tier,
                    updated_at = EXCLUDED.updated_at
            """, model.get("id"), model.get("name"), model.get("slug"),
                model.get("provider"), model.get("logo", ""),
                model.get("description", ""), model.get("category"),
                model.get("pricing_input"), model.get("pricing_output"),
                model.get("pricing_unit", ""), model.get("context_window"),
                model.get("multimodal", False), strengths_val,
                model.get("benchmark_score"), model.get("released"),
                model.get("url", ""), model.get("free_tier", ""),
                model.get("updated_at"))

