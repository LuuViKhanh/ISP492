"""
app/modules/payments/router.py
────────────────────────────────
Payment endpoints dùng PayOS.

Endpoints:
  POST /payments/orders/{order_id}/checkout   → Tạo payment link
  GET  /payments/orders/{order_id}/status     → Kiểm tra trạng thái thanh toán
  POST /payments/webhook                      → PayOS webhook callback
  POST /payments/orders/{order_id}/cancel     → Huỷ payment link
"""

import hmac
import hashlib
import json
import time
import random

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone
from pydantic import BaseModel
from typing import Optional
from app.core.timezone import get_vn_naive_datetime, to_vn_datetime

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.missions.models import Order, PaymentStatus, OrderStatus, OrderStatus
from app.core.config import settings
from app.modules.payments.payos_client import get_payos

router = APIRouter(prefix="/payments", tags=["Payments"])

allow_customer = RoleChecker([UserRole.CUSTOMER, UserRole.ADMIN])
allow_all      = RoleChecker([UserRole.CUSTOMER, UserRole.OPERATOR, UserRole.ADMIN])
allow_admin    = RoleChecker([UserRole.ADMIN])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CheckoutResponse(BaseModel):
    order_id: str
    payment_order_code: int
    checkout_url: str
    qr_code: Optional[str] = None
    # VietQR fields — FE dùng để tạo QR trực tiếp
    bin: Optional[str] = None
    account_number: Optional[str] = None
    account_name: Optional[str] = None
    description: Optional[str] = None
    payment_link_id: Optional[str] = None
    amount: int  # Luôn là integer VND
    payment_status: PaymentStatus


class PaymentStatusResponse(BaseModel):
    order_id: str
    payment_status: PaymentStatus
    paid_at: Optional[datetime] = None
    payment_transaction_id: Optional[str] = None
    amount: Optional[float] = None


class RefundRequest(BaseModel):
    amount: Optional[float] = None  # Nếu None thì hoàn toàn bộ
    reason: str  # Lý do hoàn tiền


class RefundResponse(BaseModel):
    message: str
    order_id: str
    refund_amount: float
    refund_reason: str
    refund_at: datetime


# ── Tạo payment link ──────────────────────────────────────────────────────────

