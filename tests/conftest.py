import os
import uuid

import httpx
import pytest
from sqlalchemy import text

from app.contract import SPEC, schema_validator
from app.db import Session
from app.models import User
from app.security import hasher

PASSWORD = "integration-test-password-123"


def assert_contract(response):
    path = response.request.url.path
    method = response.request.method.lower()
    import re

    template = next(
        (key for key in SPEC["paths"] if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", key), path)), None
    )
    if template is None or method not in SPEC["paths"][template]:
        return
    operation = SPEC["paths"][template][method]
    assert str(response.status_code) in operation["responses"], response.text
    description = operation["responses"][str(response.status_code)]
    if "$ref" in description:
        description = SPEC["components"]["responses"][description["$ref"].split("/")[-1]]
    if "content" in description:
        schema = description["content"]["application/json"]["schema"]
        schema_validator(schema).validate(response.json())
    else:
        assert not response.content
    uuid.UUID(response.headers["X-Request-Id"])


class Api:
    def __init__(self):
        self.base = os.environ.get("BASE_URL", "http://localhost:8082")
        self.client = httpx.Client(base_url=self.base, timeout=20)
        self.tokens = {}

    def call(self, method, path, data=None, who="user", expected=200, **kwargs):
        headers = kwargs.pop("headers", {})
        if who is not None:
            headers["Authorization"] = "Bearer " + self.tokens[who]
        response = self.client.request(method, path, json=data, headers=headers, **kwargs)
        assert response.status_code == expected, (method, path, response.status_code, response.text)
        assert_contract(response)
        return response.json() if response.content else None

    def product(self, **values):
        who = values.pop("who", "seller")
        return self.call(
            "POST",
            "/products",
            {
                "name": "keyboard",
                "description": None,
                "price": 100.25,
                "stock": 10,
                "category": "electronics",
                "status": "ACTIVE",
                **values,
            },
            who=who,
            expected=201,
        )

    def order(self, product, quantity=1, who="user", **values):
        return self.call(
            "POST",
            "/orders",
            {"items": [{"product_id": product["id"], "quantity": quantity}], **values},
            who=who,
            expected=201,
        )


@pytest.fixture
def api():
    assert os.environ["DATABASE_URL"].endswith("/marketplace_test"), (
        "tests must never reset a non-test database"
    )
    with Session.begin() as session:
        session.execute(
            text(
                "TRUNCATE refresh_sessions, user_operations, order_items, orders, promo_codes, products, users CASCADE"
            )
        )
        encoded = hasher.hash(PASSWORD)
        ids = {}
        for name, role in (
            ("user", "USER"),
            ("other", "USER"),
            ("seller", "SELLER"),
            ("seller2", "SELLER"),
            ("admin", "ADMIN"),
        ):
            user = User(id=uuid.uuid4(), email=f"{name}@example.com", password_hash=encoded, role=role)
            session.add(user)
            ids[name] = str(user.id)
    api = Api()
    api.ids = ids
    for name in ids:
        result = api.call(
            "POST", "/auth/login", {"email": f"{name}@example.com", "password": PASSWORD}, who=None
        )
        api.tokens[name] = result["access_token"]
    yield api
    api.client.close()


def age_operations():
    with Session.begin() as session:
        session.execute(
            text("UPDATE user_operations SET created_at = clock_timestamp() - interval '2 minutes'")
        )


def scalar(sql, **params):
    with Session() as session:
        return session.execute(text(sql), params).scalar()
