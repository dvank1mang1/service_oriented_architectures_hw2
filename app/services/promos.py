from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select

from app.errors import ApiError, validation
from app.models import Promo, fields
from app.security import now, require_role

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("9999999999.99")


def discount(subtotal, kind, value):
    if kind == "PERCENTAGE":
        amount = subtotal * min(value, Decimal("70")) / 100
    else:
        amount = min(value, subtotal)
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def create(session, user, data):
    require_role(user, "SELLER", "ADMIN")
    if data.valid_until < data.valid_from:
        raise validation("valid_until", "must not precede valid_from")
    promo = Promo(**data.model_dump())
    session.add(promo)
    session.flush()
    return fields(promo)


def find(session, *, code=None, promo_id=None):
    query = select(Promo).where(Promo.code == code) if code else select(Promo).where(Promo.id == promo_id)
    return session.scalar(query.with_for_update())


def check(promo, already_used=False):
    if (
        promo is None
        or not promo.active
        or not promo.valid_from <= now() <= promo.valid_until
        or promo.current_uses - int(already_used) >= promo.max_uses
    ):
        raise ApiError(422, "PROMO_CODE_INVALID", "promo is missing, inactive, expired or exhausted")
