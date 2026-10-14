"""
FDE-RAG: Seeder — đọc knowledge base từ JSON files, embed bằng Gemini, lưu vào pgvector.

Chạy một lần sau khi migrate_rag_schema.py:
  python app/modules/chatbot/rag/seeder.py

Hoặc gọi qua admin API: POST /admin/chatbot/rag/seed
"""

import sys, os
import asyncio
import json
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../")))

logger = logging.getLogger("rag.seeder")

KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "knowledge")
ROLE_FILES = {
    "customer":   "customer.json",
    "operator":   "operator.json",
    "technician": "technician.json",
    "admin":      "admin.json",
    "all":        "shared.json",
}


async def seed_knowledge_base(db, api_key: str = None, clear_first: bool = False) -> dict:
    """
    Đọc toàn bộ knowledge JSON files, embed từng chunk và insert vào bảng knowledge_chunks.

    Args:
        db: AsyncSession (có thể là cron session hoặc request session).
        api_key: Gemini API key. Nếu None thì đọc từ GEMINI_API_KEY env.
        clear_first: Nếu True, xóa toàn bộ dữ liệu cũ trước khi seed.

    Returns:
        dict với tổng số chunks đã seed theo từng role.
    """
    from sqlalchemy import text
    from app.modules.chatbot.rag.embedder import embed_text

    _api_key = api_key or os.getenv("GEMINI_API_KEY") or ""
    if not _api_key:
        raise ValueError("GEMINI_API_KEY chưa được cấu hình")

    if clear_first:
        await db.execute(text("TRUNCATE TABLE knowledge_chunks RESTART IDENTITY"))
        await db.commit()
        logger.info("[RAG Seeder] Đã xóa knowledge base cũ.")

    total_seeded = {}

    for role, filename in ROLE_FILES.items():
        filepath = os.path.join(KNOWLEDGE_DIR, filename)
        if not os.path.exists(filepath):
            logger.warning("[RAG Seeder] Không tìm thấy file: %s — bỏ qua.", filepath)
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            chunks: list[dict] = json.load(f)

        logger.info("[RAG Seeder] Seeding %d chunks cho role '%s'...", len(chunks), role)
        seeded = 0

        for chunk in chunks:
            content = chunk.get("content", "").strip()
            if not content:
                continue

            title = chunk.get("title", "")
            category = chunk.get("category", "general")

            # Embed: title + content để tăng chất lượng retrieval
            text_to_embed = f"{title}\n{content}" if title else content

            try:
                embedding = await embed_text(text_to_embed, _api_key)
            except Exception as e:
                logger.error("[RAG Seeder] Embed lỗi chunk '%s': %s", title, e)
                continue

            vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
            await db.execute(
                text("""
                    INSERT INTO knowledge_chunks (role, title, content, category, embedding)
                    VALUES (:role, :title, :content, :category, :embedding::vector)
                """),
                {
                    "role": role,
                    "title": title,
                    "content": content,
                    "category": category,
                    "embedding": vec_str,
                },
            )
            seeded += 1
            await asyncio.sleep(0.05)  # tránh rate limit Embedding API

        await db.commit()
        total_seeded[role] = seeded
        logger.info("[RAG Seeder] ✅ %d chunks seeded cho role '%s'.", seeded, role)

    return total_seeded


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    async def _main():
        from app.database.db import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            result = await seed_knowledge_base(session, clear_first=True)
            print("Kết quả seed:", result)

    asyncio.run(_main())
