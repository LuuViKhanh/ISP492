from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Any, Dict


class ChatHistoryItem(BaseModel):
    """
    Một tin nhắn trong lịch sử trò chuyện (Multi-turn conversation).
    - role: "user" (người dùng) hoặc "model" (trợ lý ảo AI)
    - content: nội dung tin nhắn dạng văn bản
    """
    role: str = Field(..., description="Role: 'user' or 'model'")
    content: str = Field(..., max_length=4000, description="Message text content")

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
        description="User question or message content"
    )
    conversation_id: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Conversation session ID (when continuing a previous conversation)"
    )
    history: List[ChatHistoryItem] = Field(
        default_factory=list,
        description="Previous conversation turns (automatically capped at the 10 most recent messages)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "message": "Has my latest order been paid yet?",
                "conversation_id": "chat_abc123",
                "history": []
            }
        }


class ChatActionItem(BaseModel):
    """
    Hành động điều hướng gợi ý trên giao diện Frontend (Action Button).
    Các type chuẩn: CREATE_DELIVERY | OPEN_ORDER | OPEN_PAYMENT | OPEN_PRICING | OPEN_HELP | OPEN_NOTIFICATIONS
    """
    type: str = Field(..., description="Standard action type")
    label: str = Field(..., description="Button display label")
    href: Optional[str] = Field(default=None, description="Frontend navigation path")
    payload: Optional[Dict[str, Any]] = Field(default=None, description="Attached payload data (e.g., prefill for creating a delivery)")


class ChatMeta(BaseModel):
    """Metadata cho mỗi request chat."""
    request_id: str = Field(..., description="Unique request identifier")


class ChatMessageResponse(BaseModel):
    """
    Kết quả trả về từ Chatbot AI theo chuẩn FE/BE Sync Spec v1.1.
    """
    conversation_id: Optional[str] = Field(
        default=None,
        description="Conversation session ID"
    )
    reply: str = Field(
        ...,
        description="Natural-language response from Gemini AI in English"
    )
    tools_called: List[str] = Field(
        default_factory=list,
        description="List of tool/function names invoked by the AI"
    )
    data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Structured payload with a standard 'type' field so the FE can render cards/widgets without regex parsing"
    )
    actions: List[ChatActionItem] = Field(
        default_factory=list,
        description="List of navigation action buttons for the FE"
    )
    meta: Optional[ChatMeta] = Field(
        default=None,
        description="Response metadata"
    )


class QuickQuestionItem(BaseModel):
    id: str
    title: str
    prompt: str


class QuickQuestionsResponse(BaseModel):
    questions: List[QuickQuestionItem]
