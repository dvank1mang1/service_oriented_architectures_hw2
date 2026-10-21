from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
import uuid

import httpx
import jwt
import pytest
from openapi_spec_validator import validate
from sqlalchemy import text

from app.config import settings
from app.contract import SPEC
from app.db import Session
from app.security import now
from conftest import PASSWORD, age_operations, assert_contract, scalar


def product_input(product, **changes):
    return {
        **{key: product[key] for key in ("name", "description", "price", "stock", "category", "status")},
        **changes,
    }


def promo(api, **changes):
    return api.call(
        "POST",
        "/promo-codes",
        {
            "code": "SAVE_20",
            "discount_type": "PERCENTAGE",
            "discount_value": 20,
            "min_order_amount": 100,
            "max_uses": 5,
            "valid_from": (now() - timedelta(days=1)).isoformat(),
            "valid_until": (now() + timedelta(days=1)).isoformat(),
            "active": True,
            **changes,
        },
        who="seller",
        expected=201,
    )


def error(api, method, path, data, code, status, who="user"):
    result = api.call(method, path, data, who=who, expected=status)
    assert result["error_code"] == code
    return result


def test_contract_and_migration(api):
    validate(SPEC)
    assert api.call("GET", "/health", who=None) == {"status": "ok"}
    served = api.client.get("/openapi.json").json()
    assert served == SPEC
    assert scalar("SELECT count(*) FROM flyway_schema_history WHERE success") == 1
    assert (
        scalar(
            "SELECT count(*) FROM pg_indexes WHERE tablename='products' AND indexname='ix_products_status'"
        )
        == 1
    )


def test_product_crud_pagination_and_soft_delete(api):
    first = api.product(price=19.99, category="Exact")
    second = api.product(status="INACTIVE", category="exact")
    page = api.call("GET", "/products?page=0&size=1")
    assert len(page["items"]) == 1 and page["totalElements"] == 2 and page["size"] == 1
    assert api.call("GET", "/products?page=10&size=1")["items"] == []
    assert api.call("GET", "/products?category=Exact")["totalElements"] == 1
    assert api.call("GET", "/products?status=INACTIVE")["items"][0]["id"] == second["id"]
    updated = api.call("PUT", f"/products/{first['id']}", product_input(first, name="changed"), who="seller")
    assert updated["created_at"] == first["created_at"]
    assert updated["updated_at"] > first["updated_at"]
    api.call("DELETE", f"/products/{first['id']}", who="seller", expected=204)
    api.call("DELETE", f"/products/{first['id']}", who="seller", expected=204)
    assert api.call("GET", f"/products/{first['id']}")["status"] == "ARCHIVED"
    assert scalar("SELECT count(*) FROM products") == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"name": "x" * 256},
        {"description": "x" * 4001},
        {"description": 2},
        {"price": 0},
        {"price": -1},
        {"price": 0.001},
        {"price": "12.50"},
        {"stock": -1},
        {"stock": 1.5},
        {"stock": True},
        {"category": ""},
        {"category": "x" * 101},
        {"status": "MISSING"},
        {"status": None},
        {"id": str(uuid.uuid4())},
        {"extra": "value"},
    ],
)
def test_product_validation(api, changes):
    body = {"name": "item", "price": 1, "stock": 1, "category": "cat", "status": "ACTIVE", **changes}
    result = error(api, "POST", "/products", body, "VALIDATION_ERROR", 400, "seller")
    assert result["details"]["fields"]
    assert scalar("SELECT count(*) FROM products") == 0


@pytest.mark.parametrize("query", ["page=-1", "page=abc", "size=0", "size=101", "status=BAD", "category="])
def test_query_validation(api, query):
    error(api, "GET", "/products?" + query, None, "VALIDATION_ERROR", 400)


def test_product_boundaries(api):
    result = api.product(name="x" * 255, category="x" * 100, description="x" * 4000, price=0.01, stock=0)
    assert result["price"] == 0.01
    error(api, "GET", "/products/not-a-uuid", None, "VALIDATION_ERROR", 400)
    error(api, "GET", f"/products/{uuid.uuid4()}", None, "PRODUCT_NOT_FOUND", 404)
    error(api, "POST", "/products", {}, "VALIDATION_ERROR", 400, "seller")


