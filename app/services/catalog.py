from sqlalchemy import func, select

from app.errors import ApiError, validation
from app.models import Order, OrderItem, Product, User, fields
from app.security import require_role


def find(session, product_id, lock=False):
    query = select(Product).where(Product.id == product_id)
    product = session.scalar(query.with_for_update() if lock else query)
    if product is None:
        raise ApiError(404, "PRODUCT_NOT_FOUND", "product not found")
    return product


def check_owner(user, product):
    require_role(user, "SELLER", "ADMIN")
    if user["role"] != "ADMIN" and product.seller_id != user["id"]:
        raise ApiError(403, "ACCESS_DENIED", "product belongs to another seller")


def seller_id(session, user, supplied, fallback):
    chosen = supplied or fallback
    if user["role"] != "ADMIN" and chosen != user["id"]:
        raise ApiError(403, "ACCESS_DENIED", "cannot assign another seller")
    owner = session.get(User, chosen)
    if owner is None or owner.role not in {"SELLER", "ADMIN"}:
        raise validation("seller_id", "must refer to an existing seller or admin")
    return chosen


def create(session, user, data):
    require_role(user, "SELLER", "ADMIN")
    values = data.model_dump()
    values["seller_id"] = seller_id(session, user, data.seller_id, user["id"])
    product = Product(**values)
    session.add(product)
    session.flush()
    return fields(product)


def update(session, user, product_id, data):
    require_role(user, "SELLER", "ADMIN")
    product = find(session, product_id, lock=True)
    check_owner(user, product)
    values = data.model_dump()
    held = session.scalar(
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order, Order.id == OrderItem.order_id)
        .where(OrderItem.product_id == product_id, Order.status.in_(["CREATED", "PAYMENT_PENDING"]))
    )
    if data.stock + held > 2147483647:
        raise validation("stock", "available plus reserved stock exceeds the integer limit")
    values["seller_id"] = seller_id(session, user, data.seller_id, product.seller_id)
    for name, value in values.items():
        setattr(product, name, value)
    session.flush()
    session.refresh(product)
    return fields(product)


def archive(session, user, product_id):
    require_role(user, "SELLER", "ADMIN")
    product = find(session, product_id, lock=True)
    check_owner(user, product)
    product.status = "ARCHIVED"


def listing(session, page, size, status, category):
    predicates = []
    if status is not None:
        predicates.append(Product.status == status)
    if category is not None:
        predicates.append(Product.category == category)
    count = session.scalar(select(func.count()).select_from(Product).where(*predicates))
    products = session.scalars(
        select(Product).where(*predicates).order_by(Product.id).offset(page * size).limit(size)
    )
    return {"items": [fields(p) for p in products], "totalElements": count, "page": page, "size": size}
