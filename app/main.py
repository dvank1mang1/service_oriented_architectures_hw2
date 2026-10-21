import json
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from app.contract import SPEC, operation_for, parse_json, redact, validate_body
from app.db import Session
from app.errors import ApiError, validation
from app.security import authenticate, now
from marketplace_api.apis import auth_api, orders_api, products_api, promos_api, system_api

app = FastAPI(title="marketplace api", version="1.0.0", redirect_slashes=False)
app.openapi = lambda: SPEC
for module in (auth_api, products_api, orders_api, promos_api, system_api):
    app.include_router(module.router)


@app.exception_handler(ApiError)
async def api_error(request, error):
    return error.response()


@app.exception_handler(RequestValidationError)
async def input_error(request, error):
    details = {
        "fields": [
            {"field": ".".join(map(str, item["loc"])), "message": item["msg"]} for item in error.errors()
        ]
    }
    return ApiError(400, "VALIDATION_ERROR", "invalid request", details).response()


@app.exception_handler(HTTPException)
async def http_error(request, error):
    code = (
        "NOT_FOUND"
        if error.status_code == 404
        else "METHOD_NOT_ALLOWED"
        if error.status_code == 405
        else "VALIDATION_ERROR"
    )
    return ApiError(error.status_code, code, str(error.detail)).response()


def authenticated_user(header):
    with Session() as session:
        return authenticate(session, header)


def integrity_error(error):
    constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", "")
    if constraint == "users_email_key":
        return ApiError(409, "EMAIL_EXISTS", "email is already registered")
    if constraint == "promo_codes_code_key":
        return ApiError(409, "PROMO_CODE_EXISTS", "promo code already exists")
    if constraint == "uq_orders_active_user":
        return ApiError(409, "ORDER_HAS_ACTIVE", "user already has an active order")
    return ApiError(500, "INTERNAL_ERROR", "database invariant violation")


@app.middleware("http")
async def request_context(request: Request, call_next):
    started = time.perf_counter()
    request_id = str(uuid.uuid4())
    request.state.user = None
    body = None
    response = None
    try:
        raw = await request.body()
        if len(raw) > 1_048_576:
            raise validation("body", "request body exceeds 1 MiB")
        if raw:
            body = parse_json(raw)
        operation = operation_for(request.url.path, request.method)
        if operation:
            if operation.get("security", SPEC["security"]):
                request.state.user = await run_in_threadpool(
                    authenticated_user, request.headers.get("authorization")
                )
            if raw and request.headers.get("content-type", "").split(";")[0].lower() != "application/json":
                raise validation("body", "content-type must be application/json")
            validate_body(operation, body)
        response = await call_next(request)
    except ApiError as error:
        response = error.response()
    except IntegrityError as error:
        response = integrity_error(error).response()
    except SQLAlchemyError:
        response = ApiError(
            503, "SERVICE_UNAVAILABLE", "database operation unavailable; retry later"
        ).response()
    except Exception as error:
        print(
            json.dumps(
                {"event": "request_failed", "request_id": request_id, "exception_type": type(error).__name__}
            ),
            flush=True,
        )
        response = ApiError(500, "INTERNAL_ERROR", "unexpected server error").response()
    response.headers["X-Request-Id"] = request_id
    entry = {
        "request_id": request_id,
        "method": request.method,
        "endpoint": request.url.path,
        "status_code": response.status_code,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "user_id": str(request.state.user["id"]) if request.state.user else None,
        "timestamp": now().isoformat(),
    }
    if request.method in {"POST", "PUT", "DELETE"}:
        entry["request_body"] = redact(body)
    print(json.dumps(entry, default=str), flush=True)
    return response


@app.get("/openapi.yaml", include_in_schema=False)
def contract_file():
    from fastapi.responses import FileResponse

    return FileResponse("openapi/marketplace.yaml", media_type="application/yaml")
