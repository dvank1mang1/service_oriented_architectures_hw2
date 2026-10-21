from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from app.config import settings
from app.errors import ApiError, validation
from app.models import Order, OrderItem, Product, User, UserOperation, fields
from app.security import now, require_role
from app.services import promos

NEXT_STATE = {
    "CREATED": "PAYMENT_PENDING",
    "PAYMENT_PENDING": "PAID",
    "PAID": "SHIPPED",
    "SHIPPED": "COMPLETED",
}


def lock_users(session, *user_ids):
    session.scalars(select(User).where(User.id.in_(set(user_ids))).order_by(User.id).with_for_update()).all()


def check_rate(session, user_id, operation):
    latest = session.scalar(
        select(UserOperation.created_at)
        .where(
            UserOperation.user_id == user_id,
            UserOperation.operation_type == operation,
        )
        .order_by(UserOperation.created_at.desc())
        .limit(1)
    )
    interval = timedelta(seconds=float(settings.order_interval * 60))
    if latest is not None and now() - latest < interval:
        remaining = max(1, int((latest + interval - now()).total_seconds()) + 1)
        raise ApiError(
            429,
            "ORDER_LIMIT_EXCEEDED",
            "operation interval has not elapsed",
            {"retry_after_seconds": remaining},
        )


def find(session, order_id):
    order = session.get(Order, order_id)
    if order is None:
        raise ApiError(404, "ORDER_NOT_FOUND", "order not found")
    return order


def check_owner(user, order):
    if user["role"] != "ADMIN" and user["id"] != order.user_id:
        raise ApiError(403, "ORDER_OWNERSHIP_VIOLATION", "order belongs to another user")


def locked_order(session, user, order_id):
    require_role(user, "USER", "ADMIN")
    order = find(session, order_id)
    check_owner(user, order)
    lock_users(session, user["id"], order.user_id)
    return session.scalar(
        select(Order).where(Order.id == order_id).with_for_update().execution_options(populate_existing=True)
    )


def response(session, order):
    session.flush()
    session.refresh(order)
    items = session.scalars(
        select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.product_id)
    )
    return {
        **fields(order),
        "items": [{key: value for key, value in fields(item).items() if key != "order_id"} for item in items],
    }


def read(session, user, order_id):
    require_role(user, "USER", "ADMIN")
    order = find(session, order_id)
    check_owner(user, order)
    return response(session, order)


def lock_products(session, ids):
    products = session.scalars(
        select(Product).where(Product.id.in_(set(ids))).order_by(Product.id).with_for_update()
    )
    return {product.id: product for product in products}


def validate_catalog(products, items):
    for item in items:
        product = products.get(item.product_id)
        if product is None:
            raise ApiError(
                404, "PRODUCT_NOT_FOUND", "product not found", {"product_id": str(item.product_id)}
            )
        if product.status != "ACTIVE":
            raise ApiError(
                409, "PRODUCT_INACTIVE", "product is not active", {"product_id": str(item.product_id)}
            )


def reserve(products, items):
    shortages = [
        {
            "product_id": str(item.product_id),
            "requested": item.quantity,
            "available": products[item.product_id].stock,
        }
        for item in items
        if products[item.product_id].stock < item.quantity
    ]
    if shortages:
        raise ApiError(409, "INSUFFICIENT_STOCK", "not enough stock", {"products": shortages})
    for item in items:
        products[item.product_id].stock -= item.quantity


def subtotal_for(items, prices):
    subtotal = sum((prices[item.product_id] * item.quantity for item in items), Decimal("0.00"))
    if subtotal > promos.MAX_AMOUNT:
        raise validation("items", "order subtotal exceeds decimal(12,2)")
    return subtotal


def add_items(session, order, items, prices):
    for item in items:
        session.add(
            OrderItem(
                order_id=order.id,
                product_id=item.product_id,
                quantity=item.quantity,
                price_at_order=prices[item.product_id],
            )
        )


