"""
app/shared/image_storage.py
───────────────────────────
Helper chuyển đổi & lưu ảnh Base64 thành file tĩnh nén WebP trên đĩa server,
chỉ lưu URL ngắn (~70 bytes) vào PostgreSQL thay vì nhúng Base64 Data URL (~270 KB+)
vào JSONB/TEXT columns.

Lợi ích:
- Giảm kích thước ghi/đọc Supabase xuống ~4,000x (tăng tốc cả upload lẫn load list).
- Trình duyệt cache ảnh vĩnh viễn qua `Cache-Control: public, max-age=31536000, immutable`.
- Tương thích ngược 100%: FE vẫn gửi Base64 hoặc URL đều hoạt động không cần sửa FE.
"""

from __future__ import annotations

import base64
import io
import mimetypes
import re
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings

UPLOAD_ROOT_DIR = Path(__file__).resolve().parents[2] / "uploads"
VALID_IMAGE_CATEGORIES = frozenset({"proofs", "fleet", "work_orders", "supplies"})

_MAX_IMAGE_DIMENSION = 1280
_WEBP_QUALITY = 75
_RECOMPRESS_THRESHOLD_BYTES = 250 * 1024  # FE nén sẵn ~200KB -> lưu thẳng chỉ mất ~2ms
_DATA_URI_RE = re.compile(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.+)$", re.DOTALL)
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

_request_base_url_var: ContextVar[str] = ContextVar("image_request_base_url", default="")

router = APIRouter(prefix="/uploads", tags=["Uploads"])


def set_request_base_url(base_url: str) -> Any:
    """Lưu base URL của request hiện tại vào ContextVar để build URL ảnh chuẩn host/tunnel."""
    return _request_base_url_var.set((base_url or "").strip().rstrip("/"))


def reset_request_base_url(token: Any) -> None:
    """Khôi phục ContextVar sau khi request kết thúc."""
    _request_base_url_var.reset(token)


def get_public_base_url() -> str:
    """Lấy base URL public (ưu tiên request hiện tại, fallback settings.BACKEND_URL)."""
    req_base = (_request_base_url_var.get() or "").strip().rstrip("/")
    if req_base:
        return req_base
    return (settings.BACKEND_URL or "").strip().rstrip("/")


def _compress_to_webp(raw_bytes: bytes, fallback_ext: str) -> tuple[bytes, str]:
    """
    Chỉ nén/resize bằng Pillow khi ảnh lớn (> 250KB) với `method=0` (siêu nhanh).
    Nếu ảnh <= 250KB (FE đã nén sẵn) thì lưu thẳng để giảm tối đa CPU & độ trễ (~2ms).
    """
    if len(raw_bytes) <= _RECOMPRESS_THRESHOLD_BYTES:
        return raw_bytes, fallback_ext
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(raw_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            if max(img.size) > _MAX_IMAGE_DIMENSION:
                img.thumbnail((_MAX_IMAGE_DIMENSION, _MAX_IMAGE_DIMENSION), Image.Resampling.BILINEAR)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            out_buf = io.BytesIO()
            img.save(out_buf, format="WEBP", quality=_WEBP_QUALITY, method=0)
            webp_bytes = out_buf.getvalue()
            if len(webp_bytes) < len(raw_bytes):
                return webp_bytes, "webp"
    except Exception:
        pass
    return raw_bytes, fallback_ext


def _build_public_upload_url(category: str, filename: str) -> str:
    prefix = f"{settings.API_V1_STR.rstrip('/')}/uploads/{category}/{filename}"
    base = get_public_base_url()
    return f"{base}{prefix}" if base else prefix


def normalize_and_store_image(
    value: Optional[str],
    category: str = "proofs",
) -> Optional[str]:
    """
    Nếu `value` là `data:image/...;base64,...`, giải mã -> nén WebP -> lưu vào `uploads/<category>/`
    và trả về URL ngắn công khai. Nếu đã là URL thường hoặc None thì giữ nguyên.
    """
    if value is None or not isinstance(value, str):
        return value
    trimmed = value.strip()
    if not trimmed:
        return trimmed
    if trimmed.startswith(("http://", "https://", "/api/")):
        return trimmed

    match = _DATA_URI_RE.match(trimmed)
    if not match:
        return value

    subtype = match.group(1).lower().split("+")[0]
    b64_payload = match.group(2).strip()
    ext_map = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp", "gif": "gif"}
    fallback_ext = ext_map.get(subtype, "jpg")

    try:
        raw_bytes = base64.b64decode(b64_payload, validate=False)
    except Exception:
        return value

    if not raw_bytes:
        return value

    out_bytes, final_ext = _compress_to_webp(raw_bytes, fallback_ext)
    safe_category = category if category in VALID_IMAGE_CATEGORIES else "proofs"
    target_dir = UPLOAD_ROOT_DIR / safe_category
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:10]}.{final_ext}"
    (target_dir / filename).write_bytes(out_bytes)
    return _build_public_upload_url(safe_category, filename)


def normalize_image_list(
    values: Optional[list[str]],
    category: str = "supplies",
) -> Optional[list[str]]:
    """Chuẩn hóa mảng URL/Base64 ảnh sang danh sách URL ngắn."""
    if values is None:
        return None
    out: list[str] = []
    for item in values:
        normalized = normalize_and_store_image(item, category=category)
        if normalized:
            out.append(normalized)
    return out


def normalize_proof_entries(
    entries: Optional[list[Any]],
    category: str = "work_orders",
) -> Optional[list[Any]]:
    """Chuẩn hóa trường `proof_image_url` trong danh sách entry dict hoặc chuỗi."""
    if entries is None:
        return None
    normalized_entries: list[Any] = []
    for item in entries:
        if isinstance(item, dict):
            copied = dict(item)
            if copied.get("proof_image_url"):
                copied["proof_image_url"] = normalize_and_store_image(
                    copied["proof_image_url"], category=category
                )
            normalized_entries.append(copied)
        elif isinstance(item, str):
            normalized_entries.append(normalize_and_store_image(item, category=category))
        else:
            normalized_entries.append(item)
    return normalized_entries


@router.get("/{category}/{filename}", summary="Serve cached uploaded image")
async def get_uploaded_image(category: str, filename: str):
    """Phục vụ ảnh minh chứng đã lưu trên server với header cache vĩnh viễn (1 năm)."""
    if category not in VALID_IMAGE_CATEGORIES or not _SAFE_FILENAME_RE.match(filename):
        raise HTTPException(status_code=404, detail="Image not found")

    cat_dir = (UPLOAD_ROOT_DIR / category).resolve()
    file_path = (cat_dir / filename).resolve()
    if not file_path.is_relative_to(cat_dir) or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")

    media_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(
        path=file_path,
        media_type=media_type or "image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