def test_role_matrix_products_and_promos(api):
    product = api.product()
    body = product_input(product)
    error(api, "POST", "/products", body, "ACCESS_DENIED", 403)
    for who in ("user", "seller2"):
        error(api, "PUT", f"/products/{product['id']}", body, "ACCESS_DENIED", 403, who)
        error(api, "DELETE", f"/products/{product['id']}", None, "ACCESS_DENIED", 403, who)
    error(api, "POST", "/products", {**body, "seller_id": api.ids["seller2"]}, "ACCESS_DENIED", 403, "seller")
    owned = api.product(who="admin", seller_id=api.ids["seller2"])
    assert owned["seller_id"] == api.ids["seller2"]
    api.call("PUT", f"/products/{product['id']}", body, who="admin")
    for who in ("user", "seller", "admin"):
        api.call("GET", "/products", who=who)
        api.call("GET", f"/products/{product['id']}", who=who)
    promo_row = promo(api)
    promo_body = {key: value for key, value in promo_row.items() if key not in {"id", "current_uses"}}
    error(api, "POST", "/promo-codes", promo_body, "ACCESS_DENIED", 403)
    error(api, "POST", "/promo-codes", promo_body, "PROMO_CODE_EXISTS", 409, "admin")
    api.call("POST", "/promo-codes", {**promo_body, "code": "ADMIN_20"}, who="admin", expected=201)
    api.call("DELETE", f"/products/{product['id']}", who="admin", expected=204)


def test_register_login_refresh_and_secret_storage(api):
    user = api.call(
        "POST", "/auth/register", {"email": "New@Example.com", "password": PASSWORD}, who=None, expected=201
    )
    assert user["role"] == "USER" and user["email"] == "new@example.com"
    hashed = scalar("SELECT password_hash FROM users WHERE email='new@example.com'")
    assert hashed.startswith("$argon2id$") and PASSWORD not in hashed
    error(
        api,
        "POST",
        "/auth/register",
        {"email": "new@example.com", "password": PASSWORD},
        "EMAIL_EXISTS",
        409,
        None,
    )
    error(
        api,
        "POST",
        "/auth/register",
        {"email": "x@example.com", "password": PASSWORD, "role": "ADMIN"},
        "VALIDATION_ERROR",
        400,
        None,
    )
    error(
        api,
        "POST",
        "/auth/login",
        {"email": "new@example.com", "password": "wrong"},
        "INVALID_CREDENTIALS",
        401,
        None,
    )
    pair = api.call("POST", "/auth/login", {"email": user["email"], "password": PASSWORD}, who=None)
    claims = jwt.decode(
        pair["access_token"], settings.jwt_secret, algorithms=["HS256"], audience=settings.jwt_audience
    )
    assert claims["role"] == "USER" and claims["exp"] - claims["iat"] == 1200
    fresh = api.call("POST", "/auth/refresh", {"refresh_token": pair["refresh_token"]}, who=None)
    assert fresh["refresh_token"] != pair["refresh_token"]
    error(
        api,
        "POST",
        "/auth/refresh",
        {"refresh_token": pair["refresh_token"]},
        "REFRESH_TOKEN_INVALID",
        401,
        None,
    )
    error(
        api,
        "POST",
        "/auth/refresh",
        {"refresh_token": pair["access_token"]},
        "REFRESH_TOKEN_INVALID",
        401,
        None,
    )


def test_access_tokens(api):
    error(api, "GET", "/products", None, "TOKEN_INVALID", 401, None)
    for token in ("broken", "Bearer broken"):
        error_body = api.call("GET", "/products", who=None, expected=401, headers={"Authorization": token})
        assert error_body["error_code"] == "TOKEN_INVALID"
    claims = jwt.decode(api.tokens["user"], options={"verify_signature": False})
    expired = jwt.encode(
        {**claims, "iat": int(now().timestamp()) - 120, "exp": int(now().timestamp()) - 60},
        settings.jwt_secret,
        algorithm="HS256",
    )
    result = api.call(
        "GET", "/products", who=None, expected=401, headers={"Authorization": "Bearer " + expired}
    )
    assert result["error_code"] == "TOKEN_EXPIRED"
    wrong_role = jwt.encode({**claims, "role": "ADMIN"}, settings.jwt_secret, algorithm="HS256")
    result = api.call(
        "GET", "/products", who=None, expected=401, headers={"Authorization": "Bearer " + wrong_role}
    )
    assert result["error_code"] == "TOKEN_INVALID"


