import getpass
import json
import os
import urllib.error
import urllib.request
import uuid

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8080")


def call(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method,
        headers=headers,
    )
    try:
        response = urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    body = response.read()
    result = json.loads(body) if body else None
    print(f"{method} {path}: {response.status}")
    if result and "access_token" not in result:
        print(json.dumps(result, indent=2))
    if response.status >= 400:
        raise SystemExit("demo stopped; inspect the error above")
    return result


def main():
    seller_email = input("seller email: ")
    password = getpass.getpass("seller password: ")
    seller = call("POST", "/auth/login", {"email": seller_email, "password": password})["access_token"]
    email = f"buyer-{uuid.uuid4().hex[:12]}@example.com"
    password = uuid.uuid4().hex
    call("POST", "/auth/register", {"email": email, "password": password})
    buyer = call("POST", "/auth/login", {"email": email, "password": password})["access_token"]
    product = call(
        "POST",
        "/products",
        {
            "name": "demo keyboard",
            "price": 1250.50,
            "stock": 10,
            "category": "electronics",
            "status": "ACTIVE",
        },
        seller,
    )
    order = call("POST", "/orders", {"items": [{"product_id": product["id"], "quantity": 2}]}, buyer)
    call("GET", f"/products/{product['id']}", token=buyer)
    call("GET", f"/orders/{order['id']}", token=buyer)
    call("POST", f"/orders/{order['id']}/cancel", token=buyer)
    call("GET", f"/products/{product['id']}", token=buyer)
    print("inspect orders, order_items and products with the SELECT queries in docs/defense.sql")


if __name__ == "__main__":
    main()
