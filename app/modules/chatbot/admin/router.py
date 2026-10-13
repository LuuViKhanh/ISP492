"""
Router API cho Admin Chatbot.
Prefix: `/admin/chatbot`
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.chatbot.schemas import (
    ChatMessageRequest,
    ChatMessageResponse,
    QuickQuestionsResponse,
    QuickQuestionItem,
)
from fastapi.responses import JSONResponse
from app.modules.chatbot.service import execute_agent_chat, ChatRateLimitError
from app.modules.chatbot.admin.prompts import ADMIN_SYSTEM_INSTRUCTION
from app.modules.chatbot.admin.tools import get_admin_gemini_tools, ADMIN_TOOLS_MAP

router = APIRouter(
    prefix="/admin/chatbot",
    tags=["Chatbot"]
)

allow_admin = RoleChecker([UserRole.ADMIN])


@router.post(
    "/chat",
    response_model=ChatMessageResponse,
    summary="Đàm thoại với Trợ lý Quản trị cấp cao (Admin Executive Copilot)",
    description="Dành riêng cho Admin để xem tổng quan điều hành kinh doanh, quy mô người dùng và tài nguyên toàn hệ thống."
)
async def admin_chat(
    body: ChatMessageRequest,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db)
):
    try:
        return await execute_agent_chat(
            db=db,
            message=body.message,
            history=body.history,
            system_instruction=ADMIN_SYSTEM_INSTRUCTION,
            gemini_tools=get_admin_gemini_tools(),
            tools_map=ADMIN_TOOLS_MAP,
            current_user_id=str(user.id),
            conversation_id=body.conversation_id,
            rate_limit_key=f"admin:{user.id}",
        )
    except ChatRateLimitError as e:
        return JSONResponse(
            status_code=429,
            content={
                "code": "CHAT_RATE_LIMITED",
                "message": e.message,
                "retry_after_seconds": e.retry_after_seconds,
            },
        )


@router.get(
    "/quick-questions",
    response_model=QuickQuestionsResponse,
    summary="Câu hỏi gợi ý nhanh cho Admin"
)
async def get_admin_quick_questions(
    user: CurrentUser = Depends(allow_admin)
):
    questions = [
        QuickQuestionItem(
            id="executive_summary",
            title="📈 Báo cáo kinh doanh tổng thể",
            prompt="Báo cáo tình hình hoạt động kinh doanh, tổng doanh thu và số lượng đơn hàng"
        ),
        QuickQuestionItem(
            id="user_breakdown",
            title="👥 Cơ cấu người dùng",
            prompt="Thống kê số lượng tài khoản theo từng vai trò trong hệ thống"
        )
    ]
    return QuickQuestionsResponse(questions=questions)
