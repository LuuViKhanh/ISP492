"""
FDE-RAG: Embedding service dùng Google text-embedding-004 (miễn phí, cùng API key với Gemini).

Chiều vector: 768 dimensions — phù hợp với pgvector(768) trong knowledge_chunks.
"""

import os
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("rag.embedder")

_EMBEDDING_MODEL = "text-embedding-004"


async def embed_text(text: str, api_key: Optional[str] = None) -> list[float]:
    """
    Embed một đoạn văn bản thành vector 768 chiều.
    Dùng Google text-embedding-004 API (miễn phí trong Gemini free tier).

    Args:
        text: Văn bản cần embed (sẽ bị truncate ở 2048 ký tự để tiết kiệm quota).
        api_key: Gemini API key. Nếu None thì đọc từ biến môi trường GEMINI_API_KEY.

    Returns:
        list[float] với 768 phần tử.
    """
    try:
        from google import genai
    except ImportError:
        raise ImportError("Cần cài đặt thư viện: pip install google-genai")

    key = api_key or os.getenv("GEMINI_API_KEY") or ""
    if not key:
        raise ValueError("GEMINI_API_KEY chưa được cấu hình trong .env")

    text = text.strip()[:2048]  # Truncate để tiết kiệm token quota
    if not text:
        return [0.0] * 768

    client = genai.Client(api_key=key)
    response = await client.aio.models.embed_content(
        model=_EMBEDDING_MODEL,
        contents=text,
    )
    return list(response.embeddings[0].values)


async def embed_batch(texts: list[str], api_key: Optional[str] = None, delay: float = 0.1) -> list[list[float]]:
    """
    Embed nhiều đoạn văn bản, có delay giữa các request để tránh rate limit.

    Args:
        texts: Danh sách văn bản.
        delay: Giây chờ giữa các request (mặc định 0.1s).

    Returns:
        list[list[float]] — mỗi phần tử là vector 768 chiều.
    """
    results = []
    for i, text in enumerate(texts):
        vec = await embed_text(text, api_key)
        results.append(vec)
        if i < len(texts) - 1:
            await asyncio.sleep(delay)
    return results
