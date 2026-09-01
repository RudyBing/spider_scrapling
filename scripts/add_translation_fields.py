"""添加数据库缺失字段的脚本"""
import asyncio
import asyncpg
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import load_settings

async def add_fields():
    settings = load_settings()
    url = settings["DATABASE_URL"]
    
    conn = await asyncpg.connect(url)
    
    try:
        # 添加 translated_at 字段
        await conn.execute("""
            ALTER TABLE spider_news 
            ADD COLUMN IF NOT EXISTS translated_at TIMESTAMPTZ
        """)
        print("[OK] 添加 translated_at 字段成功")
        
        # 添加 translate_service 字段
        await conn.execute("""
            ALTER TABLE spider_news 
            ADD COLUMN IF NOT EXISTS translate_service VARCHAR(64)
        """)
        print("[OK] 添加 translate_service 字段成功")
        
        print("\n数据库表结构更新完成！")
        
    except Exception as e:
        print(f"[ERROR] 错误：{e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(add_fields())