def test_order_create_snapshot_update_cancel(api):
    product = api.product()
    order = api.order(product, quantity=2)
    assert order["total_amount"] == 200.50 and order["status"] == "CREATED"
    assert scalar("SELECT stock FROM products") == 8
    api.call("PUT", f"/products/{product['id']}", product_input(product, price=500, stock=8), who="seller")
    assert api.call("GET", f"/orders/{order['id']}")["total_amount"] == 200.50
    updated = api.call(
        "PUT", f"/orders/{order['id']}", {"items": [{"product_id": product["id"], "quantity": 3}]}
    )
    assert updated["total_amount"] == 300.75 and scalar("SELECT stock FROM products") == 7
    assert updated["updated_at"] > order["updated_at"]
    canceled = api.call("POST", f"/orders/{order['id']}/cancel")
    assert canceled["status"] == "CANCELED" and scalar("SELECT stock FROM products") == 10
    error(api, "POST", f"/orders/{order['id']}/cancel", None, "INVALID_STATE_TRANSITION", 409)
    assert scalar("SELECT stock FROM products") == 10


def test_order_error_precedence_and_rate_limit(api):
    product = api.product()
    order = api.order(product)
    body = {"items": [{"product_id": product["id"], "quantity": 1}]}
    error(api, "POST", "/orders", body, "ORDER_LIMIT_EXCEEDED", 429)
    age_operations()
    error(api, "POST", "/orders", body, "ORDER_HAS_ACTIVE", 409)
    api.call("PUT", f"/orders/{order['id']}", body)
    error(api, "PUT", f"/orders/{order['id']}", body, "ORDER_LIMIT_EXCEEDED", 429)
    assert scalar("SELECT count(*) FROM user_operations") == 2


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"product_id": str(uuid.uuid4()), "quantity": 0}],
        [{"product_id": str(uuid.uuid4()), "quantity": 1000}],
        [{"product_id": "bad", "quantity": 1}],
        [{"product_id": str(uuid.uuid4()), "quantity": 1}] * 51,
    ],
)
def test_order_input_validation(api, items):
    error(api, "POST", "/orders", {"items": items}, "VALIDATION_ERROR", 400)
    assert scalar("SELECT count(*) FROM orders") == 0


def test_duplicate_products_and_invalid_promo_format(api):
    product = api.product()
    item = {"product_id": product["id"], "quantity": 1}
    error(api, "POST", "/orders", {"items": [item, {**item, "quantity": 2}]}, "VALIDATION_ERROR", 400)
    error(api, "POST", "/orders", {"items": [item], "promo_code": "bad"}, "VALIDATION_ERROR", 400)
    assert scalar("SELECT stock FROM products") == 10


@pytest.mark.parametrize("status", ["INACTIVE", "ARCHIVED"])
def test_inactive_products(api, status):
    product = api.product(status=status)
    error(
        api,
        "POST",
        "/orders",
        {"items": [{"product_id": product["id"], "quantity": 1}]},
        "PRODUCT_INACTIVE",
        409,
    )


def test_stock_and_missing_product_rollback(api):
    first = api.product(stock=1)
    second = api.product(stock=0)
    body = {"items": [{"product_id": p["id"], "quantity": 2} for p in (first, second)]}
    result = error(api, "POST", "/orders", body, "INSUFFICIENT_STOCK", 409)
    assert len(result["details"]["products"]) == 2
    assert scalar("SELECT sum(stock) FROM products") == 1
    assert scalar("SELECT count(*) FROM user_operations") == 0
    body["items"][0]["product_id"] = str(uuid.uuid4())
    error(api, "POST", "/orders", body, "PRODUCT_NOT_FOUND", 404)


def test_update_failure_rolls_back_old_reservation_and_items(api):
    first = api.product()
    second = api.product(stock=0)
    order = api.order(first, quantity=2)
    error(
        api,
        "PUT",
        f"/orders/{order['id']}",
        {"items": [{"product_id": second["id"], "quantity": 1}]},
        "INSUFFICIENT_STOCK",
        409,
    )
    assert scalar("SELECT stock FROM products WHERE id=:id", id=first["id"]) == 8
    unchanged = api.call("GET", f"/orders/{order['id']}")
    assert unchanged == order
    assert scalar("SELECT count(*) FROM user_operations WHERE operation_type='UPDATE_ORDER'") == 0


