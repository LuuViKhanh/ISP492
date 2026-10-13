"""
Core Engine điều phối Chatbot AI tích hợp Google Gemini API (Contract v1.1).
----------------------------------------------------------------------------
GHI CHÚ DÀNH CHO LẬP TRÌNH VIÊN (DEVELOPER EXPLANATION):
======================================================
File này đóng vai trò là "Bộ não trung tâm" (Shared AI Engine) dùng chung cho tất cả các Role:
- Customer Chatbot (Khách hàng - 18 canonical tools)
- Operator Chatbot (Nhân viên điều phối bay)
- Technician Chatbot (Kỹ thuật viên bảo trì / pin)
- Admin Chatbot (Quản trị viên hệ thống)

Nâng cấp theo `DroneOptAI_Customer_Chatbot_FE_BE_Sync_Spec` v1.1:
1. Quản lý `conversation_id` server-side kết hợp giới hạn tối đa 10 messages gần nhất.
2. Bảo vệ Quota bằng Rate Limiter theo `user_id` / IP trước khi gọi Gemini, trả về chuẩn HTTP 429:
   `{"code": "CHAT_RATE_LIMITED", "message": "Assistant is temporarily busy.", "retry_after_seconds": 30}`.
3. Tự động bóc tách `_actions` và chuẩn hóa `data.type` (14 loại chuẩn) + `meta.request_id` để FE render
   UI Card và Action Buttons mà không cần dùng Regex.
"""

import os
import time
import uuid
import asyncio
from collections import defaultdict, deque
from typing import List, Dict, Any, Optional, Callable
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    genai = None
    types = None
    GENAI_AVAILABLE = False

from app.core.config import settings
from app.modules.chatbot.schemas import (
    ChatHistoryItem,
    ChatMessageResponse,
    ChatActionItem,
    ChatMeta,
)

# ── Quản lý Lịch sử Hội thoại & Rate Limiting trên RAM ────────────────────────
MAX_HISTORY_MESSAGES = 10
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 12  # Tối đa 12 requests / 60s cho mỗi user/IP

_CONVERSATION_STORE: Dict[str, List[ChatHistoryItem]] = {}
_RATE_LIMIT_BUCKETS: Dict[str, deque] = defaultdict(deque)


class ChatRateLimitError(Exception):
    """Ngoại lệ khi vượt quá giới hạn tần suất gọi Chatbot (HTTP 429)."""

    def __init__(self, retry_after_seconds: int = 30, message: str = "Assistant is temporarily busy."):
        self.retry_after_seconds = retry_after_seconds
        self.message = message
        super().__init__(message)


def check_rate_limit(rate_key: str) -> None:
    """Kiểm tra tần suất gửi tin nhắn theo user_id hoặc IP."""
    now = time.time()
    bucket = _RATE_LIMIT_BUCKETS[rate_key]
    while bucket and (now - bucket[0]) > RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        oldest = bucket[0]
        retry_after = max(int(RATE_LIMIT_WINDOW_SECONDS - (now - oldest)) + 1, 5)
        raise ChatRateLimitError(
            retry_after_seconds=retry_after,
            message="Assistant is temporarily busy. Please wait before sending another message.",
        )
    bucket.append(now)


