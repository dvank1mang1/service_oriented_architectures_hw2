SELECT installed_rank, version, description, success FROM flyway_schema_history ORDER BY installed_rank;

SELECT id, email, role FROM users ORDER BY email;

SELECT id, name, price, stock, status, seller_id, created_at, updated_at FROM products ORDER BY created_at;

SELECT id, user_id, status, promo_code_id, total_amount, discount_amount, created_at, updated_at
FROM orders ORDER BY created_at;

SELECT order_id, product_id, quantity, price_at_order FROM order_items ORDER BY order_id, product_id;

SELECT code, discount_type, discount_value, current_uses, max_uses, min_order_amount, active
FROM promo_codes ORDER BY code;

SELECT user_id, operation_type, created_at FROM user_operations ORDER BY created_at;

SELECT user_id, expires_at, revoked FROM refresh_sessions ORDER BY created_at;

SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'products';