def create(session, user, data):
    require_role(user, "USER", "ADMIN")
    lock_users(session, user["id"])
    check_rate(session, user["id"], "CREATE_ORDER")
    active = session.scalar(
        select(Order.id)
        .where(Order.user_id == user["id"], Order.status.in_(["CREATED", "PAYMENT_PENDING"]))
        .limit(1)
    )
    if active:
        raise ApiError(409, "ORDER_HAS_ACTIVE", "user already has an active order")
    products = lock_products(session, [item.product_id for item in data.items])
    validate_catalog(products, data.items)
    reserve(products, data.items)
    prices = {product.id: product.price for product in products.values()}
    subtotal = subtotal_for(data.items, prices)
    discount = Decimal("0.00")
    promo = None
    if data.promo_code is not None:
        promo = promos.find(session, code=data.promo_code)
        promos.check(promo)
        if subtotal < promo.min_order_amount:
            raise ApiError(422, "PROMO_CODE_MIN_AMOUNT", "subtotal is below promo minimum")
        discount = promos.discount(subtotal, promo.discount_type, promo.discount_value)
        promo.current_uses += 1
    order = Order(
        user_id=user["id"],
        status="CREATED",
        total_amount=subtotal - discount,
        discount_amount=discount,
        promo_code_id=promo.id if promo else None,
    )
    session.add(order)
    session.flush()
    add_items(session, order, data.items, prices)
    session.add(UserOperation(user_id=user["id"], operation_type="CREATE_ORDER"))
    return response(session, order)


def update(session, user, order_id, data):
    order = locked_order(session, user, order_id)
    if order.status != "CREATED":
        raise ApiError(409, "INVALID_STATE_TRANSITION", "only CREATED orders can be edited")
    check_rate(session, user["id"], "UPDATE_ORDER")
    old_items = session.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all()
    products = lock_products(session, [item.product_id for item in old_items + data.items])
    prices = {product.id: product.price for product in products.values()}
    for item in old_items:
        products[item.product_id].stock += item.quantity
        prices[item.product_id] = item.price_at_order
    validate_catalog(products, data.items)
    reserve(products, data.items)
    subtotal = subtotal_for(data.items, prices)
    discount = Decimal("0.00")
    if order.promo_code_id is not None:
        promo = promos.find(session, promo_id=order.promo_code_id)
        promos.check(promo, already_used=True)
        if subtotal < promo.min_order_amount:
            promo.current_uses -= 1
            order.promo_code_id = None
        else:
            discount = promos.discount(subtotal, promo.discount_type, promo.discount_value)
    session.execute(delete(OrderItem).where(OrderItem.order_id == order.id))
    add_items(session, order, data.items, prices)
    order.total_amount = subtotal - discount
    order.discount_amount = discount
    order.updated_at = now()
    session.add(UserOperation(user_id=user["id"], operation_type="UPDATE_ORDER"))
    return response(session, order)


def cancel(session, user, order_id):
    order = locked_order(session, user, order_id)
    if order.status not in {"CREATED", "PAYMENT_PENDING"}:
        raise ApiError(409, "INVALID_STATE_TRANSITION", "order cannot be canceled in this state")
    items = session.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all()
    products = lock_products(session, [item.product_id for item in items])
    for item in items:
        products[item.product_id].stock += item.quantity
    if order.promo_code_id is not None:
        promo = promos.find(session, promo_id=order.promo_code_id)
        promo.current_uses -= 1
    order.status = "CANCELED"
    return response(session, order)


def transition(session, user, order_id, data):
    require_role(user, "ADMIN")
    order = locked_order(session, user, order_id)
    if NEXT_STATE.get(order.status) != data.status:
        raise ApiError(
            409, "INVALID_STATE_TRANSITION", "not an allowed next state; use cancel endpoint for cancellation"
        )
    order.status = data.status
    return response(session, order)
