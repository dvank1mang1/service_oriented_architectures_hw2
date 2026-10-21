# defense checklist

## twenty-minute walkthrough

1. show `openapi/marketplace.yaml`: paths, separate product schemas, validation, errors and bearer security.
2. show `Dockerfile`: code generation is a build step, not a manually maintained copy.
3. run `make up`, show startup and migration logs, then request `/health`.
4. open `/docs`, log in as a seller, create a product and list it with filters and pagination.
5. log in as a buyer, create an order and show its captured price and reduced stock.
6. run the select queries in `docs/defense.sql` to prove that rows were saved.
7. change the catalog price as seller; read the order again and show that its snapshot did not change.
8. update the order, cancel it and show stock returning in the database.
9. demonstrate errors and explain transaction boundaries and locks.

## alternative scenarios

- missing required product field, negative stock, zero price, overlong name: 400 with field details.
- invalid uuid, unknown product id: 400 and 404 respectively.
- inactive or archived product in an order: 409.
- several products with insufficient stock: 409 with all shortages; no partial reservation.
- unknown, expired, inactive or exhausted promo: 422; stock and order rows remain unchanged.
- percentage promo of 90%: the discount is capped at 70%.
- fixed discount larger than the subtotal: final amount is zero, never negative.
- same user creates another order immediately: 429, because the frequency check comes first.
- same user creates another order after the interval while the first remains active: 409.
- update an order so its subtotal falls below the promo minimum: promo removed, use count decremented.
- invalid replacement items: previous items, totals, promo and reserved stock remain unchanged.
- cancellation twice: second request gets 409; stock and promo use are restored only once.
- read or edit another user's order: 403; an admin can operate on it.
- seller attempts to create an order or buyer attempts to create a product: 403.
- expired access token or reused refresh token: 401 with the specified code.
- advance directly from CREATED to PAID: 409; go through PAYMENT_PENDING first.
- after PAID, editing and cancellation are rejected.

use fresh users for independent create scenarios or wait for the configured operation interval.
the automated tests move timestamps only in their isolated test database; the production api has no bypass endpoint.

## where to explain the code

`app/handlers.py` starts and commits or rolls back transactions with `Session.begin()`.
`app/services/orders.py` contains the checkout sequence and uses `with_for_update()`.
`app/services/promos.py` calculates discounts with `Decimal` and half-up cent rounding.
`app/security.py` verifies jwt claims and atomically rotates refresh sessions.
`app/main.py` logs requests, assigns request ids and converts exceptions into contract errors.
`db/migrations/V1__marketplace.sql` defines database constraints and timestamp triggers.

## short theory answers

**why contract first?** the api document is written before implementation. generated routes and
models make code depend on the contract rather than merely describing the code afterwards.

**why are database models handwritten?** they describe storage. the rule against handwritten dto
applies to api request and response models, all of which are generated here.

**what is a transaction?** a group of changes that commits as one unit or is rolled back.
our order, items, stock, promo counter and operation log are one such unit.

**why are transactions alone insufficient?** two transactions can both read the same stock before
either changes it. row locks serialize the check and update for the same products.

**why sorted locks?** two multi-product orders lock products in the same order, avoiding a cycle
where each holds one product and waits for the other.

**why a partial unique index?** it enforces one active order per user in the database as a second
line of defense, while allowing any number of completed or canceled orders.

**why snapshot prices?** a later catalog change must not alter the amount already agreed for an order.

**why not float for money?** decimal arithmetic avoids binary floating-point rounding errors.
json numbers are converted to decimal values, and the database stores fixed precision amounts.

**what is soft deletion?** the product status becomes ARCHIVED but its row remains, preserving
order references and history. repeated deletion is harmless.

**what is flyway?** it applies versioned migrations once, records their checksums and execution history,
and detects changes to migrations already applied. the api starts only after migrations succeed.

**jwt versus encryption?** our jwt is signed, not encrypted. its role and subject are readable;
the signature prevents undetected changes. passwords never go into tokens.

**401 versus 403?** 401 means credentials are absent or invalid. 403 means an authenticated account
does not have the required role or ownership.

**why refresh tokens?** access tokens expire quickly. a longer-lived refresh token can issue a new
pair without resending the password. rotation makes an already-used refresh token invalid.

**why one service here?** the assignment requires a local transaction across stock and orders.
modules keep responsibilities clear without introducing distributed transactions.

**what happens after thirty unpaid minutes?** this homework does not define or implement automatic
expiry. a CREATED or PAYMENT_PENDING order can be canceled explicitly, which releases its stock.
automatic expiry would require a persisted deadline and a worker using the same safe cancellation logic.
