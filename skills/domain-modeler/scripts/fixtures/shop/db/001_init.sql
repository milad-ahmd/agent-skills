CREATE TABLE IF NOT EXISTS public."customers" (id bigint primary key);
CREATE TABLE orders (id bigint primary key, customer_id bigint references customers(id));
CREATE TYPE order_status AS ENUM ('pending', 'paid', 'canceled');
