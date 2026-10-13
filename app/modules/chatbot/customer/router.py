"""
Router API cho Customer Chatbot (Chuẩn FE/BE Sync Spec v1.1).
Prefix: `/customer/chatbot`
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database.db import get_async_db
from app.shared.dependencies import get_optional_current_user, CurrentUser
from app.modules.chatbot.schemas import (
    ChatMessageRequest,
    ChatMessageResponse,
    QuickQuestionsResponse,
    QuickQuestionItem,
)
from app.modules.chatbot.service import execute_agent_chat, ChatRateLimitError
from app.modules.chatbot.customer.prompts import CUSTOMER_SYSTEM_INSTRUCTION
from app.modules.chatbot.customer.tools import get_customer_gemini_tools, CUSTOMER_TOOLS_MAP

router = APIRouter(
    prefix="/customer/chatbot",
    tags=["Chatbot"],
)


@router.post(
    "/chat",
    response_model=ChatMessageResponse,
    summary="Đàm thoại với Trợ lý ảo Khách hàng (Customer AI v1.1)",
    description=(
        "**[Customer Chatbot v1.1]** Hỗ trợ khách hàng toàn trình (18 canonical tools):\n"
        "1. Quy định kiện hàng (`get_package_rules`), tư vấn chọn size (`recommend_package_size`).\n"
        "2. Bảng giá & phụ phí (`get_pricing_rules`, `get_delivery_modes`), báo giá thực tế (`estimate_shipping_fee`).\n"
        "3. Tìm kiếm trạm Hub (`list_hubs`, `resolve_hubs_by_area`) và chính sách (`get_help_policy`).\n"
        "4. Tra cứu đơn hàng cá nhân (`get_my_orders`, `get_latest_order`, `get_order_detail`, `get_customer_delivery_status`).\n"
        "5. Thanh toán, bàn giao hàng, ETA, kiểm tra hủy đơn, thông báo & hồ sơ (bắt buộc Bearer JWT + xác thực quyền sở hữu)."
    ),
)
async def customer_chat(
    request: Request,
    body: ChatMessageRequest,
    current_user: Optional[CurrentUser] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    user_id = str(current_user.id) if current_user else None
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"customer:{user_id or client_ip}"

    try:
        return await execute_agent_chat(
            db=db,
            message=body.message,
            history=body.history,
            system_instruction=CUSTOMER_SYSTEM_INSTRUCTION,
            gemini_tools=get_customer_gemini_tools(),
            tools_map=CUSTOMER_TOOLS_MAP,
            current_user_id=user_id,
            conversation_id=body.conversation_id,
            rate_limit_key=rate_key,
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
    summary="Câu hỏi gợi ý nhanh cho Khách hàng (8 chuẩn theo FE Spec)",
)
async def get_quick_questions():
    questions = [
        QuickQuestionItem(
            id="track_latest",
            title="Track my latest delivery",
            prompt="Kiểm tra trạng thái đơn gần nhất của tôi",
        ),
        QuickQuestionItem(
            id="estimate_fee",
            title="Estimate delivery fee",
            prompt="Tôi muốn tính thử phí giao hàng",
        ),
        QuickQuestionItem(
            id="package_size",
            title="Choose package size",
            prompt="Tôi nên chọn size kiện hàng nào?",
        ),
        QuickQuestionItem(
            id="delivery_modes",
            title="Scheduled or Express?",
            prompt="Express và Scheduled khác nhau thế nào?",
        ),
        QuickQuestionItem(
            id="find_hub",
            title="Find a delivery hub",
            prompt="DroneOptAI có những hub nào?",
        ),
        QuickQuestionItem(
            id="handover",
            title="Package handover",
            prompt="Handover deadline là gì?",
        ),
        QuickQuestionItem(
            id="payment_help",
            title="Payment help",
            prompt="Tôi cần thanh toán đơn hàng như thế nào?",
        ),
        QuickQuestionItem(
            id="recent_orders",
            title="My recent deliveries",
            prompt="Cho tôi xem các đơn gần đây",
        ),
    ]
    return QuickQuestionsResponse(questions=questions)
