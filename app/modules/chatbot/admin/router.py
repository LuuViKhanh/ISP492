"""
API Router for Admin Chatbot.
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
    summary="Chat with the Admin Executive Copilot",
    description="Dedicated to Admins for reviewing business performance overviews, user distribution, and system-wide resources."
)
async def admin_chat(
    body: ChatMessageRequest,
    user: CurrentUser = Depends(allow_admin),
):
    try:
        return await execute_agent_chat(
            db=None,
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
    summary="Quick suggested questions for Admin"
)
async def get_admin_quick_questions(
    user: CurrentUser = Depends(allow_admin)
):
    questions = [
        QuickQuestionItem(
            id="executive_summary",
            title="Executive Business Summary",
            prompt="Summarize overall business performance, total revenue, and total order count"
        ),
        QuickQuestionItem(
            id="user_breakdown",
            title="User Role Breakdown",
            prompt="Show the breakdown of user accounts across each role in the system"
        )
    ]
    return QuickQuestionsResponse(questions=questions)
