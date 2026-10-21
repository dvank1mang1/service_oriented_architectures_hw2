# marketplace api

homework 2: a contract-first api for products, orders and promo codes.
the application is written in python. it uses fastapi, pydantic, sqlalchemy,
postgresql and flyway. docker builds the generated code before starting the api.

## start

you need docker desktop with compose, python 3 for the setup script, and make.
java, node and a local postgres installation are not needed.

```bash
git clone https://github.com/dvank1mang1/service_oriented_architectures_hw2.git
cd service_oriented_architectures_hw2
make setup
make up
curl -i http://localhost:8081/health
```

expect `200` and `{"status":"ok"}`. the check includes a database query.
open [swagger ui](http://localhost:8081/docs) to send requests without writing curl commands.
the api is published only on localhost. postgres has no published host port.
port 8081 avoids interfering with homework 1 on port 8080.

`make setup` creates an ignored `.env` with random database and jwt secrets.
it never overwrites an existing file. keep this file private.
change `APP_PORT` in `.env` if needed. do not change the database password after
initializing its volume unless you also change the password in postgres.

```bash
docker compose ps
docker compose logs migrate
docker compose logs -f api
docker compose down
```

`down` stops and removes containers but keeps the database volume.
the next `make up` uses the same data and flyway history.
the migration container runs once and exits successfully; this is expected.
the pinned flyway image is amd64, so docker desktop emulates that one container on apple silicon.
the python api and postgres use native images on both arm64 and amd64.

## accounts and authorization

public registration creates `USER` accounts only. it does not accept a role.
create a seller and an administrator with these local operator commands:

```bash
docker compose exec api python -m app.seed --email seller@example.com --role SELLER
docker compose exec api python -m app.seed --email admin@example.com --role ADMIN
```

each command asks for a password without echoing it. an existing account is left unchanged.
these commands require access to the running container and are not public api endpoints.
there are no built-in credentials in the application.

in swagger ui:

1. use `POST /auth/register` to create a buyer with an email and password.
2. use `POST /auth/login` to obtain an access token and a refresh token.
3. click authorize and enter the access token, without the `Bearer` prefix.
4. use the protected endpoints. log in as a different account to change roles.

all product reads and writes, order operations and promo creation require an access token.
`/auth/*`, `/health` and documentation are public.

access tokens last 20 minutes; refresh tokens last 14 days.
both are signed jwt tokens with issuer, audience, subject, role, type, expiry and unique id.
the api accepts only the configured signing algorithm. an access token cannot be used
as a refresh token or the other way around. current account roles are checked on access.

refresh rotates the token pair in a transaction. the previous refresh token can be used
only once, including concurrent calls. only a sha256 hash of the refresh token is stored.
passwords use argon2id. passwords, tokens and authorization fields are redacted from request logs.

| operation | user | seller | admin |
|---|---|---|---|
| read products | any | any | any |
| create or change products | no | own | any |
| create orders | own | no | own |
| read, update or cancel orders | own | no | any |
| advance order state | no | no | any |
| create promo codes | no | yes | yes |

## contract first

the source of truth is [openapi/marketplace.yaml](openapi/marketplace.yaml), not fastapi's inferred schema.
it describes request and response schemas, constraints, security, role rules, errors and endpoints.
the same document is returned by `/openapi.json` and displayed by swagger ui.
the yaml source is available at `/openapi.yaml`.

```bash
make generate
```

this runs the pinned openapi generator `python-fastapi` generator in docker and writes
models and routes to `generated/src/marketplace_api/`. that directory is ignored by git.
`make up` regenerates the same code during the image build; checking generated code into git is not required.

the generated routers call functions in `app/handlers.py`. handlers delegate to business services
and use generated request objects. generated response models validate outgoing responses.
there are no handwritten request or response dto classes. `app/models.py` contains database mappings,
not api dto classes. database migrations, not sqlalchemy `create_all`, create the tables.

the templates in `codegen/` adapt the upstream generator to this application:

- routes are synchronous so blocking database work runs in fastapi's thread pool;
- success status codes come from the contract's `x-success-status` extension;
- decimal fields become python `Decimal` rather than floating-point values;
- numeric query parameters accept http query strings and still enforce their limits;
- unknown model fields are rejected, and decimal response values are json numbers.

request bodies are also checked directly against the openapi schema before handlers run.
this covers constraints the generator may not implement, including nullable fields and array lengths.
decimal `multipleOf` validation uses decimal arithmetic. invalid json, fields, query parameters,
ids and enum values return `400 VALIDATION_ERROR`, never fastapi's default validation response.

## api

| method | path | result |
|---|---|---|
| post | `/products` | create a product, 201 |
| get | `/products/{id}` | read a product, 200 |
| get | `/products` | paginated list, 200 |
| put | `/products/{id}` | replace mutable product fields, 200 |
| delete | `/products/{id}` | archive without deleting the row, 204 |
| post | `/orders` | create and reserve stock, 201 |
| get | `/orders/{id}` | read an order with its items, 200 |
| put | `/orders/{id}` | replace items in a created order, 200 |
| post | `/orders/{id}/cancel` | cancel and release stock, 200 |
| post | `/orders/{id}/status` | admin advances the state, 200 |
| post | `/promo-codes` | create a promo code, 201 |
| post | `/auth/register` | create a buyer, 201 |
| post | `/auth/login` | issue tokens, 200 |
| post | `/auth/refresh` | rotate tokens, 200 |

product lists use `page=0` and `size=20` by default, with a maximum page size of 100.
the response includes `items`, `totalElements`, `page` and `size`.
filters combine with and: `status=ACTIVE&category=electronics`.
category matching is exact and case-sensitive. sorting by id keeps pagination stable.
archived products remain readable and appear unless filtered out; they cannot be ordered.

`ProductCreate`, `ProductUpdate` and `ProductResponse` are separate schemas.
`PUT` requires all mandatory mutable fields. omitting description clears it.
`seller_id` defaults to the current seller on creation and remains unchanged on update.
only an admin can assign a different existing seller or admin.

## transactions and concurrency

this homework uses one modular application with one database. catalog, orders, promos and
authentication remain separate modules, but a checkout can use one postgres transaction.
this differs deliberately from the target microservice architecture in homework 1:
the new task explicitly requires atomic stock changes and order creation in one transaction.

order creation performs the required checks in this order:

1. lock the user and check the last successful create operation.
2. reject an existing `CREATED` or `PAYMENT_PENDING` order.
3. check all requested products exist and are `ACTIVE`.
4. collect all stock shortages before changing any stock.
5. reduce available stock and capture prices.
6. calculate the subtotal, validate the promo and apply its discount.
7. save the order, items and successful operation.

all these changes commit together or roll back together. a promo error after reducing stock
still rolls the stock changes back. failed operations do not consume the rate limit.

updating an order restores its previous reservation, checks and reserves the replacement items,
recalculates totals and records the operation in a single transaction.
cancellation restores stock and promo usage exactly once; another cancellation returns a state error.

row locks serialize operations on the same user, order, product or promo.
locks are taken in the order users, order, products, promo. multiple users and products are locked
in sorted id order. this avoids reversed lock ordering between concurrent multi-item orders.
an admin updating another user's order locks both users in the same order.

postgres also has nonnegative stock and usage checks, foreign keys, unique promo codes and
a partial unique index allowing at most one active order per user.
the isolation level is postgres's default read committed; explicit row locks protect read-check-write operations.

the `products.status` index is created by flyway.
database defaults set timestamps, and triggers update `updated_at`.
money uses `Decimal` in python and `DECIMAL(12,2)` in postgres.
discounts are rounded to cents with half-up rounding, once per order.

## decisions for unspecified cases

- percentage discounts are capped at 70%, as agreed for this implementation.
  a fixed discount cannot exceed the subtotal.
- existing items keep their original `price_at_order` on order update.
  newly added products capture the current price. removing and later adding a product creates a new snapshot.
- duplicate product ids in one request are rejected before business logic; clients must combine quantities.
- updating an order with the same promo does not consume an additional use.
  its own reserved use is excluded when checking the usage limit.
- a subtotal below the promo minimum removes the promo and returns one use.
  an expired or disabled promo rejects the update and rolls everything back.
- admin order operations bypass ownership checks, but not state, stock, promo or rate-limit rules.
  rate limits apply to the acting account and separately to create and update.
- `ORDER_INTERVAL_MINUTES` defaults to 1. it accepts a nonnegative decimal; 0 disables the interval for demonstrations.
  changing this setting requires recreating the api container.
- the added admin status endpoint allows only the next state:
  `CREATED -> PAYMENT_PENDING -> PAID -> SHIPPED -> COMPLETED`.
  cancellation from `CREATED` or `PAYMENT_PENDING` uses the separate cancel endpoint.
- status advancement is a demonstration of the state machine, not a real payment integration.
  this assignment does not implement a 30-minute reservation expiry, notifications or actual payments.
- order subtotals above the database's decimal limit are rejected with a validation error.
- product stock updates must leave enough integer capacity to return all unpaid reservations.
- promo codes are global, because this assignment does not define seller-specific promo scope.

## errors and logs

all api errors use `error_code`, `message` and optional `details`.
field errors include `details.fields`; stock errors list product ids, requested quantities and available stock.

| status | codes |
|---|---|
| 400 | `VALIDATION_ERROR` |
| 401 | `TOKEN_INVALID`, `TOKEN_EXPIRED`, `REFRESH_TOKEN_INVALID`, `INVALID_CREDENTIALS` |
| 403 | `ACCESS_DENIED`, `ORDER_OWNERSHIP_VIOLATION` |
| 404 | `PRODUCT_NOT_FOUND`, `ORDER_NOT_FOUND`, `NOT_FOUND` |
| 405 | `METHOD_NOT_ALLOWED` |
| 409 | `PRODUCT_INACTIVE`, `INSUFFICIENT_STOCK`, `ORDER_HAS_ACTIVE`, `INVALID_STATE_TRANSITION`, `EMAIL_EXISTS`, `PROMO_CODE_EXISTS` |
| 422 | `PROMO_CODE_INVALID`, `PROMO_CODE_MIN_AMOUNT` |
| 429 | `ORDER_LIMIT_EXCEEDED` |
| 500 | `INTERNAL_ERROR` |
| 503 | `SERVICE_UNAVAILABLE` |

every api request produces a json log with `request_id`, `method`, `endpoint`, `status_code`,
`duration_ms`, `user_id` and an iso 8601 `timestamp`. write requests also include a redacted `request_body`.
malformed json is not logged as raw text. a new uuid is generated by middleware and returned as `X-Request-Id`.
internal failures do not return sql, secrets or stack traces to clients.

## tests

```bash
make test
```

this builds the contract and application from scratch when needed, starts an isolated postgres test database,
runs flyway, starts the real http server, and executes pytest against that server.
tests query the database directly to verify stock, snapshots, rollback, migrations and promo usage.
concurrent tests cover last stock, last promo use, competing orders for one user and repeated cancellation.
responses are checked against the openapi contract. json logs are checked for required fields and leaked passwords.

the test stack uses `compose.test.yml`, a different project name and a temporary database filesystem.
it publishes no ports and never resets the normal application database.
test containers are removed at the end. logs remain in ignored `work/`.
the github workflow runs lint and the same docker tests on pushes and pull requests.

for local python development after `make generate`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
ruff check app scripts tests
```

running the api outside docker additionally needs `PYTHONPATH=generated/src:.`, a valid `JWT_SECRET`,
and `DATABASE_URL` pointing to a migrated postgres database. docker is the recommended run path.

## defense

start with `make up`, show the compose logs and `/health`, then open swagger ui.
create seller and admin accounts using the commands above.
`make demo` asks for the seller credentials, creates a fresh buyer and product,
creates an order, shows reduced stock, cancels the order and shows restored stock.
the script never prints tokens or passwords. it creates demonstration records and does not delete existing records.

show actual database rows, not only http responses:

```bash
docker compose exec -T db psql -U marketplace -d marketplace < docs/defense.sql
```

## requirement map

| point | implementation |
|---|---|
| 1: product crud contract | `openapi/marketplace.yaml`, product routes |
| 2: schemas and constraints | separate create, update and response schemas, decimal and date-time formats |
| 3: code generation | `make generate`, docker codegen stage, ignored `generated/`, generated routes and dto |
| 4: postgres and crud | `db/migrations/`, flyway service, catalog module, status index and timestamp triggers |
| 5: errors | contract error schemas, `app/errors.py`, exception handlers |
| 6: validation | openapi constraints, generated models, `app/contract.py` |
| 7: orders | `app/services/orders.py`, transactions, locks and concurrency tests |
| 8: request logs | middleware in `app/main.py`, `scripts/check_logs.py` |
| 9: jwt | `app/security.py`, auth endpoints and refresh rotation |
| 10: roles | role and ownership checks, security descriptions and tests |

## references

- [openapi generator: python-fastapi](https://openapi-generator.tech/docs/generators/python-fastapi/)
- [fastapi: concurrency](https://fastapi.tiangolo.com/async/)
- [postgresql: row locks](https://www.postgresql.org/docs/17/explicit-locking.html)
- [flyway: migrations](https://documentation.red-gate.com/flyway/flyway-concepts/migrations)