def test_promo_discount_cap_and_cancel(api):
    product = api.product(price=100)
    promo(api, discount_value=90, max_uses=1)
    order = api.order(product, quantity=2, promo_code="SAVE_20")
    assert order["discount_amount"] == 140 and order["total_amount"] == 60
    assert scalar("SELECT current_uses FROM promo_codes") == 1
    updated = api.call(
        "PUT", f"/orders/{order['id']}", {"items": [{"product_id": product["id"], "quantity": 3}]}
    )
    assert updated["discount_amount"] == 210
    assert scalar("SELECT current_uses FROM promo_codes") == 1
    api.call("POST", f"/orders/{order['id']}/cancel")
    assert scalar("SELECT current_uses FROM promo_codes") == 0


def test_promo_minimum_removed_on_update(api):
    product = api.product(price=100)
    promo(api, min_order_amount=200)
    order = api.order(product, quantity=2, promo_code="SAVE_20")
    updated = api.call(
        "PUT", f"/orders/{order['id']}", {"items": [{"product_id": product["id"], "quantity": 1}]}
    )
    assert (
        updated["promo_code_id"] is None
        and updated["discount_amount"] == 0
        and updated["total_amount"] == 100
    )
    assert scalar("SELECT current_uses FROM promo_codes") == 0
    api.call("POST", f"/orders/{order['id']}/cancel")
    assert scalar("SELECT current_uses FROM promo_codes") == 0


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"active": False}, "PROMO_CODE_INVALID"),
        (
            {
                "valid_from": (now() - timedelta(days=3)).isoformat(),
                "valid_until": (now() - timedelta(days=1)).isoformat(),
            },
            "PROMO_CODE_INVALID",
        ),
        ({"valid_from": (now() + timedelta(hours=1)).isoformat()}, "PROMO_CODE_INVALID"),
        ({"min_order_amount": 500}, "PROMO_CODE_MIN_AMOUNT"),
    ],
)
def test_promo_failures_rollback_stock(api, changes, code):
    product = api.product(price=100)
    promo(api, **changes)
    error(
        api,
        "POST",
        "/orders",
        {"items": [{"product_id": product["id"], "quantity": 1}], "promo_code": "SAVE_20"},
        code,
        422,
    )
    assert scalar("SELECT stock FROM products") == 10
    assert scalar("SELECT count(*) FROM orders") == 0
    assert scalar("SELECT current_uses FROM promo_codes") == 0


def test_fixed_discount_and_missing_promo(api):
    product = api.product(price=100)
    body = {"items": [{"product_id": product["id"], "quantity": 1}], "promo_code": "NONE_1"}
    error(api, "POST", "/orders", body, "PROMO_CODE_INVALID", 422)
    promo(api, discount_type="FIXED_AMOUNT", discount_value=200)
    order = api.order(product, promo_code="SAVE_20")
    assert order["total_amount"] == 0 and order["discount_amount"] == 100


def test_order_ownership_roles_and_states(api):
    product = api.product()
    order = api.order(product)
    path = f"/orders/{order['id']}"
    body = {"items": [{"product_id": product["id"], "quantity": 1}]}
    for method, suffix, data in (("GET", "", None), ("PUT", "", body), ("POST", "/cancel", None)):
        error(api, method, path + suffix, data, "ORDER_OWNERSHIP_VIOLATION", 403, "other")
        error(api, method, path + suffix, data, "ACCESS_DENIED", 403, "seller")
    error(api, "POST", "/orders", body, "ACCESS_DENIED", 403, "seller")
    error(api, "GET", f"/orders/{uuid.uuid4()}", None, "ORDER_NOT_FOUND", 404)
    api.call("GET", path, who="admin")
    error(api, "POST", path + "/status", {"status": "PAID"}, "ACCESS_DENIED", 403)
    error(api, "POST", path + "/status", {"status": "PAID"}, "INVALID_STATE_TRANSITION", 409, "admin")
    for state in ("PAYMENT_PENDING", "PAID", "SHIPPED", "COMPLETED"):
        assert api.call("POST", path + "/status", {"status": state}, who="admin")["status"] == state
        error(api, "PUT", path, body, "INVALID_STATE_TRANSITION", 409)
    error(api, "POST", path + "/cancel", None, "INVALID_STATE_TRANSITION", 409)
    error(api, "POST", path + "/status", {"status": "CREATED"}, "INVALID_STATE_TRANSITION", 409, "admin")


