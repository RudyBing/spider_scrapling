"""腾讯翻译君 API 封装
使用 HTTP 方式直接调用腾讯翻译 API V3 版本，不依赖 SDK

配置方式:
    在 .env 文件中添加:
    TENCENT_SECRET_ID=your_secret_id
    TENCENT_SECRET_KEY=your_secret_key

使用示例:
    from translators.tencent import TencentTranslator
    translator = TencentTranslator()
    result = await translator.translate("Hello World")
    print(result.translated_text)  # 你好世界
"""
import os
import hmac
import hashlib
import time
import json
import aiohttp
from datetime import datetime
from loguru import logger
from dataclasses import dataclass
from typing import Optional


@dataclass
class TranslateResult:
    """翻译结果"""
    source_text: str
    translated_text: str
    source_lang: str = "en"
    target_lang: str = "zh"


class TencentTranslator:
    """腾讯翻译君翻译器（使用 API V3 版本）"""
    
    def __init__(self):
        self.secret_id = os.environ.get("TENCENT_SECRET_ID", "")
        self.secret_key = os.environ.get("TENCENT_SECRET_KEY", "")
        self._check_config()
    
    def _check_config(self):
        """检查翻译配置"""
        if not self.secret_id or not self.secret_key:
            logger.warning("腾讯翻译君配置缺失：请设置 TENCENT_SECRET_ID 和 TENCENT_SECRET_KEY")
            logger.warning("翻译功能将不可用")
    
    async def translate(self, text: str, source_lang: str = "en", target_lang: str = "zh") -> TranslateResult:
        """翻译单段文本（使用腾讯翻译 API V3 版本）
        
        Args:
            text: 待翻译文本
            source_lang: 源语言 (默认 en)
            target_lang: 目标语言 (默认 zh)
        
        Returns:
            TranslateResult: 翻译结果
        
        Raises:
            RuntimeError: 配置缺失或翻译失败
        """
        if not text or not text.strip():
            return TranslateResult(
                source_text=text,
                translated_text="",
                source_lang=source_lang,
                target_lang=target_lang
            )
        
        if not self.secret_id or not self.secret_key:
            raise RuntimeError("腾讯翻译君配置缺失")
        
        try:
            # 腾讯翻译 API 端点
            endpoint = "tmt.tencentcloudapi.com"
            host = "tmt.tencentcloudapi.com"
            service = "tmt"
            version = "2018-03-21"
            action = "TextTranslate"
            region = "ap-beijing"
            
            # 准备请求体
            payload = {
                "SourceText": text,
                "Source": source_lang,
                "Target": target_lang,
                "ProjectId": 0
            }
            req_body = json.dumps(payload)
            
            # 准备签名
            timestamp = int(time.time())
            date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
            
            # 拼接签名原文
            http_request_method = "POST"
            canonical_uri = "/"
            canonical_querystring = ""
            ct = "application/json; charset=utf-8"
            canonical_headers = "content-type:%s\nhost:%s\n" % (ct, host)
            signed_headers = "content-type;host"
            hashed_request_payload = hashlib.sha256(req_body.encode("utf-8")).hexdigest()
            canonical_request = (http_request_method + "\n" +
                                 canonical_uri + "\n" +
                                 canonical_querystring + "\n" +
                                 canonical_headers + "\n" +
                                 signed_headers + "\n" +
                                 hashed_request_payload)
            
            algorithm = "TC3-HMAC-SHA256"
            credential_scope = date + "/" + service + "/" + "tc3_request"
            hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
            string_to_sign = (algorithm + "\n" +
                              str(timestamp) + "\n" +
                              credential_scope + "\n" +
                              hashed_canonical_request)
            
            # 计算签名
            def sign(key, msg):
                return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
            
            secret_date = sign(("TC3" + self.secret_key).encode("utf-8"), date)
            secret_service = sign(secret_date, service)
            secret_signing = sign(secret_service, "tc3_request")
            signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
            
            # 拼接 Authorization
            authorization = (algorithm + " " +
                             "Credential=" + self.secret_id + "/" + credential_scope + ", " +
                             "SignedHeaders=" + signed_headers + ", " +
                             "Signature=" + signature)
            
            # 准备请求头
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "Host": host,
                "X-TC-Action": action,
                "X-TC-Version": version,
                "X-TC-Timestamp": str(timestamp),
                "X-TC-Region": region,
                "Authorization": authorization
            }
            
            # 发送请求
            url = "https://" + endpoint
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=req_body) as resp:
                    result_json = await resp.json()
                    
                    if resp.status != 200:
                        raise RuntimeError(f"API 请求失败：{resp.status} - {result_json}")
                    
                    # 检查响应
                    if "Response" not in result_json:
                        raise RuntimeError(f"无效的 API 响应：{result_json}")
                    
                    # 检查错误
                    if "Error" in result_json.get("Response", {}):
                        error = result_json["Response"]["Error"]
                        raise RuntimeError(f"API 错误：{error.get('Code')} - {error.get('Message')}")
                    
                    target_text = result_json["Response"].get("TargetText", "")
                    
                    if not target_text:
                        raise RuntimeError(f"翻译结果为空：{result_json}")
                    
                    result = TranslateResult(
                        source_text=text,
                        translated_text=target_text,
                        source_lang=source_lang,
                        target_lang=target_lang
                    )
                    
                    logger.debug(f"翻译成功：{text[:30]}... -> {target_text[:30]}...")
                    return result
                    
        except Exception as e:
            logger.error(f"翻译异常：{e}")
            raise RuntimeError(f"翻译失败：{e}")
    
    async def translate_batch(
        self,
        texts: list[str],
        source_lang: str = "en",
        target_lang: str = "zh",
        max_concurrent: int = 5
    ) -> list[TranslateResult]:
        """批量翻译
        
        Args:
            texts: 待翻译文本列表
            source_lang: 源语言
            target_lang: 目标语言
            max_concurrent: 最大并发数 (避免 API 限流)
        
        Returns:
            list[TranslateResult]: 翻译结果列表
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def translate_with_limit(text: str) -> TranslateResult:
            async with semaphore:
                try:
                    result = await self.translate(text, source_lang, target_lang)
                    await asyncio.sleep(0.2)  # 避免限流 (5 次/秒)
                    return result
                except Exception as e:
                    logger.warning(f"翻译失败：{text[:50]}... - {e}")
                    # 失败时返回原文
                    return TranslateResult(
                        source_text=text,
                        translated_text=text,
                        source_lang=source_lang,
                        target_lang=target_lang
                    )
        
        tasks = [translate_with_limit(text) for text in texts]
        results = await asyncio.gather(*tasks)
        return list(results)
    
    def check_config(self) -> bool:
        """检查翻译配置是否就绪"""
        return bool(self.secret_id and self.secret_key)


# 全局单例
_translator: Optional[TencentTranslator] = None


def get_translator() -> TencentTranslator:
    """获取翻译器单例"""
    global _translator
    if _translator is None:
        _translator = TencentTranslator()
    return _translator


def check_translate_config() -> dict:
    """检查翻译配置
    
    Returns:
        dict: 配置状态
    """
    translator = get_translator()
    ready = translator.check_config()
    return {
        "tencent": ready,
        "youdao": False,  # 预留其他翻译服务
        "ready": ready
    }
