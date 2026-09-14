from fastapi import APIRouter
from sqlalchemy import text, inspect

from app.database.db import engine
from app.database.schemas import (
    ExecuteSQL,
    CreateTable,
    InsertRow,
    UpdateRows,
    DeleteRows,
)

router = APIRouter(prefix="/dev/db", tags=["[DEV] Database Management"])

# =====================================================
# HEALTH CHECK
# =====================================================

@router.get("/health")
def db_health():
    """Kiểm tra kết nối Database có hoạt động không."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "database": "connected"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }

# =====================================================
# LIST TABLES
# =====================================================

@router.get("/tables")
def list_tables():
    """Liệt kê tất cả các bảng hiện có trong database."""
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        return {
            "status": "success",
            "tables": tables,
            "count": len(tables)
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# =====================================================
# TABLE SCHEMA (xem cấu trúc bảng)
# =====================================================

@router.get("/tables/{table_name}/schema")
def get_table_schema(table_name: str):
    """Xem cấu trúc (các cột) của một bảng."""
    try:
        inspector = inspect(engine)
        columns = inspector.get_columns(table_name)
        pk = inspector.get_pk_constraint(table_name)
        return {
            "status": "success",
            "table": table_name,
            "primary_key": pk.get("constrained_columns", []),
            "columns": [
                {
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": col["nullable"],
                    "default": str(col.get("default", ""))
                }
                for col in columns
            ]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# =====================================================
# CREATE TABLE
# =====================================================

@router.post("/tables/create")
def create_table(payload: CreateTable):
    """Tạo bảng mới bằng câu lệnh DDL thô.

    Ví dụ body:
    {
        "ddl": "CREATE TABLE IF NOT EXISTS drones (id SERIAL PRIMARY KEY, name TEXT NOT NULL, status TEXT DEFAULT 'active')"
    }
    """
    try:
        with engine.connect() as conn:
            conn.execute(text(payload.ddl))
            conn.commit()
        print(f"✅ Table created via DDL")
        return {"status": "success", "message": "Table created"}
    except Exception as e:
        print(f"❌ CREATE TABLE ERROR: {e}")
        return {"status": "error", "message": str(e)}

# =====================================================
# DROP TABLE
# =====================================================

@router.delete("/tables/{table_name}")
def drop_table(table_name: str):
    """Xóa vĩnh viễn một bảng khỏi database.
    ⚠️ Không thể hoàn tác!"""
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))
            conn.commit()
        print(f"🗑️ Table '{table_name}' dropped")
        return {"status": "success", "message": f"Table '{table_name}' dropped"}
    except Exception as e:
        print(f"❌ DROP TABLE ERROR: {e}")
        return {"status": "error", "message": str(e)}

# =====================================================
# EXECUTE RAW SQL
# =====================================================

@router.post("/execute")
def execute_sql(payload: ExecuteSQL):
    """Chạy câu lệnh SQL bất kỳ (SELECT, INSERT, UPDATE...).
    Trả về kết quả nếu có (SELECT), trả về rows_affected nếu là DML.

    Ví dụ body:
    {
        "sql": "SELECT * FROM drones WHERE status = :status",
        "params": {"status": "active"}
    }
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(payload.sql),
                payload.params or {}
            )
            conn.commit()

            # Nếu câu lệnh trả về kết quả (SELECT)
            if result.returns_rows:
                rows = result.fetchall()
                keys = list(result.keys())
                return {
                    "status": "success",
                    "rows": [dict(zip(keys, row)) for row in rows],
                    "count": len(rows)
                }

            # DML (INSERT / UPDATE / DELETE)
            return {
                "status": "success",
                "rows_affected": result.rowcount
            }

    except Exception as e:
        print(f"❌ EXECUTE SQL ERROR: {e}")
        return {"status": "error", "message": str(e)}

# =====================================================
# SELECT ALL (xem dữ liệu của bảng)
# =====================================================