def test_admin_updates_and_cancels_another_order(api):
    product = api.product()
    order = api.order(product)
    api.call(
        "PUT",
        f"/orders/{order['id']}",
        {"items": [{"product_id": product["id"], "quantity": 2}]},
        who="admin",
    )
    api.call("POST", f"/orders/{order['id']}/status", {"status": "PAYMENT_PENDING"}, who="admin")
    api.call("POST", f"/orders/{order['id']}/cancel", who="admin")
    assert scalar("SELECT stock FROM products") == 10
    assert api.order(product, who="admin")["user_id"] == api.ids["admin"]


def concurrent(api, method, path, bodies, users):
    def send(entry):
        body, user = entry
        response = httpx.request(
            method,
            api.base + path,
            json=body,
            headers={"Authorization": "Bearer " + api.tokens[user]},
            timeout=20,
        )
        assert_contract(response)
        return response

    with ThreadPoolExecutor(max_workers=len(users)) as pool:
        return list(pool.map(send, zip(bodies, users)))


def test_concurrent_last_stock(api):
    product = api.product(stock=1)
    body = {"items": [{"product_id": product["id"], "quantity": 1}]}
    results = concurrent(api, "POST", "/orders", [body, body], ["user", "other"])
    assert sorted(r.status_code for r in results) == [201, 409]
    assert scalar("SELECT stock FROM products") == 0
    assert scalar("SELECT count(*) FROM orders") == 1


def test_concurrent_same_user(api):
    product = api.product()
    body = {"items": [{"product_id": product["id"], "quantity": 1}]}
    results = concurrent(api, "POST", "/orders", [body, body], ["user", "user"])
    assert sorted(r.status_code for r in results) == [201, 429]
    assert scalar("SELECT count(*) FROM orders") == 1
    assert scalar("SELECT stock FROM products") == 9


def test_concurrent_last_promo_use(api):
    products = [api.product(price=100), api.product(price=100)]
    promo(api, max_uses=1)
    bodies = [{"items": [{"product_id": p["id"], "quantity": 1}], "promo_code": "SAVE_20"} for p in products]
    results = concurrent(api, "POST", "/orders", bodies, ["user", "other"])
    assert sorted(r.status_code for r in results) == [201, 422]
    assert scalar("SELECT current_uses FROM promo_codes") == 1
    assert scalar("SELECT sum(stock) FROM products") == 19


def test_concurrent_cancel_returns_stock_once(api):
    product = api.product(price=100)
    promo(api)
    order = api.order(product, promo_code="SAVE_20")
    results = concurrent(api, "POST", f"/orders/{order['id']}/cancel", [None, None], ["user", "user"])
    assert sorted(r.status_code for r in results) == [200, 409]
    assert scalar("SELECT current_uses FROM promo_codes") == 0
    assert scalar("SELECT stock FROM products") == 10


def test_expired_promo_update_is_atomic(api):
    product = api.product(price=100)
    promo(api)
    order = api.order(product, promo_code="SAVE_20")
    with Session.begin() as session:
        session.execute(text("UPDATE promo_codes SET active=false"))
    error(
        api,
        "PUT",
        f"/orders/{order['id']}",
        {"items": [{"product_id": product["id"], "quantity": 2}]},
        "PROMO_CODE_INVALID",
        422,
    )
    assert scalar("SELECT stock FROM products") == 9
    assert api.call("GET", f"/orders/{order['id']}") == order


def test_decimal_precision(api):
    product = api.product(price=0.29, stock=999)
    order = api.order(product, quantity=999)
    assert Decimal(str(order["total_amount"])) == Decimal("289.71")


