from enum import Enum

class UserRole(str, Enum):
    ADMIN = "Admin"
    OPERATOR = "Operator"
    TECHNICIAN = "Technician"
    CUSTOMER = "Customer"
