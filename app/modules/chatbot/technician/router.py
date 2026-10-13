"""
Router API cho Technician Chatbot.
Prefix: `/technician/chatbot`
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
from app.modules.chatbot.technician.prompts import TECHNICIAN_SYSTEM_INSTRUCTION
from app.modules.chatbot.technician.tools import get_technician_gemini_tools, TECHNICIAN_TOOLS_MAP

router = APIRouter(
    prefix="/technician/chatbot",
    tags=["Chatbot"]
)

allow_technician = RoleChecker([UserRole.TECHNICIAN, UserRole.ADMIN])


@router.post(
    "/chat",
    response_model=ChatMessageResponse,
    summary="Đàm thoại với Trợ lý Kỹ thuật & Bảo dưỡng (Technician Copilot)",
    description="Dành riêng cho Kỹ thuật viên và Admin để theo dõi tình trạng pin, phiếu sửa chữa và bảo dưỡng Drone."
)
async def technician_chat(
    body: ChatMessageRequest,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db)
):
    try:
        return await execute_agent_chat(
            db=db,
            message=body.message,
            history=body.history,
            system_instruction=TECHNICIAN_SYSTEM_INSTRUCTION,
            gemini_tools=get_technician_gemini_tools(),
            tools_map=TECHNICIAN_TOOLS_MAP,
            current_user_id=str(user.id),
            conversation_id=body.conversation_id,
            rate_limit_key=f"technician:{user.id}",
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
    summary="Câu hỏi gợi ý nhanh cho Kỹ thuật viên"
)
async def get_technician_quick_questions(
    user: CurrentUser = Depends(allow_technician)
):
    questions = [
        QuickQuestionItem(
            id="fleet_health",
            title="🛠️ Sức khỏe đội Drone",
            prompt="Hiện tại có bao nhiêu Drone đang cần bảo trì hoặc hỏng hóc?"
        ),
        QuickQuestionItem(
            id="low_battery",
            title="🔋 Pin cần sạc gấp",
            prompt="Kiểm tra các pin đang có dung lượng dưới 30% cần sạc ngay"
        ),
        QuickQuestionItem(
            id="pending_orders",
            title="📋 Lệnh sửa chữa tồn đọng",
            prompt="Có bao nhiêu Work Order sửa chữa đang chờ xử lý?"
        )
    ]
    return QuickQuestionsResponse(questions=questions)
