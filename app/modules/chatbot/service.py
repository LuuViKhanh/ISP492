"""
Core Engine điều phối Chatbot AI tích hợp Google Gemini API.
--------------------------------------------------------------
GHI CHÚ DÀNH CHO LẬP TRÌNH VIÊN (DEVELOPER EXPLANATION):
======================================================
File này đóng vai trò là "Bộ não trung tâm" (Shared AI Engine) dùng chung cho tất cả các Role:
- Customer Chatbot (Khách hàng)
- Operator Chatbot (Nhân viên điều phối bay)
- Technician Chatbot (Kỹ thuật viên bảo trì / pin)
- Admin Chatbot (Quản trị viên hệ thống)

Quy trình hoạt động (Agentic Tool Calling Loop):
1. Nhận message, history, system_instruction, danh sách tools tương ứng của từng Role.
2. Gửi request sang Google Gemini API (sử dụng thư viện chính thức `google-genai`).
3. Nếu Gemini yêu cầu gọi hàm (FunctionCall) -> Tìm hàm tương ứng trong `tools_map` và thực thi bất đồng bộ (Async) với DB Session.
4. Gửi kết quả JSON từ DB ngược lại cho Gemini để sinh câu trả lời tự nhiên chuẩn Markdown.

LƯU Ý VỀ TẢI ĐỒNG THỜI (CONCURRENCY & RATE LIMITS):
- Server FastAPI chạy bất đồng bộ (Async/ASGI) nên chịu tải song song rất tốt.
- Tuy nhiên, Google Gemini gói Free Tier có hạn mức 5-15 requests/phút (RPM). Khi nhiều người hỏi
  dồn dập trong 1 phút, Google có thể trả về mã 429 RESOURCE_EXHAUSTED.
- Hàm `call_gemini` bên dưới đã tích hợp cơ chế Retry tự động (tối đa 3 lần sau 1.5s delay)
  để giảm thiểu lỗi 503/429 tạm thời.
- Để triển khai Production hoặc demo nhiều người: Nên bật gói Pay-As-You-Go hoặc thêm tầng Caching (Redis).
"""

import os
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
from app.modules.chatbot.schemas import ChatHistoryItem, ChatMessageResponse


async def execute_agent_chat(
    db: AsyncSession,
    message: str,
    history: List[ChatHistoryItem],
    system_instruction: str,
    gemini_tools: Optional[List[Any]] = None,
    tools_map: Optional[Dict[str, Callable]] = None,
    current_user_id: Optional[str] = None
) -> ChatMessageResponse:
    """
    Hàm thực thi chat chung cho mọi role.

    :param db: AsyncSession kết nối Database
    :param message: Câu hỏi của người dùng
    :param history: Lịch sử đàm thoại
    :param system_instruction: Hướng dẫn vai trò riêng của từng role (Customer, Operator, Tech, Admin)
    :param gemini_tools: Danh sách Schema tools khai báo cho Gemini
    :param tools_map: Dictionary ánh xạ {tên_tool: hàm_python_thực_thi}
    :param current_user_id: ID người dùng hiện tại (nếu đã đăng nhập)
    """
    if tools_map is None:
        tools_map = {}

    # 0. Kiểm tra xem thư viện google-genai đã được cài đặt chưa
    if not GENAI_AVAILABLE:
        return ChatMessageResponse(
            reply=(
                "⚠️ **Thư viện AI (`google-genai`) chưa được cài đặt trên máy chủ backend.**\n\n"
                "👉 Lập trình viên vui lòng tắt server backend (Ctrl+C) và chạy lệnh sau trên terminal:\n"
                "```bash\n"
                "pip install google-genai==2.29.0\n"
                "```\n"
                "Sau khi cài đặt xong, hãy khởi động lại server (`uvicorn app.main:app --reload`)."
            ),
            tools_called=[],
            data=None
        )

    # 1. Kiểm tra API Key
    api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return ChatMessageResponse(
            reply=(
                "⚠️ **Chưa cấu hình API Key:** Hệ thống chưa nhận được `GEMINI_API_KEY` trong file `.env`.\n\n"
                "👉 Lập trình viên vui lòng lấy Google Gemini API Key tại https://aistudio.google.com/ "
                "và điền vào biến `GEMINI_API_KEY` trong file cấu hình `.env`."
            ),
            tools_called=[],
            data=None
        )

    model_name = settings.GEMINI_MODEL or "gemini-3.8-flash"

    # 2. Khởi tạo Gemini Client
    client = genai.Client(api_key=api_key)

    # 3. Chuẩn bị lịch sử hội thoại (Contents)
    contents: List[Any] = []
    for item in history:
        role = "user" if item.role.lower() == "user" else "model"
        contents.append(
            types.Content(
                role=role,
                parts=[types.Part.from_text(text=item.content)]
            )
        )

    # Thêm câu hỏi hiện tại
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=message)]
        )
    )

    # 4. Cấu hình Model với System Instruction và Tools của Role đó
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=gemini_tools if gemini_tools else None,
        temperature=0.3,
    )

    tools_called: List[str] = []
    tool_payload_data: Optional[Dict[str, Any]] = None

    async def call_gemini(cnts):
        for attempt in range(3):
            try:
                return await client.aio.models.generate_content(
                    model=model_name,
                    contents=cnts,
                    config=config
                )
            except Exception as e:
                err_text = str(e)
                if ("503" in err_text or "UNAVAILABLE" in err_text or "429" in err_text) and attempt < 2:
                    import asyncio
                    await asyncio.sleep(1.5)
                    continue
                raise e

    try:
        # 5. Gọi Gemini lần 1
        response = await call_gemini(contents)

        # 6. Kiểm tra xem Gemini có yêu cầu gọi Tool nào không
        if response.function_calls:
            if response.candidates and response.candidates[0].content:
                contents.append(response.candidates[0].content)

            function_response_parts: List[Any] = []

            for call in response.function_calls:
                call_name = call.name
                call_args = call.args or {}
                tools_called.append(call_name)

                # Tìm và thực thi hàm trong tools_map
                func = tools_map.get(call_name)
                if func:
                    tool_result = await func(db=db, current_user_id=current_user_id, **call_args)
                    tool_payload_data = tool_result
                else:
                    tool_result = {"error": f"Tool '{call_name}' không tồn tại trong hệ thống."}

                function_response_parts.append(
                    types.Part.from_function_response(
                        name=call_name,
                        response={"result": tool_result}
                    )
                )

            contents.append(
                types.Content(
                    role="user",
                    parts=function_response_parts
                )
            )

            # 7. Gọi Gemini lần 2 để AI tổng hợp kết quả của Tool
            followup_response = await call_gemini(contents)

            final_reply = followup_response.text or "Đã xử lý thông tin thành công."

        else:
            final_reply = response.text or "Xin lỗi, tôi chưa hiểu rõ yêu cầu. Bạn có thể nói rõ hơn không?"

        return ChatMessageResponse(
            reply=final_reply,
            tools_called=tools_called,
            data=tool_payload_data
        )

    except Exception as e:
        error_msg = str(e)
        return ChatMessageResponse(
            reply=(
                f"⚠️ **Đã xảy ra lỗi khi kết nối với Gemini AI:** `{error_msg}`\n\n"
                "Vui lòng kiểm tra lại kết nối mạng hoặc tính hợp lệ của `GEMINI_API_KEY`."
            ),
            tools_called=tools_called,
            data=None
        )
