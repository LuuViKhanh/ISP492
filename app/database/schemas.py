from pydantic import BaseModel
from typing import Any, Dict, Optional

# =====================================================
# SCHEMAS — Request body models cho Developer DB API
# =====================================================


class ExecuteSQL(BaseModel):
    """Chạy câu lệnh SQL bất kỳ (SELECT, INSERT, UPDATE...).
    Dùng :param_name trong sql và truyền params tương ứng."""
    sql: str
    params: Optional[Dict[str, Any]] = None


class CreateTable(BaseModel):
    """Tạo bảng mới bằng câu lệnh DDL thô (CREATE TABLE ...)"""
    ddl: str


class InsertRow(BaseModel):
    """Insert một hoặc nhiều row vào bảng.
    columns: danh sách tên cột
    values:  danh sách dict giá trị tương ứng"""
    columns: list[str]
    values:  list[Dict[str, Any]]


class UpdateRows(BaseModel):
    """Cập nhật dữ liệu theo điều kiện WHERE.
    set_values:  dict {cột: giá trị mới}
    where_sql:   câu điều kiện, ví dụ 'id = :id'
    where_params: dict {param: giá trị}"""
    set_values:   Dict[str, Any]
    where_sql:    str
    where_params: Optional[Dict[str, Any]] = None


class DeleteRows(BaseModel):
    """Xóa các row theo điều kiện WHERE.
    where_sql:    câu điều kiện, ví dụ 'id = :id'
    where_params: dict {param: giá trị}"""
    where_sql:    str
    where_params: Optional[Dict[str, Any]] = None