@router.get("/tables/{table_name}/rows")
def get_rows(table_name: str, limit: int = 50, offset: int = 0):
    """Lấy dữ liệu trong bảng. Mặc định lấy 50 row đầu tiên."""
    try:
        with engine.connect() as conn:

            # Đếm tổng
            count_result = conn.execute(
                text(f'SELECT COUNT(*) FROM "{table_name}"')
            )
            total = count_result.scalar()

            # Lấy dữ liệu
            result = conn.execute(
                text(f'SELECT * FROM "{table_name}" LIMIT :limit OFFSET :offset'),
                {"limit": limit, "offset": offset}
            )
            rows = result.fetchall()
            keys = list(result.keys())

        return {
            "status": "success",
            "table": table_name,
            "total": total,
            "limit": limit,
            "offset": offset,
            "rows": [dict(zip(keys, row)) for row in rows]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# =====================================================
# INSERT ROWS
# =====================================================

@router.post("/tables/{table_name}/rows")
def insert_rows(table_name: str, payload: InsertRow):
    """Insert một hoặc nhiều row vào bảng.

    Ví dụ body:
    {
        "columns": ["name", "status", "battery_level"],
        "values": [
            {"name": "Drone-01", "status": "active", "battery_level": 100},
            {"name": "Drone-02", "status": "idle",   "battery_level": 85}
        ]
    }
    """
    try:
        cols_str   = ", ".join([f'"{c}"' for c in payload.columns])
        params_str = ", ".join([f":{c}" for c in payload.columns])
        sql = f'INSERT INTO "{table_name}" ({cols_str}) VALUES ({params_str})'

        with engine.connect() as conn:
            conn.execute(text(sql), payload.values)
            conn.commit()

        print(f"✅ Inserted {len(payload.values)} row(s) into '{table_name}'")
        return {
            "status": "success",
            "message": f"Inserted {len(payload.values)} row(s)"
        }
    except Exception as e:
        print(f"❌ INSERT ERROR: {e}")
        return {"status": "error", "message": str(e)}

# =====================================================
# UPDATE ROWS
# =====================================================

@router.put("/tables/{table_name}/rows")
def update_rows(table_name: str, payload: UpdateRows):
    """Cập nhật dữ liệu theo điều kiện WHERE.

    Ví dụ body:
    {
        "set_values":   {"status": "maintenance"},
        "where_sql":    "name = :name",
        "where_params": {"name": "Drone-01"}
    }
    """
    try:
        set_clause = ", ".join([f'"{k}" = :set_{k}' for k in payload.set_values])
        sql = f'UPDATE "{table_name}" SET {set_clause} WHERE {payload.where_sql}'

        # Đổi tên param set_ để tránh đụng với where_params
        params = {f"set_{k}": v for k, v in payload.set_values.items()}
        if payload.where_params:
            params.update(payload.where_params)

        with engine.connect() as conn:
            result = conn.execute(text(sql), params)
            conn.commit()

        print(f"✅ Updated {result.rowcount} row(s) in '{table_name}'")
        return {
            "status": "success",
            "rows_affected": result.rowcount
        }
    except Exception as e:
        print(f"❌ UPDATE ERROR: {e}")
        return {"status": "error", "message": str(e)}

# =====================================================
# DELETE ROWS
# =====================================================

@router.delete("/tables/{table_name}/rows")
def delete_rows(table_name: str, payload: DeleteRows):
    """Xóa các row theo điều kiện WHERE.
    ⚠️ Để xóa toàn bộ bảng, dùng endpoint DROP TABLE.

    Ví dụ body:
    {
        "where_sql":    "status = :status",
        "where_params": {"status": "decommissioned"}
    }
    """
    try:
        sql = f'DELETE FROM "{table_name}" WHERE {payload.where_sql}'

        with engine.connect() as conn:
            result = conn.execute(text(sql), payload.where_params or {})
            conn.commit()

        print(f"🗑️ Deleted {result.rowcount} row(s) from '{table_name}'")
        return {
            "status": "success",
            "rows_affected": result.rowcount
        }
    except Exception as e:
        print(f"❌ DELETE ERROR: {e}")
        return {"status": "error", "message": str(e)}
