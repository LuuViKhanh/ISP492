"""
FDE-RAG: Tool `search_knowledge_base` dùng chung cho tất cả 4 roles.

Gemini sẽ tự gọi tool này khi cần tra cứu chính sách, quy trình, hướng dẫn
mà không có sẵn trong các tool nghiệp vụ khác.
"""

import os
import logging
from typing import Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.chatbot.rag.embedder import embed_text
from app.modules.chatbot.rag.retriever import search_chunks

logger = logging.getLogger("rag.tool")

try:
    from google.genai import types as genai_types
    _HAS_GENAI = True
except ImportError:
    genai_types = None
    _HAS_GENAI = False


# ── Gemini FunctionDeclaration (khai báo schema cho Gemini biết) ──────────────
def get_rag_function_declaration() -> Any:
    """Trả về FunctionDeclaration khai báo tool search_knowledge_base với Gemini."""
    if not _HAS_GENAI or genai_types is None:
        return None

    return genai_types.FunctionDeclaration(
        name="search_knowledge_base",
        description=(
            "Tìm kiếm thông tin trong cơ sở kiến thức nội bộ của hệ thống DroneOptAI. "
            "Dùng khi cần tra cứu: chính sách công ty, quy trình nghiệp vụ, hướng dẫn vận hành, "
            "quy định bảo trì, câu hỏi thường gặp (FAQ), hoặc bất kỳ thông tin nào "
            "không có trong các tool nghiệp vụ chuyên biệt khác."
        ),
        parameters=genai_types.Schema(
            type=genai_types.Type.OBJECT,
            properties={
                "query": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="Câu hỏi hoặc từ khóa cần tra cứu trong knowledge base.",
                ),
            },
            required=["query"],
        ),
    )


# ── Python function thực thi khi Gemini gọi tool ─────────────────────────────
async def tool_search_knowledge_base(
    db: AsyncSession,
    query: str,
    role: str = "all",
    current_user_id: Optional[str] = None,
    top_k: int = 4,
    **kwargs,
) -> dict:
    """
    Thực thi RAG search:
    1. Embed câu hỏi bằng text-embedding-004.
    2. Tìm top_k chunks liên quan nhất trong pgvector.
    3. Trả về kết quả để Gemini tổng hợp câu trả lời.

    Args:
        query: Câu hỏi người dùng (được Gemini trích xuất và truyền vào).
        role: Role hiện tại ('customer', 'operator', 'technician', 'admin').
        top_k: Số chunks trả về (mặc định 4 — cân bằng giữa chất lượng và token cost).
    """
    if not query or not query.strip():
        return {
            "success": False,
            "code": "EMPTY_QUERY",
            "message": "Câu hỏi trống.",
            "chunks": [],
        }

    api_key = os.getenv("GEMINI_API_KEY") or ""
    if not api_key:
        return {
            "success": False,
            "code": "NO_API_KEY",
            "message": "GEMINI_API_KEY chưa được cấu hình.",
            "chunks": [],
        }

    try:
        # 1. Embed câu hỏi
        query_vec = await embed_text(query.strip(), api_key)

        # 2. Tìm kiếm trong pgvector
        chunks = await search_chunks(
            db=db,
            query_embedding=query_vec,
            role=role,
            top_k=top_k,
            min_similarity=0.45,
        )

        if not chunks:
            return {
                "success": True,
                "code": "NO_RESULTS",
                "message": "Không tìm thấy thông tin liên quan trong knowledge base.",
                "query": query,
                "chunks": [],
            }

        # 3. Định dạng kết quả để Gemini dễ đọc
        formatted = []
        for i, chunk in enumerate(chunks, 1):
            formatted.append({
                "index": i,
                "title": chunk["title"],
                "content": chunk["content"],
                "category": chunk["category"],
                "similarity": chunk["similarity"],
            })

        return {
            "success": True,
            "type": "knowledge_search_result",
            "query": query,
            "result_count": len(formatted),
            "knowledge_chunks": formatted,
            "instruction": (
                "Dựa vào các đoạn thông tin trên để trả lời câu hỏi của người dùng. "
                "Chỉ sử dụng thông tin từ knowledge base, không tự suy đoán thêm."
            ),
        }

    except Exception as e:
        logger.error("RAG tool error: %s", e, exc_info=True)
        return {
            "success": False,
            "code": "RAG_ERROR",
            "message": f"Lỗi khi tra cứu knowledge base: {str(e)[:100]}",
            "chunks": [],
        }


def make_rag_tool_for_role(role: str):
    """
    Factory: tạo hàm tool đã bind sẵn role cho từng chatbot.

    Usage:
        tool_search_kb_customer = make_rag_tool_for_role("customer")
    """
    async def _tool(db: AsyncSession, query: str, current_user_id: Optional[str] = None, **kwargs):
        return await tool_search_knowledge_base(
            db=db, query=query, role=role,
            current_user_id=current_user_id,
        )
    _tool.__name__ = f"tool_search_knowledge_base_{role}"
    return _tool
