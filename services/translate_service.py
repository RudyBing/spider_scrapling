"""翻译服务层 - 管理新闻翻译任务
提供批量翻译、任务调度、进度跟踪等功能
"""
import asyncio
from datetime import datetime
from loguru import logger
from typing import Optional
from storage.db import get_pool
from translators.tencent import get_translator, TranslateResult


class TranslateService:
    """新闻翻译服务"""
    
    def __init__(self):
        self.translator = get_translator()
    
    async def translate_news_item(
        self,
        news_id: str,
        title: str,
        content: str = "",
        source_lang: str = "en",
        target_lang: str = "zh"
    ) -> dict:
        """翻译单条新闻
        
        Args:
            news_id: 新闻 ID
            title: 新闻标题
            content: 新闻内容
            source_lang: 源语言
            target_lang: 目标语言
        
        Returns:
            dict: 翻译后的数据
        """
        try:
            # 翻译标题
            title_result = await self.translator.translate(title, source_lang, target_lang)
            
            # 翻译内容（如果有）
            content_result: Optional[TranslateResult] = None
            if content and content.strip():
                # 内容太长则截断（腾讯翻译限制 2000 字符/次）
                content_to_translate = content[:2000] if len(content) > 2000 else content
                content_result = await self.translator.translate(
                    content_to_translate, source_lang, target_lang
                )
            
            return {
                "title_cn": title_result.translated_text,
                "content_cn": content_result.translated_text if content_result else None,
                "language": target_lang,
                "translate_service": "腾讯翻译君",
            }
            
        except Exception as e:
            logger.error(f"翻译新闻 {news_id} 失败：{e}")
            # 翻译失败时返回空值，保留原文
            return {
                "title_cn": None,
                "content_cn": None,
            }
    
    async def translate_batch_news(
        self,
        news_list: list[dict],
        source_lang: str = "en",
        target_lang: str = "zh",
        max_concurrent: int = 3
    ) -> tuple[int, int]:
        """批量翻译新闻
        
        Args:
            news_list: 新闻列表，每项包含 id, title, content
            source_lang: 源语言
            target_lang: 目标语言
            max_concurrent: 最大并发数
        
        Returns:
            tuple[int, int]: (成功数量，失败数量)
        """
        logger.info(f"开始批量翻译 {len(news_list)} 条新闻")
        
        semaphore = asyncio.Semaphore(max_concurrent)
        success_count = 0
        fail_count = 0
        
        async def translate_one(news: dict):
            nonlocal success_count, fail_count
            
            async with semaphore:
                try:
                    result = await self.translate_news_item(
                        news_id=news["id"],
                        title=news["title"],
                        content=news.get("content", ""),
                        source_lang=source_lang,
                        target_lang=target_lang
                    )
                    
                    # 更新数据库
                    await self._update_news_translation(news["id"], result)
                    success_count += 1
                    logger.info(f"✅ {news['title'][:30]}... 翻译成功")
                    
                except Exception as e:
                    logger.error(f"❌ {news['title'][:30]}... 翻译失败：{e}")
                    fail_count += 1
                
                # 避免 API 限流
                await asyncio.sleep(0.2)
        
        tasks = [translate_one(news) for news in news_list]
        await asyncio.gather(*tasks)
        
        logger.info(f"批量翻译完成：成功 {success_count} 条，失败 {fail_count} 条")
        return success_count, fail_count
    
    async def _update_news_translation(self, news_id: str, translation: dict):
        """更新新闻翻译结果到数据库"""
        pool = await get_pool()
        if pool is None:
            logger.error("数据库未连接")
            return
        
        async with pool.acquire() as conn:
            # 构建 UPDATE SQL
            updates = []
            values = []
            
            if translation.get("title_cn"):
                updates.append("title_cn = $%d" % (len(values) + 1))
                values.append(translation["title_cn"])
            
            if translation.get("content_cn"):
                updates.append("content_cn = $%d" % (len(values) + 1))
                values.append(translation["content_cn"])
            
            if translation.get("translate_service"):
                updates.append("translate_service = $%d" % (len(values) + 1))
                values.append(translation["translate_service"])
            
            if updates:
                # 添加 translated_at、updated_at 和 is_published，使用数据库 CURRENT_TIMESTAMP
                updates.append("translated_at = CURRENT_TIMESTAMP")
                updates.append("updated_at = CURRENT_TIMESTAMP")
                updates.append("is_published = TRUE")
                
                values.append(news_id)
                sql = f"UPDATE spider_news SET {', '.join(updates)} WHERE id = ${len(values)}"
                await conn.execute(sql, *values)
    
    async def get_untranslated_news(
        self,
        limit: int = 100,
        source_lang: str = "en"
    ) -> list[dict]:
        """获取未翻译的新闻
        
        Args:
            limit: 限制数量
            source_lang: 源语言
        
        Returns:
            list[dict]: 未翻译的新闻列表
        """
        pool = await get_pool()
        if pool is None:
            logger.error("数据库未连接")
            return []
        
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, title, content, original_url, published_at
                FROM spider_news
                WHERE language = $1
                  AND (title_cn IS NULL OR content_cn IS NULL)
                ORDER BY published_at DESC
                LIMIT $2
                """,
                source_lang,
                limit
            )
            return rows
    
    async def run_translation_task(
        self,
        limit: int = 50,
        source_lang: str = "en",
        target_lang: str = "zh",
        max_concurrent: int = 3
    ) -> dict:
        """运行翻译任务
        
        Args:
            limit: 每次翻译的最大数量
            source_lang: 源语言
            target_lang: 目标语言
            max_concurrent: 最大并发数
        
        Returns:
            dict: 翻译统计信息
        """
        logger.info("=" * 60)
        logger.info("🌐 开始翻译任务")
        logger.info("=" * 60)
        
        # 检查配置
        if not self.translator.check_config():
            logger.error("翻译配置未就绪，请先配置 TENCENT_SECRET_ID 和 TENCENT_SECRET_KEY")
            return {"success": 0, "fail": 0, "error": "配置缺失"}
        
        # 获取未翻译的新闻
        untranslated = await self.get_untranslated_news(limit, source_lang)
        
        if not untranslated:
            logger.info("✅ 没有待翻译的新闻")
            return {"success": 0, "fail": 0}
        
        logger.info(f"📋 待翻译新闻：{len(untranslated)} 条")
        
        # 执行批量翻译
        success, fail = await self.translate_batch_news(
            untranslated,
            source_lang,
            target_lang,
            max_concurrent
        )
        
        logger.info("=" * 60)
        logger.info(f"✅ 翻译任务完成：成功 {success} 条，失败 {fail} 条")
        logger.info("=" * 60)
        
        return {"success": success, "fail": fail}


# 全局单例
_translate_service: Optional[TranslateService] = None


def get_translate_service() -> TranslateService:
    """获取翻译服务单例"""
    global _translate_service
    if _translate_service is None:
        _translate_service = TranslateService()
    return _translate_service
