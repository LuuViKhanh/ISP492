"""
FDE-RAG: Retriever — tìm kiếm các knowledge chunks liên quan nhất bằng cosine similarity trên pgvector.
"""

import json
import logging
from typing import Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("rag.retriever")


async def search_chunks(
    db: AsyncSession,
    query_embedding: list[float],
    role: str,
    top_k: int = 5,
    min_similarity: float = 0.5,
) -> list[dict]:
    """
    Tìm top_k knowledge chunks có độ tương đồng cosine cao nhất với query_embedding.

    Lọc theo role: trả về chunks của role đó VÀ chunks có role='all' (kiến thức chung).

    Args:
        db: AsyncSession.
        query_embedding: Vector 768 chiều của câu hỏi.
        role: Role cần tìm kiếm ('customer', 'operator', 'technician', 'admin').
        top_k: Số kết quả trả về (mặc định 5).
        min_similarity: Ngưỡng tương đồng tối thiểu 0-1 (mặc định 0.5).

    Returns:
        list[dict] với keys: id, role, title, content, category, similarity.
    """
    # Chuyển vector thành chuỗi PostgreSQL array literal
    vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    sql = text("""
        SELECT
            id,
            role,
            title,
            content,
            category,
            1 - (embedding <=> :embedding ::vector) AS similarity
        FROM knowledge_chunks
        WHERE (role = :role OR role = 'all')
          AND embedding IS NOT NULL
        ORDER BY embedding <=> :embedding ::vector
        LIMIT :top_k
    """)

    try:
        result = await db.execute(sql, {
            "embedding": vec_str,
            "role": role,
            "top_k": top_k,
        })
        rows = result.fetchall()
        keys = result.keys()

        chunks = []
        for row in rows:
            chunk = dict(zip(keys, row))
            sim = float(chunk.get("similarity") or 0)
            if sim >= min_similarity:
                chunks.append({
                    "id": chunk["id"],
                    "role": chunk["role"],
                    "title": chunk.get("title") or "",
                    "content": chunk["content"],
                    "category": chunk.get("category") or "",
                    "similarity": round(sim, 4),
                })

        return chunks

    except Exception as e:
        # Nếu pgvector chưa bật hoặc bảng chưa tạo → trả về list rỗng, không crash chatbot
        logger.warning("RAG search failed (pgvector chưa sẵn sàng?): %s", e)
        return []


async def count_chunks(db: AsyncSession, role: Optional[str] = None) -> int:
    """Đếm số chunks trong knowledge base (dùng cho admin monitoring)."""
    try:
        if role:
            result = await db.execute(
                text("SELECT COUNT(*) FROM knowledge_chunks WHERE role = :role OR role = 'all'"),
                {"role": role},
            )
        else:
            result = await db.execute(text("SELECT COUNT(*) FROM knowledge_chunks"))
        return result.scalar() or 0
    except Exception:
        return 0
