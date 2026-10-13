"""
FDE-RAG: Admin API để quản lý RAG knowledge base.
Prefix: /admin/chatbot/rag
"""

import os
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker
from app.shared.roles import UserRole
from app.modules.chatbot.rag.retriever import count_chunks
from app.modules.chatbot.rag.seeder import seed_knowledge_base

router = APIRouter(prefix="/admin/chatbot/rag", tags=["Admin - RAG Knowledge Base"])
require_admin = RoleChecker([UserRole.ADMIN])


@router.get("/stats")
async def get_rag_stats(
    db: AsyncSession = Depends(get_async_db),
    _=Depends(require_admin),
):
    """Xem thống kê knowledge base: số chunks theo từng role."""
    roles = ["customer", "operator", "technician", "admin", "all"]
    stats = {}
    for role in roles:
        stats[role] = await count_chunks(db, role)
    stats["total"] = await count_chunks(db)
    return {"status": "ok", "chunks_by_role": stats}


@router.post("/seed")
async def seed_rag(
    clear_first: bool = False,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(require_admin),
):
    """
    Seed (hoặc re-seed) toàn bộ knowledge base.

    - clear_first=false (mặc định): thêm mới, không xóa data cũ.
    - clear_first=true: xóa toàn bộ và seed lại từ đầu.

    ⚠️ Yêu cầu:
    - GEMINI_API_KEY phải được cấu hình trong .env.
    - pgvector extension phải được bật trên Supabase.
    - Đã chạy migrate_rag_schema.py để tạo bảng knowledge_chunks.
    """
    api_key = os.getenv("GEMINI_API_KEY") or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY chưa được cấu hình trong .env")

    try:
        result = await seed_knowledge_base(db, api_key=api_key, clear_first=clear_first)
        total = sum(result.values())
        return {
            "status": "success",
            "message": f"Đã seed {total} chunks vào knowledge base.",
            "detail_by_role": result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi seed: {str(e)}")


@router.delete("/clear")
async def clear_rag(
    db: AsyncSession = Depends(get_async_db),
    _=Depends(require_admin),
):
    """Xóa toàn bộ knowledge base (dùng khi cần reset)."""
    from sqlalchemy import text
    await db.execute(text("TRUNCATE TABLE knowledge_chunks RESTART IDENTITY"))
    await db.commit()
    return {"status": "ok", "message": "Đã xóa toàn bộ knowledge base."}
