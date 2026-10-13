"""
Customer Reference, Help & Status Services (Single Source of Truth cho FE & Chatbot).
------------------------------------------------------------------------------------
GHI CHÚ CHO LẬP TRÌNH VIÊN (DEVELOPER NOTES):
- Đọc dữ liệu chuẩn (Canonical Business Rules) từ `knowledge/customer_help.json`.
- Dùng chung cho cả REST API (`/api/v1/customer/reference/...`, `/api/v1/customer/help/...`)
  lẫn các Tools của Customer Chatbot.
- Đảm bảo nguyên tắc: Sửa business rule tại 1 nơi duy nhất -> Pricing Page, Create Delivery,
  Help & Policies và Chatbot tự động đồng bộ 100%.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

from app.modules.missions.models import (
    Order,
    Mission,
    OrderStatus,
    MissionStatus,
    PaymentStatus,
    DeliveryMode,
)

KNOWLEDGE_FILE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "knowledge",
    "customer_help.json",
)


def _load_knowledge() -> Dict[str, Any]:
    with open(KNOWLEDGE_FILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class CustomerReferenceService:
    """Cung cấp các quy tắc về kiện hàng (Package), bảng giá (Pricing) và chế độ giao (Delivery Modes)."""

    @staticmethod
    def get_package_rules() -> Dict[str, Any]:
        data = _load_knowledge()
        return {
            "type": "package_recommendation",
            "max_network_payload_kg": 10.0,
            "categories": data.get("package_categories", []),
            "sizes": data.get("package_sizes", []),
        }

    @staticmethod
    def recommend_package_size(
        weight_kg: float,
        length_cm: Optional[float] = None,
        width_cm: Optional[float] = None,
        height_cm: Optional[float] = None,
    ) -> Dict[str, Any]:
        data = _load_knowledge()
        sizes = data.get("package_sizes", [])
        tiers = {t["size"]: t for t in data.get("pricing", {}).get("tiers", [])}

        if weight_kg <= 0:
            return {
                "type": "package_recommendation",
                "success": False,
                "code": "INVALID_PACKAGE_WEIGHT",
                "message": "Khối lượng kiện hàng phải lớn hơn 0 kg.",
            }

        if weight_kg > 10.0:
            return {
                "type": "package_recommendation",
                "success": False,
                "code": "PACKAGE_EXCEEDS_MAX_LIMIT",
                "weight_kg": weight_kg,
                "max_allowed_kg": 10.0,
                "message": (
                    f"Kiện hàng {weight_kg} kg vượt quá giới hạn tối đa của mạng lưới DroneOptAI "
                    "(Size XL tối đa ≤ 10 kg, kích thước 50 × 40 × 30 cm)."
                ),
            }

        matched_size = None
        for s in sizes:
            if weight_kg <= s["max_payload_kg"]:
                dims = s["max_dimensions_cm"]
                if length_cm is not None and length_cm > dims["length"]:
                    continue
                if width_cm is not None and width_cm > dims["width"]:
                    continue
                if height_cm is not None and height_cm > dims["height"]:
                    continue
                matched_size = s
                break

        if not matched_size:
            return {
                "type": "package_recommendation",
                "success": False,
                "code": "PACKAGE_DIMENSIONS_EXCEEDED",
                "weight_kg": weight_kg,
                "dimensions_cm": {"length": length_cm, "width": width_cm, "height": height_cm},
                "message": (
                    "Kích thước kiện hàng vượt quá giới hạn tối đa của Size XL (50 × 40 × 30 cm)."
                ),
            }

        tier_pricing = tiers.get(matched_size["size"], {})
        return {
            "type": "package_recommendation",
            "success": True,
            "weight_kg": weight_kg,
            "recommended_size": matched_size["size"],
            "max_payload_kg": matched_size["max_payload_kg"],
            "max_dimensions_cm": matched_size["max_dimensions_cm"],
            "dimensions_display": matched_size["dimensions_display"],
            "description": matched_size["description"],
            "description_vi": matched_size["description_vi"],
            "pricing_tier": tier_pricing,
        }

    @staticmethod
    def get_pricing_rules() -> Dict[str, Any]:
        data = _load_knowledge()
        pricing = data.get("pricing", {})
        return {
            "type": "pricing_info",
            "currency": pricing.get("currency", "VND"),
            "formula": pricing.get("formula", {}),
            "tiers": pricing.get("tiers", []),
            "surcharges": pricing.get("surcharges", {}),
        }

    @staticmethod
    def get_delivery_modes() -> Dict[str, Any]:
        data = _load_knowledge()
        return {
            "type": "delivery_mode_info",
            "modes": data.get("delivery_modes", []),
        }


class CustomerHelpService:
    """Quản lý nội dung Help & Policies và kiểm soát chặt chẽ các Policy chưa công bố (No-Hallucination)."""

    UNDEFINED_TOPIC_ALIASES = {
        "prohibited": "prohibited_items",
        "prohibited_items": "prohibited_items",
        "hang_cam": "prohibited_items",
        "banned": "prohibited_items",
        "restricted": "prohibited_items",
        "insurance_coverage": "insurance_coverage",
        "claim": "insurance_coverage",
        "boi_thuong": "insurance_coverage",
        "den_bu": "insurance_coverage",
        "refund": "cancellation_refund_detail",
        "hoan_tien": "cancellation_refund_detail",
        "cancellation_refund_detail": "cancellation_refund_detail",
        "operating_hours": "hub_operating_hours",
        "hub_operating_hours": "hub_operating_hours",
        "gio_mo_cua": "hub_operating_hours",
        "working_hours": "hub_operating_hours",
        "recipient_verification": "recipient_verification",
        "otp": "recipient_verification",
        "xac_minh": "recipient_verification",
        "express_sla": "express_sla",
        "sla": "express_sla",
        "support_contact": "support_contact",
        "hotline": "support_contact",
        "contact": "support_contact",
        "cskh": "support_contact",
        "phone": "support_contact",
        "email_support": "support_contact",
    }

    TOPIC_ALIASES = {
        "create_order": "order_creation",
        "how_to_order": "order_creation",
        "tao_don": "order_creation",
        "hub": "hub_to_hub",
        "package": "package_rules",
        "size": "package_rules",
        "price": "pricing",
        "fee": "pricing",
        "mode": "delivery_modes",
        "express": "delivery_modes",
        "scheduled": "delivery_modes",
        "handover": "handover_deadline",
        "deadline": "handover_deadline",
        "dropoff": "handover_deadline",
        "pay": "payment",
        "payos": "payment",
        "cancel": "cancellation",
        "notification": "notifications",
        "pickup": "collection",
        "collect": "collection",
    }

    @classmethod
    def get_all_help_topics(cls) -> Dict[str, Any]:
        data = _load_knowledge()
        topics_dict = data.get("help_topics", {})
        return {
            "type": "policy",
            "delivery_flow": data.get("delivery_flow", {}),
            "topics": list(topics_dict.values()),
            "undefined_policies": list(data.get("undefined_policies", {}).keys()),
        }

    @classmethod
    def get_help_policy(cls, topic: str) -> Dict[str, Any]:
        data = _load_knowledge()
        clean_key = (topic or "").strip().lower().replace(" ", "_").replace("-", "_")

        # 1. Kiểm tra nếu rơi vào nhóm policy chưa được định nghĩa chính thức
        undefined_policies = data.get("undefined_policies", {})
        mapped_undef = cls.UNDEFINED_TOPIC_ALIASES.get(clean_key)
        if clean_key in undefined_policies or mapped_undef in undefined_policies:
            target_undef = mapped_undef or clean_key
            # Trường hợp đặc biệt: nếu hỏi về bảo hiểm nói chung vs chi tiết bồi thường
            return {
                "type": "policy",
                "success": False,
                "code": "POLICY_NOT_DEFINED",
                "topic": target_undef,
                "policy_value": None,
                "message": (
                    f"Chính sách '{target_undef}' hiện chưa có dữ liệu chính thức được công bố trong hệ thống DroneOptAI. "
                    "Trợ lý AI không được tự suy diễn hoặc bịa thông tin này."
                ),
                "known_related_rule": (
                    "Bảo hiểm kiện hàng là tùy chọn +10% Core Fee, nhưng chi tiết phạm vi bồi thường chưa được công bố."
                    if target_undef == "insurance_coverage"
                    else None
                ),
            }

        # 2. Tìm trong danh sách 12 chủ đề canonical
        help_topics = data.get("help_topics", {})
        resolved_key = cls.TOPIC_ALIASES.get(clean_key, clean_key)

        if resolved_key in help_topics:
            topic_obj = help_topics[resolved_key]
            return {
                "type": "policy",
                "success": True,
                "topic": resolved_key,
                "title": topic_obj["title"],
                "summary": topic_obj["summary"],
                "content": topic_obj["content"],
                "delivery_flow": data.get("delivery_flow") if resolved_key == "order_creation" else None,
            }

        # 3. Tìm kiếm mờ theo từ khóa trong key/title
        for k, v in help_topics.items():
            if clean_key in k or clean_key in v["title"].lower():
                return {
                    "type": "policy",
                    "success": True,
                    "topic": k,
                    "title": v["title"],
                    "summary": v["summary"],
                    "content": v["content"],
                }

        return {
            "type": "policy",
            "success": False,
            "code": "POLICY_NOT_DEFINED",
            "topic": topic,
            "available_topics": list(help_topics.keys()),
            "message": (
                f"Chủ đề '{topic}' chưa có dữ liệu chính thức để xác nhận trên hệ thống."
            ),
        }


class CustomerStatusService:
    """
    Tổng hợp trạng thái Order + Payment + Handoff + Mission thành một trạng thái
    Customer-friendly duy nhất theo đúng bảng quy chuẩn FE/BE Sync Spec (Table 19).
    """

    @staticmethod
    def compute_customer_delivery_status(
        order: Order,
        mission: Optional[Mission] = None,
    ) -> Dict[str, Any]:
        pay_status = order.payment_status or PaymentStatus.UNPAID
        ord_status = order.status or OrderStatus.PENDING
        mis_status = mission.status if mission else None

        # 1. Order Cancelled
        if ord_status == OrderStatus.CANCELLED or mis_status == MissionStatus.CANCELLED:
            return {
                "customer_status": "cancelled",
                "status_label": "Cancelled",
                "status_label_vi": "Đã hủy",
                "handoff_state": "cancelled",
            }

        # 2. Mission Failed
        if mis_status in (MissionStatus.FAILED, MissionStatus.REJECTED, MissionStatus.INCIDENT_RETURN):
            return {
                "customer_status": "delivery_issue",
                "status_label": "Delivery issue / Needs support",
                "status_label_vi": "Sự cố giao hàng / Cần hỗ trợ",
                "handoff_state": "issue",
            }

        # 3. Completed (Arrival confirmed at destination)
        if mis_status in (MissionStatus.COMPLETED, MissionStatus.MISSION_COMPLETED) and (
            mission and mission.arrival_confirmed_at is not None
        ):
            return {
                "customer_status": "completed",
                "status_label": "Completed",
                "status_label_vi": "Đã hoàn tất giao nhận",
                "handoff_state": "collected",
            }

        # 4. Handoff received at destination -> Ready for collection
        if (
            order.destination_received_at is not None
            or ord_status == OrderStatus.DELIVERED_TO_HUB
            or (mission and mission.arrived_at is not None and mission.arrival_confirmed_at is None)
            or mis_status in (MissionStatus.COMPLETED, MissionStatus.MISSION_COMPLETED)
        ):
            return {
                "customer_status": "ready_for_collection",
                "status_label": "Ready for collection",
                "status_label_vi": "Đã tới trạm đích - Sẵn sàng nhận hàng",
                "handoff_state": "received_at_destination",
            }

        # 5. Mission in progress OR handoff in transit -> In delivery
        if ord_status == OrderStatus.IN_DELIVERY or mis_status in (
            MissionStatus.IN_PROGRESS,
            MissionStatus.FLYING,
            MissionStatus.ACTIVE_MISSION,
        ):
            return {
                "customer_status": "in_delivery",
                "status_label": "In delivery",
                "status_label_vi": "Đang vận chuyển bằng Drone",
                "handoff_state": "in_transit",
            }

        # 6. Mission scheduled
        if mis_status in (
            MissionStatus.SCHEDULED,
            MissionStatus.APPROVED,
            MissionStatus.PENDING_APPROVAL,
        ):
            return {
                "customer_status": "scheduled",
                "status_label": "Scheduled",
                "status_label_vi": "Đã lên lịch bay",
                "handoff_state": (
                    "received_at_origin" if order.origin_received_at else "awaiting_origin_receive"
                ),
            }

        # 7. Handoff received at origin + mission not started
        if order.origin_received_at is not None:
            return {
                "customer_status": "package_received_at_origin",
                "status_label": "Package received at origin hub / Preparing delivery",
                "status_label_vi": "Đã nhận hàng tại trạm gửi / Đang chuẩn bị giao",
                "handoff_state": "received_at_origin",
            }

        # 8. Payment PAID + awaiting origin receive
        if pay_status == PaymentStatus.PAID:
            return {
                "customer_status": "awaiting_package_dropoff",
                "status_label": "Awaiting package drop-off",
                "status_label_vi": "Đã thanh toán - Chờ gửi hàng tại Origin Hub",
                "handoff_state": "awaiting_origin_receive",
            }

        # 9. Payment PENDING / UNPAID
        return {
            "customer_status": "awaiting_payment",
            "status_label": "Awaiting payment",
            "status_label_vi": "Đang chờ thanh toán",
            "handoff_state": "awaiting_payment",
        }

    @staticmethod
    def compute_handover_and_eta(
        order: Order,
        mission: Optional[Mission] = None,
    ) -> Dict[str, Optional[str]]:
        """
        Trích xuất thời gian bàn giao (handover_deadline) và ETA từ dữ liệu thực của Order/Mission.
        Không tự bịa nếu dữ liệu không tồn tại.
        """
        handover_deadline: Optional[datetime] = None
        estimated_departure: Optional[datetime] = None
        estimated_arrival: Optional[datetime] = None

        if order.delivery_mode == DeliveryMode.EXPRESS:
            if order.created_at:
                handover_deadline = order.created_at + timedelta(hours=3)
        elif order.delivery_mode == DeliveryMode.SCHEDULED:
            if order.requested_delivery_at:
                handover_deadline = order.requested_delivery_at - timedelta(hours=3)
                estimated_departure = order.requested_delivery_at

        if mission:
            if mission.departed_at:
                estimated_departure = mission.departed_at
            elif mission.scheduled_time:
                estimated_departure = mission.scheduled_time
            elif mission.start_time:
                estimated_departure = mission.start_time

            if mission.arrived_at:
                estimated_arrival = mission.arrived_at
            elif mission.end_time:
                estimated_arrival = mission.end_time

        if not estimated_arrival and order.estimated_window_end:
            estimated_arrival = order.estimated_window_end
        if not estimated_departure and order.estimated_window_start:
            estimated_departure = order.estimated_window_start

        def _fmt(dt: Optional[datetime]) -> Optional[str]:
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc).isoformat()
            return dt.isoformat()

        return {
            "handover_deadline": _fmt(handover_deadline),
            "estimated_departure": _fmt(estimated_departure),
            "estimated_arrival": _fmt(estimated_arrival),
            "origin_received_at": _fmt(order.origin_received_at),
            "destination_received_at": _fmt(order.destination_received_at),
        }