@router.post(
    "/orders/{order_id}/checkout",
    response_model=CheckoutResponse,
    summary="Create PayOS payment link for an order",
)
async def create_checkout(
    order_id: str,
    user: CurrentUser = Depends(allow_customer),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Tạo payment link PayOS cho một đơn hàng.

    - Nếu đơn hàng đã có link (status=PENDING), trả về link cũ luôn.
    - `delivery_fee` phải được set trước khi gọi endpoint này.
    - Sau khi tạo thành công, `payment_status` chuyển sang `PENDING`.
    - FE redirect user đến `checkout_url` hoặc hiển thị `qr_code`.
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Chỉ cho phép customer của đơn hàng đó (hoặc admin)
    if user.role.value not in ("Admin",) and order.customer_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to pay for this order")

    if order.payment_status == PaymentStatus.PAID:
        raise HTTPException(status_code=400, detail="Order has already been paid")

    if not order.delivery_fee or order.delivery_fee <= 0:
        raise HTTPException(status_code=400, detail="Order does not have a delivery fee set (delivery_fee)")

    # Nếu đã có link PENDING → trả lại từ DB (đã lưu lúc tạo)
    if order.payment_status == PaymentStatus.PENDING and order.payment_checkout_url and order.payment_order_code:
        # Nếu DB chưa có qr_code (đơn cũ tạo trước fix) → thử lấy từ PayOS status
        qr = order.payment_qr_code
        bin_ = order.payment_bin
        acc_num = order.payment_account_number
        acc_name = order.payment_account_name
        return CheckoutResponse(
            order_id=order_id,
            payment_order_code=order.payment_order_code,
            checkout_url=order.payment_checkout_url,
            qr_code=qr,
            bin=bin_,
            account_number=acc_num,
            account_name=acc_name,
            amount=int(round(order.delivery_fee)),
            payment_status=order.payment_status,
        )

    # Tạo orderCode duy nhất (PayOS yêu cầu số nguyên dương)
    order_code = int(time.time() * 1000) % 9999999 + random.randint(1, 999)

    amount_vnd = int(round(order.delivery_fee))  # Làm tròn VND integer

    frontend_url = settings.FRONTEND_URL.rstrip("/")
    return_url  = f"{frontend_url}/payment/success?order_id={order_id}"
    cancel_url  = f"{frontend_url}/payment/cancel?order_id={order_id}"

    try:
        payos = get_payos()
        from payos import PaymentData, ItemData
        payment_data = PaymentData(
            orderCode=order_code,
            amount=amount_vnd,
            description=f"Pay order {order_id[:10]}"[:25],  # PayOS max 25 ký tự
            items=[
                ItemData(
                    name=f"Delivery {order.package_label or order_id[:8]}"[:50],
                    quantity=1,
                    price=amount_vnd,
                )
            ],
            returnUrl=return_url,
            cancelUrl=cancel_url,
        )
        response = payos.createPaymentLink(payment_data)
    except Exception as e:
        import traceback
        print(f"[PayOS ERROR] {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"PayOS error: {str(e)}")

    # Lưu vào DB — bao gồm VietQR fields để reuse sau
    now = get_vn_naive_datetime()
    res_data = response.to_json()
    order.payment_status        = PaymentStatus.PENDING
    order.payment_order_code    = order_code
    order.payment_checkout_url  = res_data.get("checkoutUrl") or response.checkoutUrl
    order.payment_qr_code       = res_data.get("qrCode")
    order.payment_bin           = res_data.get("bin")
    order.payment_account_number = res_data.get("accountNumber")
    order.payment_account_name  = res_data.get("accountName")
    order.updated_at            = now
    await db.commit()
    await db.refresh(order)

    return CheckoutResponse(
        order_id=order_id,
        payment_order_code=order_code,
        checkout_url=res_data.get("checkoutUrl") or response.checkoutUrl,
        qr_code=res_data.get("qrCode"),
        bin=res_data.get("bin"),
        account_number=res_data.get("accountNumber"),
        account_name=res_data.get("accountName"),
        description=res_data.get("description"),
        payment_link_id=res_data.get("paymentLinkId"),
        amount=amount_vnd,
        payment_status=PaymentStatus.PENDING,
    )


# ── Helper Notification ───────────────────────────────────────────────────────

async def _notify_customer_payment_confirmed(db: AsyncSession, order: Order, paid_time: datetime) -> None:
    """Gửi Notification xác nhận thanh toán kèm thời gian về cho Customer (chống gửi trùng)."""
    if not order.customer_id:
        return
    try:
        from app.modules.system.models import Notification, NotificationType
        from app.core.timezone import get_vn_datetime
        ref_id = f"ORDER_PAID_{order.id}"
        existing = (await db.execute(
            select(Notification.id).where(Notification.reference_id == ref_id).limit(1)
        )).scalar_one_or_none()
        if existing:
            return
        vn_now = get_vn_datetime()
        time_str = paid_time.strftime("%H:%M:%S %d/%m/%Y") if paid_time else vn_now.strftime("%H:%M:%S %d/%m/%Y")
        fee_str = f"{int(round(order.delivery_fee or 0)):,} VND"
        db.add(Notification(
            user_id=str(order.customer_id),
            title=f"💳 Payment confirmed for Order #{order.id}",
            message=(
                f"Payment of {fee_str} for Order #{order.id} was confirmed at {time_str}. "
                f"Please drop off your package at the origin Hub so our staff can receive it and dispatch a Drone."
            ),
            notifications_type=NotificationType.PAYMENT,
            reference_id=ref_id,
            created_at=vn_now,
        ))
    except Exception as e:
        print(f"[NOTIFY PAYMENT ERROR] {e}")


# ── Kiểm tra trạng thái ───────────────────────────────────────────────────────

@router.get(
    "/orders/{order_id}/status",
    response_model=PaymentStatusResponse,
    summary="Check payment status",
)
async def get_payment_status(
    order_id: str,
    user: CurrentUser = Depends(allow_all),
    db: AsyncSession = Depends(get_async_db),
):
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if user.role == UserRole.CUSTOMER and str(order.customer_id) != str(user.id):
        raise HTTPException(status_code=403, detail="ORDER_ACCESS_DENIED")

    # Nếu vẫn PENDING → chủ động hỏi PayOS để sync trạng thái thực tế
    if order.payment_status == PaymentStatus.PENDING and order.payment_order_code:
        try:
            payos = get_payos()
            payment_info = payos.getPaymentLinkInformation(order.payment_order_code)
            info_data = payment_info.to_json() if hasattr(payment_info, "to_json") else {}
            remote_status = str(info_data.get("status", "")).upper()
            if remote_status == "PAID":
                now = get_vn_naive_datetime()
                order.payment_status = PaymentStatus.PAID
                order.paid_at = now
                order.updated_at = now
                if order.status == OrderStatus.PENDING:
                    order.status = OrderStatus.AWAITING_DROP_OFF
                transaction_list = info_data.get("transactions", [])
                if transaction_list:
                    order.payment_transaction_id = str(transaction_list[0].get("reference", ""))
                await _notify_customer_payment_confirmed(db, order, now)
                await db.commit()
                await db.refresh(order)
            elif remote_status in ("CANCELLED", "EXPIRED"):
                order.payment_status = PaymentStatus.FAILED
                order.updated_at = get_vn_naive_datetime()
                await db.commit()
                await db.refresh(order)
        except Exception:
            pass  # Nếu PayOS lỗi thì trả về status hiện tại trong DB

    return PaymentStatusResponse(
        order_id=order_id,
        payment_status=order.payment_status or PaymentStatus.UNPAID,
        paid_at=to_vn_datetime(order.paid_at),   # trả về với timezone VN (+07:00)
        payment_transaction_id=order.payment_transaction_id,
        amount=order.delivery_fee,
    )


# ── Webhook từ PayOS ──────────────────────────────────────────────────────────

@router.post("/webhook", summary="PayOS webhook callback (no auth required)")
async def payos_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """
    PayOS gọi endpoint này sau khi user thanh toán xong.

    Verify HMAC-SHA256 signature trước khi xử lý.
    Cập nhật `payment_status = PAID` và `paid_at` cho Order tương ứng.
    """
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    # ── Verify signature ────────────────────────────────────────────────────
    # PayOS gửi signature trong payload["signature"]
    received_sig = payload.get("signature", "")
    data = payload.get("data", {})

    # If data is empty (PayOS verification call during webhook registration) -> return 200 immediately
    if not data:
        return JSONResponse(status_code=200, content={"message": "OK"})

    # Build canonical string: sort keys alphabetically, normalize null/undefined to empty string
    sorted_keys = sorted(data.keys())
    parts = []
    for k in sorted_keys:
        v = data[k]
        if v is None or str(v) in ("undefined", "null"):
            v = ""
        parts.append(f"{k}={v}")
    canonical = "&".join(parts)

    expected_sig = hmac.new(
        settings.PAYOS_CHECKSUM_KEY.encode(),
        canonical.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(received_sig.lower(), expected_sig.lower()):
        return JSONResponse(status_code=400, content={"error": "Invalid signature"})

    # ── Xử lý kết quả thanh toán ────────────────────────────────────────────
    order_code     = data.get("orderCode")
    # PayOS gửi code trong data, không phải root level
    status_code    = str(data.get("code", "") or payload.get("code", ""))
    transaction_id = str(data.get("reference", "") or data.get("transactionDateTime", ""))

    if not order_code:
        return JSONResponse(status_code=200, content={"message": "Ignored"})

    # Tìm Order theo payment_order_code
    result = await db.execute(
        select(Order).where(Order.payment_order_code == int(order_code))
    )
    order = result.scalar_one_or_none()
    if not order:
        return JSONResponse(status_code=200, content={"message": "Order not found, ignored"})

    now = get_vn_naive_datetime()

    if status_code == "00":
        order.payment_status         = PaymentStatus.PAID
        order.payment_transaction_id = transaction_id
        order.paid_at                = now
        order.updated_at             = now
        # Chuyển order sang AWAITING_DROP_OFF — KH cần mang hàng đến hub
        if order.status == OrderStatus.PENDING:
            order.status = OrderStatus.AWAITING_DROP_OFF
        await _notify_customer_payment_confirmed(db, order, now)
    else:
        order.payment_status = PaymentStatus.FAILED
        order.updated_at     = now

    await db.commit()
    return JSONResponse(status_code=200, content={"message": "OK"})


# ── Đăng ký Webhook URL với PayOS ────────────────────────────────────────────

@router.post(
    "/register-webhook",
    summary="Register Webhook URL with PayOS (one-time setup)",
    include_in_schema=True,
)
async def register_webhook(
    user: CurrentUser = Depends(allow_admin),
):
    """
    Gọi PayOS API để đăng ký webhook URL.
    Chỉ cần chạy 1 lần sau khi deploy.
    Webhook URL: {BACKEND_URL}/api/v1/payments/webhook
    """
    import httpx
    webhook_url = f"{settings.BACKEND_URL.rstrip(chr(47))}/api/v1/payments/webhook"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api-merchant.payos.vn/confirm-webhook",
                json={"webhookUrl": webhook_url},
                headers={
                    "x-client-id": settings.PAYOS_CLIENT_ID,
                    "x-api-key": settings.PAYOS_API_KEY,
                },
                timeout=10,
            )
            return {"status": resp.status_code, "response": resp.json()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Hoàn tiền (Refund) ────────────────────────────────────────────────────────

@router.post(
    "/orders/{order_id}/refund",
    response_model=RefundResponse,
    summary="Admin refund for customer order",
)
async def refund_order(
    order_id: str,
    body: RefundRequest,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Admin hoàn tiền cho khách.
    
    - Chỉ có thể hoàn tiền cho đơn đã PAID
    - Nếu không truyền amount → hoàn toàn bộ delivery_fee
    - Gọi PayOS refund API → tiền về tài khoản ngân hàng khách (1-3 ngày)
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    if order.payment_status != PaymentStatus.PAID:
        raise HTTPException(
            status_code=400, 
            detail=f"Only paid orders can be refunded. Current status: {order.payment_status.value}"
        )
    
    if not order.payment_order_code:
        raise HTTPException(status_code=400, detail="Order does not have payment information (order_code)")
    
    refund_amount = body.amount or order.delivery_fee
    if refund_amount <= 0:
        raise HTTPException(status_code=400, detail="Refund amount must be greater than 0")
    
    if refund_amount > (order.delivery_fee or 0):
        raise HTTPException(
            status_code=400, 
            detail=f"Refund amount cannot exceed delivery_fee ({order.delivery_fee} VND)"
        )
    
    try:
        payos = get_payos()
        # Gọi PayOS refund API
        refund_response = payos.refund(
            orderId=order.payment_order_code,
            amount=int(refund_amount),
            description=body.reason[:255],
        )
        
        # Cập nhật DB
        now = get_vn_naive_datetime()
        order.payment_status = PaymentStatus.REFUNDED
        order.refund_at = now
        order.refund_amount = refund_amount
        order.refund_reason = body.reason
        order.refund_status = "PENDING"  # Chờ PayOS xác nhận
        
        await db.commit()
        
        # Gửi email hoàn tiền cho khách hàng gửi hàng qua tài khoản customer (bảng orders không dùng cột sender_email)
        sender_email_addr = None
        if order.customer_id:
            from app.modules.auth.models import Account
            cust_acc = await db.get(Account, str(order.customer_id))
            sender_email_addr = cust_acc.email if (cust_acc and cust_acc.email) else None

        if sender_email_addr:
            from app.core.email import send_refund_email
            await send_refund_email(
                to_email=sender_email_addr,
                order_id=order_id,
                refund_amount=refund_amount,
                refund_reason=body.reason,
            )
        else:
            print(f"[REFUND] Order {order_id} không tìm thấy email người gửi từ Account, bỏ qua gửi email")
        
        return RefundResponse(
            message="Refund processed successfully",
            order_id=order_id,
            refund_amount=refund_amount,
            refund_reason=body.reason,
            refund_at=now,
        )
        
    except Exception as e:
        import traceback
        print(f"[REFUND ERROR] {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"PayOS refund error: {str(e)}")


# ── Huỷ payment link ──────────────────────────────────────────────────────────

@router.post(
    "/orders/{order_id}/cancel",
    summary="Cancel PayOS payment link",
)
async def cancel_payment(
    order_id: str,
    user: CurrentUser = Depends(allow_customer),
    db: AsyncSession = Depends(get_async_db),
):
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.payment_status == PaymentStatus.PAID:
        raise HTTPException(status_code=400, detail="Paid orders cannot be cancelled")

    if not order.payment_order_code:
        raise HTTPException(status_code=400, detail="No payment link found to cancel")

    try:
        payos = get_payos()
        payos.cancelPaymentLink(order.payment_order_code)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PayOS error: {str(e)}")

    now = get_vn_naive_datetime()
    order.payment_status = PaymentStatus.CANCELLED
    order.updated_at     = now
    await db.commit()

    return {"message": "Payment link cancelled", "order_id": order_id}


# ── Danh sách giao dịch (Admin xem toàn bộ transactions) ────────────────────────

@router.get(
    "/transactions",
    summary="Admin xem danh sách toàn bộ giao dịch",
)
async def get_transactions(
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Danh sách toàn bộ giao dịch trong hệ thống (Admin only).
    """
    from app.modules.payments.schemas import TransactionResponse

    result = await db.execute(select(Order))
    orders = result.scalars().all()

    transactions = [
        TransactionResponse(
            payment_id=order.id,
            order_id=order.id,
            customer_id=order.customer_id,
            amount=order.delivery_fee or 0,
            payment_status=order.payment_status.value if order.payment_status else "UNPAID",
            paid_at=order.paid_at,
            refund_at=order.refund_at,
            refund_amount=order.refund_amount,
            refund_reason=order.refund_reason,
            created_at=order.created_at,
        )
        for order in orders
    ]

    return transactions


# ── Danh sách hoàn tiền (Admin xem toàn bộ refunds) ────────────────────────────

@router.get(
    "/refunds",
    summary="Admin xem danh sách toàn bộ hoàn tiền",
)
async def get_refunds(
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Danh sách toàn bộ giao dịch hoàn tiền trong hệ thống (Admin only).
    Chỉ lấy Orders có refund_amount > 0.
    """
    from app.modules.payments.schemas import RefundListResponse

    result = await db.execute(
        select(Order).where(Order.refund_amount > 0)
    )
    orders = result.scalars().all()

    refunds = [
        RefundListResponse(
            refund_id=order.id,
            order_id=order.id,
            refund_amount=order.refund_amount or 0,
            reason=order.refund_reason or "N/A",
            status=order.refund_status.value if hasattr(order.refund_status, "value") else (order.refund_status or "PENDING"),
            processed_at=order.refund_at,
        )
        for order in orders
    ]

    return refunds
