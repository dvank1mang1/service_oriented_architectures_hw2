from fastapi import Response
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.db import Session
from app.errors import ApiError
from app.models import User, fields
from app.security import dummy_hash, hasher, issue_tokens, rotate, verify_password
from app.services import catalog, orders, promos


def health(request):
    try:
        with Session() as session:
            session.execute(text("SELECT 1 FROM products LIMIT 1"))
        return {"status": "ok"}
    except SQLAlchemyError:
        raise ApiError(
            503, "SERVICE_UNAVAILABLE", "database is unavailable or migrations are missing"
        ) from None


def register(request, register_request):
    with Session.begin() as session:
        user = User(
            email=register_request.email.lower(),
            password_hash=hasher.hash(register_request.password),
            role="USER",
        )
        session.add(user)
        session.flush()
        request.state.user = {"id": user.id, "role": user.role}
        return {"id": user.id, "email": user.email, "role": user.role}


def login(request, login_request):
    with Session.begin() as session:
        user = session.scalar(select(User).where(User.email == login_request.email.lower()))
        valid = verify_password(user.password_hash if user else dummy_hash, login_request.password)
        if user is None or not valid:
            raise ApiError(401, "INVALID_CREDENTIALS", "invalid email or password")
        request.state.user = {"id": user.id, "role": user.role}
        return issue_tokens(session, user)


def refresh(request, refresh_request):
    with Session.begin() as session:
        return rotate(session, refresh_request.refresh_token)


def list_products(request, page=0, size=20, status=None, category=None):
    with Session.begin() as session:
        return catalog.listing(session, page, size, status, category)


def get_product(request, id):
    with Session.begin() as session:
        return fields(catalog.find(session, id))


def create_product(request, product_create):
    with Session.begin() as session:
        return catalog.create(session, request.state.user, product_create)


def update_product(request, id, product_update):
    with Session.begin() as session:
        return catalog.update(session, request.state.user, id, product_update)


def archive_product(request, id):
    with Session.begin() as session:
        catalog.archive(session, request.state.user, id)
    return Response(status_code=204)


def create_promo(request, promo_create):
    with Session.begin() as session:
        return promos.create(session, request.state.user, promo_create)


def create_order(request, order_create):
    with Session.begin() as session:
        return orders.create(session, request.state.user, order_create)


def get_order(request, id):
    with Session.begin() as session:
        return orders.read(session, request.state.user, id)


def update_order(request, id, order_update):
    with Session.begin() as session:
        return orders.update(session, request.state.user, id, order_update)


def cancel_order(request, id):
    with Session.begin() as session:
        return orders.cancel(session, request.state.user, id)


def transition_order(request, id, order_transition):
    with Session.begin() as session:
        return orders.transition(session, request.state.user, id, order_transition)
