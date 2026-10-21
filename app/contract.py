import json
import re
from decimal import Decimal, localcontext
from pathlib import Path

import yaml
from jsonschema import ValidationError, validators
from openapi_schema_validator import OAS30Validator, oas30_format_checker

from app.errors import ApiError, validation

SPEC = yaml.safe_load(Path("openapi/marketplace.yaml").read_text())


def exact_multiple(validator, divisor, instance, schema):
    if isinstance(instance, (int, float, Decimal)) and not isinstance(instance, bool):
        value, step = Decimal(str(instance)), Decimal(str(divisor))
        if not value.is_finite():
            yield ValidationError("must be a finite number")
            return
        with localcontext() as context:
            context.prec = max(28, len(value.as_tuple().digits) + abs(value.as_tuple().exponent) + 10)
            remainder = value % step
        if remainder:
            yield ValidationError(f"must be a multiple of {divisor}")


ContractValidator = validators.extend(OAS30Validator, {"multipleOf": exact_multiple})
OPERATIONS = []
for path, methods in SPEC["paths"].items():
    for method, operation in methods.items():
        if method in {"get", "post", "put", "delete"}:
            OPERATIONS.append(
                (re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"), method.upper(), operation)
            )


def operation_for(path, method):
    return next((op for pattern, verb, op in OPERATIONS if verb == method and pattern.fullmatch(path)), None)


def schema_validator(schema):
    return ContractValidator(
        {**schema, "components": SPEC["components"]}, format_checker=oas30_format_checker
    )


def parse_json(raw):
    try:
        return json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeDecodeError):
        raise validation("body", "must contain valid json") from None


def validate_body(operation, data):
    body = operation.get("requestBody")
    if not body:
        if data is not None:
            raise validation("body", "this operation does not accept a request body")
        return
    schema = body["content"]["application/json"]["schema"]
    checker = schema_validator(schema)
    errors = []
    for error in checker.iter_errors(data):
        field = ".".join(str(part) for part in error.absolute_path) or "body"
        if error.validator == "required":
            for missing in error.validator_value:
                if missing not in error.instance:
                    errors.append({"field": f"{field}.{missing}", "message": "required field is missing"})
        elif error.validator == "additionalProperties" and isinstance(error.instance, dict):
            for extra in error.instance.keys() - error.schema.get("properties", {}).keys():
                errors.append({"field": f"{field}.{extra}", "message": "unexpected field"})
        else:
            errors.append({"field": field, "message": f"violates {error.validator}: {error.validator_value}"})
    if errors:
        errors = [dict(item) for item in dict.fromkeys(tuple(error.items()) for error in errors)]
        raise ApiError(400, "VALIDATION_ERROR", "invalid request", {"fields": errors})
    if isinstance(data, dict) and "items" in data:
        seen = set()
        for index, item in enumerate(data["items"]):
            product_id = item["product_id"].lower()
            if product_id in seen:
                raise validation(f"items.{index}.product_id", "duplicate products are not allowed")
            seen.add(product_id)


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if any(word in key.lower() for word in ("password", "token", "secret", "authorization"))
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