def test_invalid_json_and_request_ids(api):
    response = api.client.post(
        "/auth/login", content='{"password": "DO_NOT_LOG_THIS",', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400 and response.json()["error_code"] == "VALIDATION_ERROR"
    uuid.UUID(response.headers["X-Request-Id"])
    response2 = api.client.get("/health", headers={"X-Request-Id": "untrusted"})
    uuid.UUID(response2.headers["X-Request-Id"])
    assert response2.headers["X-Request-Id"] != response.headers["X-Request-Id"]


def test_replacement_adds_current_prices_and_restores_removed_items(api):
    first = api.product(price=100)
    second = api.product(price=200)
    order = api.order(first, quantity=2)
    updated = api.call(
        "PUT", f"/orders/{order['id']}", {"items": [{"product_id": second["id"], "quantity": 3}]}
    )
    assert updated["total_amount"] == 600
    assert scalar("SELECT stock FROM products WHERE id=:id", id=first["id"]) == 10
    assert scalar("SELECT stock FROM products WHERE id=:id", id=second["id"]) == 7
    assert scalar("SELECT count(*) FROM order_items") == 1


def test_required_error_names_missing_fields(api):
    result = error(api, "POST", "/products", {}, "VALIDATION_ERROR", 400, "seller")
    fields = {item["field"] for item in result["details"]["fields"]}
    assert fields == {"body.name", "body.price", "body.stock", "body.category", "body.status"}


def test_numeric_overflow_is_validation_error(api):
    body = {"name": "item", "price": 1e100, "stock": 1, "category": "cat", "status": "ACTIVE"}
    error(api, "POST", "/products", body, "VALIDATION_ERROR", 400, "seller")
    product = api.product(price=9999999999.99)
    error(
        api,
        "POST",
        "/orders",
        {"items": [{"product_id": product["id"], "quantity": 2}]},
        "VALIDATION_ERROR",
        400,
    )
    assert scalar("SELECT stock FROM products") == 10


def test_stock_update_keeps_room_to_restore_reservations(api):
    product = api.product()
    order = api.order(product, quantity=2)
    error(
        api,
        "PUT",
        f"/products/{product['id']}",
        product_input(product, stock=2147483647),
        "VALIDATION_ERROR",
        400,
        "seller",
    )
    api.call("POST", f"/orders/{order['id']}/cancel")
    assert scalar("SELECT stock FROM products") == 10


def test_order_accepts_fifty_unique_items(api):
    products = [api.product(price=1, stock=1) for _ in range(50)]
    result = api.call(
        "POST", "/orders", {"items": [{"product_id": p["id"], "quantity": 1} for p in products]}, expected=201
    )
    assert len(result["items"]) == 50 and result["total_amount"] == 50
    assert scalar("SELECT sum(stock) FROM products") == 0


def test_refresh_concurrent_consumption(api):
    pair = api.call("POST", "/auth/login", {"email": "user@example.com", "password": PASSWORD}, who=None)

    def send(_):
        return httpx.post(
            api.base + "/auth/refresh", json={"refresh_token": pair["refresh_token"]}, timeout=20
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, range(2)))
    assert sorted(result.status_code for result in results) == [200, 401]
    assert (
        next(result for result in results if result.status_code == 401).json()["error_code"]
        == "REFRESH_TOKEN_INVALID"
    )
    for result in results:
        assert_contract(result)


def test_refresh_as_access_and_expired_refresh(api):
    pair = api.call("POST", "/auth/login", {"email": "user@example.com", "password": PASSWORD}, who=None)
    result = api.call(
        "GET",
        "/products",
        who=None,
        expected=401,
        headers={"Authorization": "Bearer " + pair["refresh_token"]},
    )
    assert result["error_code"] == "TOKEN_INVALID"
    claims = jwt.decode(pair["refresh_token"], options={"verify_signature": False})
    expired = jwt.encode(
        {**claims, "iat": int(now().timestamp()) - 120, "exp": int(now().timestamp()) - 60},
        settings.jwt_secret,
        algorithm="HS256",
    )
    error(api, "POST", "/auth/refresh", {"refresh_token": expired}, "REFRESH_TOKEN_INVALID", 401, None)


def test_bad_email_and_promo_dates(api):
    error(
        api,
        "POST",
        "/auth/register",
        {"email": "not-an-email", "password": PASSWORD},
        "VALIDATION_ERROR",
        400,
        None,
    )
    error(
        api,
        "POST",
        "/promo-codes",
        {
            "code": "SAVE_1",
            "discount_type": "PERCENTAGE",
            "discount_value": 10,
            "min_order_amount": 0,
            "max_uses": 1,
            "valid_from": now().isoformat(),
            "valid_until": (now() - timedelta(days=1)).isoformat(),
            "active": True,
        },
        "VALIDATION_ERROR",
        400,
        "seller",
    )
