# Scrapling 爬虫系统 - 项目说明

## 架构变更（新增）

每个目标站点对应 `spiders/` 目录下的一个 Python 类，该类继承自 `SpiderBase` 并重写 `parse()` 方法。

### 添加新站点的步骤

**1. 在 `config/sites.yaml` 中添加站点：**
```yaml
sites:
  - name: "my_site"
    url: "https://example.com"
    fetcher: http          # 或 stealthy / dynamic
```

**2. 在 `spiders/articles.py`（或新建文件）中创建爬虫类：**
```python
from spiders.base import SpiderBase

class MySiteSpider(SpiderBase):
    name = "my_site"
    fetcher_type = "http"
    schedule_interval = "daily"

    SELECTORS = {
        "title":   "h1",
        "content": ".article-body",
        "author":  ".author",
        "date":    "time",
    }

    def parse(self, html: str) -> dict:
        from scrapling import Fetcher
        page = Fetcher(html_source=html)

        title   = page.css(self.SELECTORS["title"]).get_text().strip()
        content = page.css(self.SELECTORS["content"]).get_text().strip()

        return {
            "title":         title or "无标题",
            "content":       content[:10000],
            "author":        page.css(self.SELECTORS.get("author", ".author")).get_text().strip() or None,
            "published_at":  None,
            "extra":         {},
        }

# 注册到 spider registry
SPIDER_REGISTRY["my_site"] = MySiteSpider()
```

**3. 运行爬虫：**
```powershell
python main.py
```

---

## 核心设计

```
sites.yaml (入口URL)
    │
    ▼
dispatcher.py (Crawler) 匹配 name → SPIDER_REGISTRY
    │
    ▼
SpiderBase 子类 (parse 方法实现具体解析逻辑)
    │
    ▼
storage/db.py (PostgreSQL 入库)
```

---

## 依赖安装（仅需首次）

```powershell
cd D:\PythonProject\myProject
python -m venv .venv_scrapling
.\.venv_scrapling\Scripts\activate
pip install -r spider_scrapling\requirements.txt
playwright install chromium
```

## 配置

复制并编辑 `.env` 文件：
```powershell
cd spider_scrapling
copy .env.example .env
```

填入 PostgreSQL 连接串（推荐 Neon / Supabase 免费层）。

## GitHub Actions

在仓库 Settings → Secrets 中添加 `DATABASE_URL`，每天北京时间 11:00 自动运行。

---

## 翻译功能（新增）

集成腾讯翻译君 SDK，支持批量翻译英文新闻为中文。

### 配置

1. 安装翻译依赖：
```bash
pip install tencentcloud-sdk-python
```

2. 在 `.env` 文件中添加腾讯翻译君 API 密钥：
```bash
TENCENT_SECRET_ID=your_secret_id
TENCENT_SECRET_KEY=your_secret_key
```

### 使用

**命令行翻译：**
```bash
# 翻译 50 条未翻译的英文新闻
python scripts/translate_news.py

# 自定义参数
python scripts/translate_news.py --limit 100 --concurrent 5
```

**代码调用：**
```python
from services.translate_service import get_translate_service

service = get_translate_service()
result = await service.run_translation_task(limit=50)
```

### 翻译特性

- ✅ 翻译成功后自动更新 `title_cn` 和 `content_cn` 字段
- ✅ 自动设置 `is_published = TRUE`，标记为已发布
- ✅ 记录翻译时间 `translated_at` 和翻译服务 `translate_service`
- ✅ 支持并发控制，避免 API 限流

详细说明请参考 [翻译功能文档](docs/translation.md)
