"""
Migration: Tạo bảng knowledge_chunks cho RAG (Retrieval Augmented Generation).

Yêu cầu: pgvector extension phải được bật trên Supabase trước khi chạy.
  → Supabase Dashboard → Database → Extensions → "vector" → Enable

Chạy script này một lần:
  python app/shared/database/migrate_rag_schema.py
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from sqlalchemy import text
from app.database.db import engine


def migrate_rag_schema():
    print("🚀 Bắt đầu migrate RAG schema...")
    with engine.connect() as conn:
        try:
            # 1. Bật pgvector extension
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            print("✅ pgvector extension đã sẵn sàng.")

            # 2. Tạo bảng knowledge_chunks
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id          BIGSERIAL PRIMARY KEY,
                    role        VARCHAR(50)  NOT NULL DEFAULT 'all',
                    title       VARCHAR(500),
                    content     TEXT         NOT NULL,
                    category    VARCHAR(100),
                    embedding   vector(768),
                    created_at  TIMESTAMP    DEFAULT NOW()
                );
            """))
            print("✅ Bảng knowledge_chunks đã được tạo.")

            # 3. Index cosine similarity (IVFFlat — nhanh cho tập dữ liệu nhỏ)
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding
                ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 10);
            """))

            # 4. Index lọc theo role
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_role
                ON knowledge_chunks (role);
            """))

            conn.commit()
            print("✅ Migration RAG schema hoàn thành!")

        except Exception as e:
            conn.rollback()
            print(f"❌ Lỗi migrate: {e}")
            raise


if __name__ == "__main__":
    migrate_rag_schema()
