import uuid

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, Uuid, text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    email = Column(String(254), nullable=False)
    password_hash = Column(Text, nullable=False)
    role = Column(Enum("USER", "SELLER", "ADMIN", name="user_role"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("clock_timestamp()"))


class Product(Base):
    __tablename__ = "products"
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(String(4000))
    price = Column(Numeric(12, 2), nullable=False)
    stock = Column(Integer, nullable=False)
    category = Column(String(100), nullable=False)
    status = Column(Enum("ACTIVE", "INACTIVE", "ARCHIVED", name="product_status"), nullable=False)
    seller_id = Column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
    updated_at = Column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        server_onupdate=text("clock_timestamp()"),
    )


class Promo(Base):
    __tablename__ = "promo_codes"
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    code = Column(String(20), nullable=False)
    discount_type = Column(Enum("PERCENTAGE", "FIXED_AMOUNT", name="discount_type"), nullable=False)
    discount_value = Column(Numeric(12, 2), nullable=False)
    min_order_amount = Column(Numeric(12, 2), nullable=False)
    max_uses = Column(Integer, nullable=False)
    current_uses = Column(Integer, nullable=False, default=0)
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_until = Column(DateTime(timezone=True), nullable=False)
    active = Column(Boolean, nullable=False)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False)
    status = Column(
        Enum("CREATED", "PAYMENT_PENDING", "PAID", "SHIPPED", "COMPLETED", "CANCELED", name="order_status"),
        nullable=False,
    )
    promo_code_id = Column(Uuid, ForeignKey("promo_codes.id"))
    total_amount = Column(Numeric(12, 2), nullable=False)
    discount_amount = Column(Numeric(12, 2), nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
    updated_at = Column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        server_onupdate=text("clock_timestamp()"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id = Column(Uuid, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Uuid, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    price_at_order = Column(Numeric(12, 2), nullable=False)


class UserOperation(Base):
    __tablename__ = "user_operations"
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False)
    operation_type = Column(Enum("CREATE_ORDER", "UPDATE_ORDER", name="operation_type"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("clock_timestamp()"))


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"
    id = Column(Uuid, primary_key=True)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False)
    token_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=text("clock_timestamp()"))


def fields(entity):
    return {column.name: getattr(entity, column.name) for column in entity.__table__.columns}
