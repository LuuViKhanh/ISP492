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
    summary="Chat with the Customer AI Assistant (Customer AI v1.1)",
    description=(
        "**[Customer Chatbot v1.1]** End-to-end customer support (18 canonical tools):\n"
        "1. Package rules (`get_package_rules`) and size recommendation (`recommend_package_size`).\n"
        "2. Pricing rules & surcharges (`get_pricing_rules`, `get_delivery_modes`) and shipping fee quotation (`estimate_shipping_fee`).\n"
        "3. Hub lookup (`list_hubs`, `resolve_hubs_by_area`) and help policies (`get_help_policy`).\n"
        "4. Personal order tracking (`get_my_orders`, `get_latest_order`, `get_order_detail`, `get_customer_delivery_status`).\n"
        "5. Payment status, handover status, ETA, cancellation eligibility, notifications & profile (requires Bearer JWT + ownership check)."
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
    summary="Quick suggested questions for Customers (8 standard items per FE Spec)",
)
async def get_quick_questions():
    questions = [
        QuickQuestionItem(
            id="track_latest",
            title="Track my latest delivery",
            prompt="Check the status of my latest delivery order",
        ),
        QuickQuestionItem(
            id="estimate_fee",
            title="Estimate delivery fee",
            prompt="I want to estimate a delivery shipping fee",
        ),
        QuickQuestionItem(
            id="package_size",
            title="Choose package size",
            prompt="Which package size should I choose?",
        ),
        QuickQuestionItem(
            id="delivery_modes",
            title="Scheduled or Express?",
            prompt="What is the difference between Express and Scheduled delivery?",
        ),
        QuickQuestionItem(
            id="find_hub",
            title="Find a delivery hub",
            prompt="Which delivery hubs does DroneOptAI operate?",
        ),
        QuickQuestionItem(
            id="handover",
            title="Package handover",
            prompt="What is the handover deadline?",
        ),
        QuickQuestionItem(
            id="payment_help",
            title="Payment help",
            prompt="How do I pay for my delivery order?",
        ),
        QuickQuestionItem(
            id="recent_orders",
            title="My recent deliveries",
            prompt="Show my recent delivery orders",
        ),
    ]
    return QuickQuestionsResponse(questions=questions)
