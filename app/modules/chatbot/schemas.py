from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict


class ChatHistoryItem(BaseModel):
    """
    Một tin nhắn trong lịch sử trò chuyện (Multi-turn conversation).
    - role: "user" (người dùng) hoặc "model" (trợ lý ảo AI)
    - content: nội dung tin nhắn dạng văn bản
    """
    role: str = Field(..., description="Vai trò: 'user' hoặc 'model'")
    content: str = Field(..., description="Nội dung tin nhắn")


class ChatMessageRequest(BaseModel):
    """
    Dữ liệu đầu vào khi người dùng gửi tin nhắn cho Chatbot.
    """
    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Nội dung câu hỏi của khách hàng (VD: 'Đơn ORD-001 của tôi đến đâu rồi?')"
    )
    history: List[ChatHistoryItem] = Field(
        default_factory=list,
        description="Lịch sử các lượt chat trước đó để AI duy trì ngữ cảnh đàm thoại"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "message": "Tính phí giao hàng từ Hub Hòa Lạc sang Hub Cầu Giấy kiện 2kg chuyển phát nhanh",
                "history": []
            }
        }


class ChatMessageResponse(BaseModel):
    """
    Kết quả trả về từ Chatbot AI cho giao diện người dùng.
    """
    reply: str = Field(
        ...,
        description="Câu trả lời tự nhiên của Gemini AI bằng tiếng Việt"
    )
    tools_called: List[str] = Field(
        default_factory=list,
        description="Danh sách tên các Tool/Function mà AI đã tự động kích hoạt (VD: ['estimate_shipping_fee'])"
    )
    data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Dữ liệu thô từ Tool (nếu có) để Frontend có thể hiển thị dạng Card/Widget chuyên dụng"
    )


class QuickQuestionItem(BaseModel):
    id: str
    title: str
    prompt: str


class QuickQuestionsResponse(BaseModel):
    questions: List[QuickQuestionItem]
