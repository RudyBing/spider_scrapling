"""数据库连接与初始化
支持 PostgreSQL（推荐生产用）和 SQLite（本地调试用）。
DATABASE_URL 以 sqlite:// 开头时自动使用 SQLite，无需安装 PostgreSQL。
"""
from __future__ import annotations

import os
import re
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
# 字段分为两类：
# ALWAYS_COLS：采集核心字段，必须有值、始终覆盖写（保持数据新鲜）。
# OPTIONAL_COLS：可能由其他数据源或 AI 任务生成，只在该字段"有值"时才写入，
#                为空值时不写不覆盖，避免清掉库中已有的准确内容。
ALWAYS_COLS = [
    "name", "slug", "category", "pricing_input",
    "pricing_output", "context_window", "multimodal", "composite_score",
    "updated_at",
]
OPTIONAL_COLS = [
    "logo", "provider", "pricing_unit", "description", "strengths", "benchmark_score",
    "released", "url", "free_tier",
]


def _is_filled(value) -> bool:
    """判断字段是否为"有值"状态（None/空串/空集合视为无值）"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def _normalize_id_for_match(id_str: str) -> str:
    """归一化模型 ID，用于跨格式匹配（litellm 点号 ↔ openrouter 斜杠）。

    处理规则（严格按顺序）：
    1. 小写化，统一分隔符为 `-`
    2. 首段 provider 别名标准化（单向映射到标准名，确保不同写法结果一致）
       - google ↔ gemini（统一为 google）
       - mistralai ↔ mistral（统一为 mistral）
       - azure_ai ↔ azure-ai（统一为 azure_ai）
       - x-ai ↔ xai（统一为 xai）
    3. 去除第二段中重复的 provider 名（用 alias 后的标准名）
       例：google/gemini-2.5-flash → google-2-5-flash
           deepseek/deepseek-chat-v3 → deepseek-chat-v3
           meta-llama/Meta-Llama-3.1-70B → meta-llama-3-1-70b
           anthropic/claude-3-opus → anthropic-claude-3-opus（不去重）
    4. 剥离 Bedrock/Vertex 风格版本后缀（-v数字:数字，如 -v1:0）
       注：deepseek-chat-v3 中的 -v3 是型号标识，不剥离
    """
    # 已知 provider 别名表（key=任意写法，value=标准名）
    # 用于：① 无 / 分隔符时识别 bare 格式的 provider 别名
    #       ② 去重时正确识别多连字符 provider（如 meta-llama）
    _PROVIDER_ALIASES = {
        "google", "gemini",
        "mistralai", "mistral",
        "azure_ai", "azure-ai",
        "x-ai", "xai",
        # 常见 provider（完整列表，用于多连字符识别）
        "anthropic", "openai", "deepseek", "meta-llama", "meta",
        "cohere", "fireworks", "together", "groq", "nvidia",
        "novita", "perplexity", "yi", "qwen", "dashscope",
        "sambanova", "zhipu", "baidu", "junjie",
    }
    # 标准别名映射（首段 only）
    _STANDARD_ALIASES = {
        "gemini": "google",
        "mistralai": "mistral",
        "azure_ai": "azure-ai",
        "xai": "x-ai",
    }

    s = id_str.lower()
    # 先将版本号小数点替换为 -（如 2.5→2-5，3.1→3-1），避免其干扰后续分隔符检测
    s = re.sub(r"(?<=\d)\.(?=\d)", "-", s)
    # 再按第一个 / 或 . 定位第一段 provider（此时 . 只在原本就是分隔符的位置出现）
    _sep_positions = [(s.find(c), c) for c in "/."] if s else []
    sep_idx, sep_char = min(((i, c) for i, c in _sep_positions if i >= 0), default=(-1, ""))
    orig_prefix = s[:sep_idx] if sep_idx >= 0 else ""

    # ---- 步骤1：首段 alias 标准化 ----
    # 用 _PROVIDER_ALIASES 反查：若 orig_prefix 是某个标准名的别名，则用标准名
    alias_prefix = _STANDARD_ALIASES.get(orig_prefix, orig_prefix)
    if sep_idx >= 0:
        s = alias_prefix + "-" + s[sep_idx + 1:]
    else:
        # 无分隔符时：检查整个 ID 是否是 provider 别名（如 gemini → google）
        # 策略：取 s 的第一个 token（按 - 分割），若在别名表中则替换首段
        first_token_end = s.find("-")
        if first_token_end >= 0:
            first_token = s[:first_token_end]
            mapped = _STANDARD_ALIASES.get(first_token, first_token)
            if mapped != first_token:
                s = mapped + s[first_token_end:]
        else:
            s = _STANDARD_ALIASES.get(s, s)

    # ---- 步骤2：去除第二段中重复的 provider 名 ----
    if sep_idx >= 0:
        std_prefix = alias_prefix  # alias 后的标准名
        rest_lower = s[len(std_prefix) + 1:]  # 去掉 "std_prefix-"
        # 用 _PROVIDER_ALIASES 从已知 provider 列表中匹配第二段首段
        # 策略：按长度降序尝试匹配（优先匹配长的，如 meta-llama 而非 meta）
        matched_rest_prefix = ""
        for candidate in sorted(_PROVIDER_ALIASES, key=len, reverse=True):
            if rest_lower.startswith(candidate + "-") or rest_lower == candidate:
                matched_rest_prefix = candidate
                break
        if matched_rest_prefix:
            # 将匹配的 provider 名也做 alias 映射
            aliased_rest = _STANDARD_ALIASES.get(matched_rest_prefix, matched_rest_prefix)
            if aliased_rest == std_prefix:
                suffix = rest_lower[len(matched_rest_prefix):]  # 去掉匹配的 provider 名
                if suffix.startswith("-"):
                    suffix = suffix[1:]
                s = std_prefix + "-" + suffix if suffix else std_prefix

    # ---- 步骤3：剥离版本号后缀 ----
    s = re.sub(r"-v\d+:\d+$", "", s)       # Bedrock/Vertex 风格 -v1:0
    s = re.sub(r"@[\d]+", "", s)           # @时间戳
    s = re.sub(r":[\d]+$", "", s)          # 结尾 :数字

    return s.strip("-")


async def insert_ai_model(conn, model: dict):
    """插入或更新 AI 模型数据到 spider_ai_models 表

    写入策略（insert 与 update 一致，都只写"有值的字段"）：
    - ALWAYS_COLS：采集核心字段，始终写入（保证价格/上下文等数据新鲜）。
    - OPTIONAL_COLS：仅当本次采集提供了值才写入（如 openrouter 的 description/benchmark_score/released）；若本次没提供值
      （None/空串/空数组），则 update 时保留库中原值、insert 时不写入该列，
      避免覆盖 AI 或其他数据源已生成的内容。

    id/slug 匹配：精确匹配 + 跨格式归一化匹配。
    litellm（点号分隔，如 "anthropic.claude-3-opus"）与 openrouter（斜杠分隔，如 "anthropic/claude-3-opus"）
    通过 _normalize_id_for_match() 归一化后比对，匹配成功则 UPDATE，否则 INSERT。
    同一模型在不同 provider 路径下（如 litellm 的 "azure/deepseek-v3" 与 openrouter 的 "deepseek/deepseek-chat"）
    会各自保留为独立记录，确保数据不被错误覆盖。

    Args:
        conn: 数据库连接
        model: AI 模型数据字典
    """
    always_cols = list(ALWAYS_COLS)
    always_vals = [model.get(c) for c in always_cols]

    # 仅收集"有值"的可选字段
    opt_cols = []
    opt_vals = []
    for col in OPTIONAL_COLS:
        v = model.get(col)
        if _is_filled(v):
            opt_cols.append(col)
            opt_vals.append(v)

    existing_id = model.get("id")
    existing_slug = model.get("slug")
    new_norm_id = _normalize_id_for_match(existing_id)
    new_norm_slug = _normalize_id_for_match(existing_slug)

    # 查找已有记录：先精确匹配，未命中再归一化跨格式匹配
    existing = await conn.fetchrow(
        f"SELECT id FROM spider_ai_models WHERE id = {'?' if _USE_SQLITE else '$1'} "
        f"OR slug = {'?' if _USE_SQLITE else '$2'}",
        existing_id, existing_slug,
    )
    if not existing:
        rows = await conn.fetch(
            f"SELECT id, slug FROM spider_ai_models"
        )
        for row in rows:
            if _normalize_id_for_match(row["id"]) == new_norm_id or \
               _normalize_id_for_match(row["slug"]) == new_norm_slug:
                existing = row
                break

    if existing:
        # UPDATE：写 ALWAYS_COLS + 有值的 OPTIONAL_COLS
        set_cols = list(always_cols)
        set_vals = list(always_vals)
        for col, val in zip(opt_cols, opt_vals):
            set_cols.append(col)
            set_vals.append(val)
        if _USE_SQLITE:
            updates = ", ".join(f"{c} = ?" for c in set_cols)
            await conn.execute(
                f"UPDATE spider_ai_models SET {updates} WHERE id = ? OR slug = ?",
                *set_vals, existing_id, existing_slug,
            )
        else:
            updates = ", ".join(f"{c} = ${i + 1}" for i, c in enumerate(set_cols))
            where_idx = len(set_vals) + 1
            await conn.execute(
                f"UPDATE spider_ai_models SET {updates} "
                f"WHERE id = ${where_idx} OR slug = ${where_idx + 1}",
                *set_vals, existing_id, existing_slug,
            )
    else:
        # INSERT：不含 id 的列写入 ALWAYS_COLS + 有值的 OPTIONAL_COLS
        cols = ["id"] + list(always_cols) + opt_cols
        vals = [existing_id] + list(always_vals) + opt_vals
        if _USE_SQLITE:
            placeholders = ", ".join("?" for _ in vals)
            await conn.execute(
                f"INSERT INTO spider_ai_models ({', '.join(cols)}) "
                f"VALUES ({placeholders})",
                *vals
            )
        else:
            placeholders = ", ".join(f"${i + 1}" for i in range(len(vals)))
            conflict_cols = [c for c in cols if c != "id"]
            conflict_updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in conflict_cols)
            await conn.execute(
                f"INSERT INTO spider_ai_models ({', '.join(cols)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT (slug) DO UPDATE SET {conflict_updates}",
                *vals
            )