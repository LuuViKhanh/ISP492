"""
Router API cho Customer Chatbot.
Prefix: `/customer/chatbot`
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database.db import get_async_db
from app.shared.dependencies import get_optional_current_user, CurrentUser
from app.core.config import settings
from app.modules.chatbot.schemas import (
    ChatMessageRequest,
    ChatMessageResponse,
    QuickQuestionsResponse,
    QuickQuestionItem,
)
from app.modules.chatbot.service import execute_agent_chat
from app.modules.chatbot.customer.prompts import CUSTOMER_SYSTEM_INSTRUCTION
from app.modules.chatbot.customer.tools import get_customer_gemini_tools, CUSTOMER_TOOLS_MAP

router = APIRouter(
    prefix="/customer/chatbot",
    tags=["Chatbot"]
)


@router.post(
    "/chat",
    response_model=ChatMessageResponse,
    summary="Đàm thoại với Trợ lý ảo Khách hàng (Customer AI)",
    description=(
        "**[Customer Chatbot]** Hỗ trợ khách hàng:\n"
        "1. Tra cứu tiến độ, trạng thái và chi tiết đơn hàng.\n"
        "2. Tính cước phí giao hàng ước tính giữa 2 Hub (tích hợp AI dự đoán năng lượng Drone).\n"
        "3. Xem danh sách các Hub và tra cứu đơn hàng gần đây của tài khoản."
    )
)
async def customer_chat(
    body: ChatMessageRequest,
    current_user: Optional[CurrentUser] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    user_id = current_user.id if current_user else None

    return await execute_agent_chat(
        db=db,
        message=body.message,
        history=body.history,
        system_instruction=CUSTOMER_SYSTEM_INSTRUCTION,
        gemini_tools=get_customer_gemini_tools(),
        tools_map=CUSTOMER_TOOLS_MAP,
        current_user_id=user_id
    )


@router.get(
    "/quick-questions",
    response_model=QuickQuestionsResponse,
    summary="Câu hỏi gợi ý nhanh cho Khách hàng"
)
async def get_quick_questions():
    questions = [
        QuickQuestionItem(
            id="track_order",
            title="📦 Tra cứu đơn hàng",
            prompt="Kiểm tra trạng thái đơn hàng ORD-0001 giúp tôi"
        ),
        QuickQuestionItem(
            id="estimate_fee",
            title="💰 Tính cước vận chuyển",
            prompt="Tính chi phí giao hàng từ Hub Hòa Lạc sang Hub Cầu Giấy với kiện hàng 1.5kg"
        ),
        QuickQuestionItem(
            id="list_hubs",
            title="📍 Danh sách các trạm Hub",
            prompt="Hệ thống DroneOptAI hiện đang hỗ trợ giao nhận ở những trạm Hub nào?"
        ),
        QuickQuestionItem(
            id="my_orders",
            title="📋 Đơn hàng của tôi",
            prompt="Xem danh sách các đơn hàng gần đây của tôi"
        )
    ]
    return QuickQuestionsResponse(questions=questions)