async def execute_agent_chat(
    db: AsyncSession,
    message: str,
    history: List[ChatHistoryItem],
    system_instruction: str,
    gemini_tools: Optional[List[Any]] = None,
    tools_map: Optional[Dict[str, Callable]] = None,
    current_user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    rate_limit_key: Optional[str] = None,
) -> ChatMessageResponse:
    """
    Hàm thực thi chat chung cho mọi role theo chuẩn Contract v1.1.
    """
    if tools_map is None:
        tools_map = {}

    conv_id = (conversation_id or "").strip() or f"chat_{uuid.uuid4().hex[:12]}"
    req_id = f"req_{uuid.uuid4().hex[:10]}"
    meta = ChatMeta(request_id=req_id)

    # Kiểm tra Rate Limit theo user/IP trước khi gọi Gemini
    effective_rate_key = rate_limit_key or (f"user:{current_user_id}" if current_user_id else f"conv:{conv_id}")
    check_rate_limit(effective_rate_key)

    # 0. Kiểm tra xem thư viện google-genai đã được cài đặt chưa
    if not GENAI_AVAILABLE:
        return ChatMessageResponse(
            conversation_id=conv_id,
            reply=(
                "⚠️ **Thư viện AI (`google-genai`) chưa được cài đặt trên máy chủ backend.**\n\n"
                "👉 Lập trình viên vui lòng tắt server backend (Ctrl+C) và chạy lệnh:\n"
                "```bash\n"
                "pip install google-genai==2.29.0\n"
                "```"
            ),
            tools_called=[],
            data={"type": "generic_answer", "code": "AI_UNAVAILABLE"},
            actions=[],
            meta=meta,
        )

    # 1. Kiểm tra API Key
    api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return ChatMessageResponse(
            conversation_id=conv_id,
            reply=(
                "⚠️ **Chưa cấu hình API Key:** Hệ thống chưa nhận được `GEMINI_API_KEY` trong file `.env`."
            ),
            tools_called=[],
            data={"type": "generic_answer", "code": "AI_UNAVAILABLE"},
            actions=[],
            meta=meta,
        )

    primary_model = settings.GEMINI_MODEL or "gemini-3.5-flash-lite"
    fallback_models = []
    for m in [primary_model, "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.8-flash"]:
        if m and m not in fallback_models:
            fallback_models.append(m)

    # Tắt vòng lặp retry ngầm (tenacity 5 lần) của google-genai SDK để tránh treo request 60-120s khi gặp 503
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=12000,  # 12s (Google API yêu cầu tối thiểu 10s)
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )

    # 2. Chuẩn bị lịch sử hội thoại (giới hạn 10 tin nhắn gần nhất)
    store_key = f"{current_user_id or 'anon'}:{conv_id}"
    effective_history = list(history) if history else list(_CONVERSATION_STORE.get(store_key, []))
    effective_history = effective_history[-MAX_HISTORY_MESSAGES:]

    contents: List[Any] = []
    for item in effective_history:
        role = "user" if item.role.lower() == "user" else "model"
        contents.append(
            types.Content(
                role=role,
                parts=[types.Part.from_text(text=item.content[:2000])],
            )
        )

    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=message[:2000])],
        )
    )

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=gemini_tools if gemini_tools else None,
        temperature=0.2,
    )

    tools_called: List[str] = []
    merged_data: Optional[Dict[str, Any]] = None
    collected_actions: List[ChatActionItem] = []

    async def call_gemini(cnts):
        last_err = None
        for candidate_model in fallback_models:
            try:
                return await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=candidate_model,
                        contents=cnts,
                        config=config,
                    ),
                    timeout=12.5,
                )
            except asyncio.TimeoutError as e:
                last_err = e
                continue
            except Exception as e:
                last_err = e
                err_text = str(e)
                if any(code in err_text for code in ("503", "UNAVAILABLE", "504", "DEADLINE_EXCEEDED", "404", "NOT_FOUND")):
                    continue
                if "429" in err_text or "RESOURCE_EXHAUSTED" in err_text:
                    raise ChatRateLimitError(
                        retry_after_seconds=30,
                        message="Assistant is temporarily busy.",
                    )
                raise e
        raise last_err or RuntimeError("All Gemini fallback models timed out or are unavailable.")

    try:
        # 3. Gọi Gemini lần 1 (Intent & Tool Routing)
        response = await call_gemini(contents)

        # 4. Thực thi các Tool Calls nếu Gemini yêu cầu
        if response.function_calls:
            if response.candidates and response.candidates[0].content:
                contents.append(response.candidates[0].content)

            function_response_parts: List[Any] = []

            for call in response.function_calls:
                call_name = call.name
                call_args = call.args or {}
                tools_called.append(call_name)

                func = tools_map.get(call_name)
                if func:
                    raw_result = await func(db=db, current_user_id=current_user_id, **call_args)
                    if isinstance(raw_result, dict):
                        tool_result = dict(raw_result)
                        raw_actions = tool_result.pop("_actions", None) or []
                        for act in raw_actions:
                            if isinstance(act, dict) and act.get("type") and act.get("label"):
                                collected_actions.append(ChatActionItem(**act))
                        if merged_data is None:
                            merged_data = dict(tool_result)
                        else:
                            # Giữ type của tool chính hoặc cập nhật các trường bổ sung
                            for k, v in tool_result.items():
                                if k == "type" and merged_data.get("type"):
                                    continue
                                merged_data[k] = v
                    else:
                        tool_result = {"result": raw_result}
                else:
                    tool_result = {
                        "type": "generic_answer",
                        "code": "TOOL_UNAVAILABLE",
                        "error": f"Tool '{call_name}' không tồn tại trong hệ thống.",
                    }

                function_response_parts.append(
                    types.Part.from_function_response(
                        name=call_name,
                        response={"result": tool_result},
                    )
                )

            contents.append(
                types.Content(
                    role="user",
                    parts=function_response_parts,
                )
            )

            # 5. Gọi Gemini lần 2 để diễn đạt kết quả từ dữ liệu thật của Tool
            followup_response = await call_gemini(contents)
            final_reply = followup_response.text or "Đã xử lý thông tin thành công."
        else:
            final_reply = (
                response.text
                or "Xin lỗi, tôi chưa hiểu rõ yêu cầu. Bạn có thể nói rõ hơn không?"
            )

        # Đảm bảo `data` luôn có trường `type` chuẩn để FE render không cần regex
        if merged_data is None:
            merged_data = {"type": "generic_answer"}
        elif "type" not in merged_data:
            merged_data["type"] = "generic_answer"

        # Dedup actions theo (type, href)
        unique_actions: List[ChatActionItem] = []
        seen_act = set()
        for a in collected_actions:
            key = (a.type, a.href or "")
            if key not in seen_act:
                seen_act.add(key)
                unique_actions.append(a)

        # Lưu lịch sử hội thoại theo conversation_id (tối đa 10 messages)
        updated_history = effective_history + [
            ChatHistoryItem(role="user", content=message[:2000]),
            ChatHistoryItem(role="model", content=final_reply[:2000]),
        ]
        _CONVERSATION_STORE[store_key] = updated_history[-MAX_HISTORY_MESSAGES:]

        return ChatMessageResponse(
            conversation_id=conv_id,
            reply=final_reply,
            tools_called=tools_called,
            data=merged_data,
            actions=unique_actions,
            meta=meta,
        )

    except ChatRateLimitError:
        raise
    except Exception as e:
        error_msg = str(e)
        return ChatMessageResponse(
            conversation_id=conv_id,
            reply=(
                f"⚠️ **Đã xảy ra lỗi khi kết nối với dịch vụ AI:** `{error_msg}`\n\n"
                "Vui lòng thử lại sau giây lát."
            ),
            tools_called=tools_called,
            data={"type": "generic_answer", "code": "AI_UNAVAILABLE"},
            actions=[],
            meta=meta,
        )
