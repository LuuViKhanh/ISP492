from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Any, Dict


class ChatHistoryItem(BaseModel):
    """
    Một tin nhắn trong lịch sử trò chuyện (Multi-turn conversation).
    - role: "user" (người dùng) hoặc "model" (trợ lý ảo AI)
    - content: nội dung tin nhắn dạng văn bản
    """
    role: str = Field(..., description="Vai trò: 'user' hoặc 'model'")
    content: str = Field(..., max_length=4000, description="Nội dung tin nhắn")

    @field_validator("role")
    @classmethod
    def normalize_role(cls, v: str) -> str:
        role_lower = (v or "user").strip().lower()
        if role_lower in ("user", "customer", "human"):
            return "user"
        return "model"


class ChatMessageRequest(BaseModel):
    """
    Dữ liệu đầu vào khi người dùng gửi tin nhắn cho Chatbot (Contract v1.1).
    """
    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Nội dung câu hỏi của khách hàng"
    )
    conversation_id: Optional[str] = Field(
        default=None,
        max_length=100,
        description="ID phiên hội thoại (nếu tiếp tục cuộc trò chuyện trước đó)"
    )
    history: List[ChatHistoryItem] = Field(
        default_factory=list,
        description="Lịch sử các lượt chat trước đó (hệ thống tự động giới hạn tối đa 10 tin nhắn gần nhất)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "message": "Đơn gần nhất của tôi đã thanh toán chưa?",
                "conversation_id": "chat_abc123",
                "history": []
            }
        }


class ChatActionItem(BaseModel):
    """
    Hành động điều hướng gợi ý trên giao diện Frontend (Action Button).
    Các type chuẩn: CREATE_DELIVERY | OPEN_ORDER | OPEN_PAYMENT | OPEN_PRICING | OPEN_HELP | OPEN_NOTIFICATIONS
    """
    type: str = Field(..., description="Loại action chuẩn")
    label: str = Field(..., description="Nhãn hiển thị trên nút bấm")
    href: Optional[str] = Field(default=None, description="Đường dẫn điều hướng trên FE")
    payload: Optional[Dict[str, Any]] = Field(default=None, description="Dữ liệu đính kèm (ví dụ prefill tạo đơn)")


class ChatMeta(BaseModel):
    """Metadata cho mỗi request chat."""
    request_id: str = Field(..., description="Mã định danh request duy nhất")


class ChatMessageResponse(BaseModel):
    """
    Kết quả trả về từ Chatbot AI theo chuẩn FE/BE Sync Spec v1.1.
    """
    conversation_id: Optional[str] = Field(
        default=None,
        description="ID phiên hội thoại"
    )
    reply: str = Field(
        ...,
        description="Câu trả lời tự nhiên của Gemini AI bằng tiếng Việt"
    )
    tools_called: List[str] = Field(
        default_factory=list,
        description="Danh sách tên các Tool/Function mà AI đã tự động kích hoạt"
    )
    data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Structured data có trường 'type' chuẩn để FE hiển thị Card/Widget mà không cần Regex"
    )
    actions: List[ChatActionItem] = Field(
        default_factory=list,
        description="Danh sách các nút hành động điều hướng trên FE"
    )
    meta: Optional[ChatMeta] = Field(
        default=None,
        description="Metadata của phản hồi"
    )


class QuickQuestionItem(BaseModel):
    id: str
    title: str
    prompt: str


class QuickQuestionsResponse(BaseModel):
    questions: List[QuickQuestionItem]
