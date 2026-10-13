"""
Router API cho Operator Chatbot.
Prefix: `/operator/chatbot`
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
from app.modules.chatbot.operator.prompts import OPERATOR_SYSTEM_INSTRUCTION
from app.modules.chatbot.operator.tools import get_operator_gemini_tools, OPERATOR_TOOLS_MAP

router = APIRouter(
    prefix="/operator/chatbot",
    tags=["Chatbot"]
)

allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])


@router.post(
    "/chat",
    response_model=ChatMessageResponse,
    summary="Đàm thoại với Trợ lý Điều phối bay (Operator Copilot)",
    description="Dành riêng cho Operator và Admin để giám sát chuyến bay, thống kê KPI và sự cố theo thời gian thực."
)
async def operator_chat(
    body: ChatMessageRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db)
):
    try:
        return await execute_agent_chat(
            db=db,
            message=body.message,
            history=body.history,
            system_instruction=OPERATOR_SYSTEM_INSTRUCTION,
            gemini_tools=get_operator_gemini_tools(),
            tools_map=OPERATOR_TOOLS_MAP,
            current_user_id=str(user.id),
            conversation_id=body.conversation_id,
            rate_limit_key=f"operator:{user.id}",
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
    summary="Câu hỏi gợi ý nhanh cho Operator"
)
async def get_operator_quick_questions(
    user: CurrentUser = Depends(allow_operator)
):
    questions = [
        QuickQuestionItem(
            id="active_flights",
            title="🚁 Chuyến bay đang hoạt động",
            prompt="Hiện tại có bao nhiêu Drone đang thực hiện chuyến bay?"
        ),
        QuickQuestionItem(
            id="kpi_report",
            title="📊 Báo cáo KPI vận hành",
            prompt="Tóm tắt hiệu suất vận hành và tỷ lệ hoàn thành chuyến bay hôm nay"
        ),
        QuickQuestionItem(
            id="recent_incidents",
            title="⚠️ Báo cáo sự cố gần đây",
            prompt="Có sự cố bay nào gần đây cần chú ý không?"
        )
    ]
    return QuickQuestionsResponse(questions=questions)
