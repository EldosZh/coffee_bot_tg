"""Schema version 2. Money is stored as integer tenge."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 user_id INTEGER PRIMARY KEY, name TEXT NOT NULL, username TEXT,
 phone TEXT, bonus_balance INTEGER NOT NULL DEFAULT 0 CHECK(bonus_balance>=0),
 bonus_earned INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, drink INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL,
 category_id INTEGER NOT NULL REFERENCES categories(id), price INTEGER NOT NULL CHECK(price>0),
 active INTEGER NOT NULL DEFAULT 1, in_stock INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS promo_codes (
 code TEXT PRIMARY KEY, percent INTEGER NOT NULL CHECK(percent BETWEEN 1 AND 100),
 active INTEGER NOT NULL DEFAULT 1, expires TEXT, max_uses INTEGER,
 per_user INTEGER NOT NULL DEFAULT 1 CHECK(per_user>0)
);
CREATE TABLE IF NOT EXISTS carts (
 user_id INTEGER PRIMARY KEY REFERENCES users(user_id), promo_code TEXT REFERENCES promo_codes(code)
);
CREATE TABLE IF NOT EXISTS cart_items (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES carts(user_id),
 product_id INTEGER NOT NULL REFERENCES products(id), options TEXT NOT NULL,
 qty INTEGER NOT NULL CHECK(qty BETWEEN 1 AND 99), unit_price INTEGER NOT NULL,
 UNIQUE(user_id,product_id,options)
);
CREATE TABLE IF NOT EXISTS orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(user_id),
 subtotal INTEGER NOT NULL, discount INTEGER NOT NULL DEFAULT 0,
 bonus_used INTEGER NOT NULL DEFAULT 0, bonus_awarded INTEGER NOT NULL DEFAULT 0,
 total INTEGER NOT NULL, method TEXT NOT NULL, address TEXT NOT NULL,
 phone TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '', payment TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'new', created TEXT NOT NULL,
 checkout_key TEXT UNIQUE, legacy INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS order_items (
 id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
 product_id INTEGER REFERENCES products(id), name TEXT NOT NULL,
 options TEXT NOT NULL, qty INTEGER NOT NULL, unit_price INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS promo_usages (
 id INTEGER PRIMARY KEY, code TEXT NOT NULL REFERENCES promo_codes(code),
 user_id INTEGER NOT NULL REFERENCES users(user_id), order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id)
);
CREATE TABLE IF NOT EXISTS reviews (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(user_id),
 rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), comment TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, text TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS orders_user ON orders(user_id,id);
CREATE INDEX IF NOT EXISTS promo_usage_code ON promo_usages(code,user_id);
"""

STATUSES = {
    'new': '🆕 Новый', 'preparing': '👨‍🍳 Готовится', 'ready': '☕ Готов',
    'delivery': '🚗 В пути', 'completed': '✅ Завершён', 'cancelled': '❌ Отменён',
}
TRANSITIONS = {
    'new': ('preparing', 'cancelled'), 'preparing': ('ready', 'cancelled'),
    'ready': ('delivery', 'completed', 'cancelled'), 'delivery': ('completed', 'cancelled'),
    'completed': (), 'cancelled': (),
}
