# Sajilo Pasal (सजिलो पसल) — Project Brief for Claude

## What this project is
A Django-based QR Menu & Inventory Tracker for small Nepali businesses (kirana stores,
restaurants, tea shops, fruit stalls). Shop owners scan a QR code to manage their menu
and inventory. Customers scan the same QR code to view the menu and place orders.
Tagline: "Helping small Nepali shops go digital, easily."

---

## Developer context
- **Name:** Mandip (GitHub: `mandip-adk`, LinkedIn: `mandip-adhikari`)
- **Stack:** Django 6.x, PostgreSQL (Neon in production), SQLite (local dev)
- **Storage:** Cloudinary (production), local filesystem (dev, when DATABASE_URL is not set)
- **Hosting:** Render
- **Environment:** Windows, PowerShell, venv at `D:\QR_Menu_Inventory_Tracker\venv`
- **Python packages:** All in `requirements.txt` (key ones: django, psycopg2-binary,
  cloudinary, django-cloudinary-storage, pillow, qrcode[pil], python-decouple,
  python-dotenv, gunicorn, whitenoise)

---

## Project structure (flat apps at root, no `apps/` prefix)
```
QR_Menu_Inventory_Tracker/
├── config/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── accounts/          # Custom User model, OTP verification
├── shops/             # Shop model, public menu view
├── categories/        # Category model
├── products/          # Product model, inventory tracking
├── orders/            # Order and OrderItem models (Day 11)
├── qr_manager/        # QR code generation, short URL redirect
├── inventory/         # Inventory logs (Day 16+)
├── dashboard/         # Owner dashboard (Day 13+)
├── static/
│   ├── css/main.css
│   └── js/
│       ├── cart.js
│       └── image_compressor.js
├── templates/
│   ├── base/
│   │   ├── base.html
│   │   ├── navbar.html
│   │   └── footer.html
│   ├── accounts/
│   ├── shops/
│   ├── categories/
│   ├── products/
│   ├── orders/
│   ├── qr_manager/
│   └── dashboard/
├── manage.py
├── requirements.txt
└── .env
```

---

## CSS / branding
- Project name: **Sajilo Pasal** / **सजिलो पसल**
- CSS variables: `--sajilo-primary: #2d6a4f` (forest green), `--sajilo-accent: #f77f00`
- CSS classes: `.btn-sajilo`, `.navbar-sajilo`
- Bootstrap 5.3.2 + Bootstrap Icons 1.11.1 (CDN)
- Mobile-first, optimised for low-end Android / slow Ncell/NTC connections

---

## URL structure (config/urls.py)
```python
path("admin/",     admin.site.urls)
path("accounts/",  include("accounts.urls",      namespace="accounts"))
path("dashboard/", include("dashboard.urls",     namespace="dashboard"))
path("shops/",     include("shops.urls",         namespace="shops"))
path("shops/",     include("categories.urls",    namespace="categories"))
path("shops/",     include("products.urls",      namespace="products"))
path("shop/",      include("shops.menu_urls",    namespace="menu"))
path("s/",         include("qr_manager.urls",    namespace="qr_manager"))
path("orders/",    include("orders.urls",        namespace="orders"))
```

---

## Apps built (Days 1–11)

### `accounts` app
**Models:**
- `User` — custom, email-based (no username). Fields: `email`, `first_name`,
  `last_name`, `is_active` (False until OTP verified), `is_staff`, `is_verified`,
  `date_joined`. `AUTH_USER_MODEL = "accounts.User"`.
- `EmailVerificationOTP` — OTP hashed with SHA-256 (`otp_code_hash`, never plaintext).
  Fields: `user`, `otp_code_hash`, `expires_at`, `is_used`, `created_at`,
  `failed_attempts`, `locked_until`. Has `LOCKOUT_SCHEDULE` (escalating: 60s, 60s,
  5min, 10min, 30min, 1hr, 5hr, then permanent retire at 8 attempts).
  `create_for_user()` retires old OTPs atomically before creating new one.
  Uses `secrets.randbelow()` (not `random`) for code generation.
- `DailyOTPAttemptLimit` — per-user daily cap (20 attempts) across resends,
  closes the "resend resets counter" loophole.

**Key views:** `register_view`, `login_view`, `logout_view`, `verify_otp_view`,
`resend_otp_view`.

**Security decisions:**
- `session.cycle_key()` after every successful login (session fixation prevention)
- Rate limiting on resend is DB-based (not session-based) — incognito/other browser
  doesn't bypass it
- Lockout tracked on OTP row itself, not in session
- `authenticate()` silently rejects `is_active=False` users — `_check_unverified_credentials()`
  handles the case where correct password + unverified account would otherwise show
  "Invalid email or password" instead of routing to OTP

**Settings additions:**
```python
AUTH_USER_MODEL = "accounts.User"
OTP_EXPIRY_MINUTES = 5
if DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
```

---

### `shops` app
**Model: `Shop`**
Fields: `owner` (FK→User), `name`, `slug` (unique, auto-generated, locked on creation),
`shop_type` (TextChoices: KIRANA, RESTAURANT, FRUIT_VEG, TEA_CAFE, OTHER),
`logo` (ImageField, Cloudinary), `phone` (Nepal mobile: `^(98|97)\d{8}$`),
`address`, `description`, `is_active`, `created_at`, `updated_at`.

**Slug generation:** `_save_with_unique_slug()` — retry-on-IntegrityError loop
(up to 10 retries). Slug truncated to 160 chars (10 chars headroom before max_length=170).
Slug NEVER changes on rename (QR codes already printed may reference it).
Nepali/Devanagari names produce fallback slug `"shop"`, `"shop-2"`, etc.

**Image validation:** `validate_logo_image()` — size (5MB max), extension allowlist
(jpg/jpeg/png/webp), Pillow content-sniff (catches renamed TIFFs etc.).

**Public menu:** `shops/menu_views.py` → `public_menu_view(request, slug)` — no login
required, 404 for inactive shops, prefetch_related to avoid N+1.
`shops/menu_urls.py` — `path("<slug:slug>/", ..., name="detail")` → `app_name = "menu"`.

**`get_menu_url()` method** (NOT a @property) — uses `reverse("menu:detail", kwargs={"slug": self.slug})`.
Previously was `menu_url_path` (hardcoded string) — renamed on Day 9 when the URL existed.

**QR system:** `/s/<shop_id>/` short redirect (no login needed) → redirects to
`shop.get_menu_url()`. QR encodes the short URL (numeric PK, ~24 chars, alphanumeric
mode) not the slug URL (~45 chars). QR PNG and SVG served on-the-fly (not stored).

---

### `categories` app
**Model: `Category`**
Fields: `shop` (FK), `name`, `display_order` (PositiveIntegerField, default=0,
**reserved but unused** — ordering is still by `created_at`), `created_at`, `updated_at`.

**Uniqueness:** `UniqueConstraint(shop, name)` at DB level (case-sensitive) +
`clean()` check with `name__iexact` (case-insensitive, form-layer). Same name
allowed across different shops.

**Two-hop ownership check in views:** `get_object_or_404(Category, pk=id, shop__slug=slug, shop__owner=request.user)` — one joined query, not two separate checks.

---

### `products` app
**Model: `Product`**
Fields: `category` (FK, CASCADE), `name`, `description`, `price` (DecimalField,
max_digits=10, decimal_places=2), `image` (ImageField, Cloudinary),
`stock_quantity` (IntegerField — signed, allows negative for allow_over_order),
`allow_over_order` (BooleanField), `is_available` (BooleanField), `created_at`, `updated_at`.

**Orderability truth table (single source of truth: `is_orderable` property):**
```
stock | allow_over_order | is_available | can_order?
  0   |      False       |     True     |    No
  0   |      True        |     True     |    Yes
  3   |      False       |     True     |    Yes
 -2   |      True        |     True     |    Yes
 any  |      any         |     False    |    No   ← owner manual override
```

**`is_low_stock` property:** True when `0 < stock_quantity <= 5` AND `allow_over_order=False`.

**`adjust_stock(delta)` method:** Uses `F()` expression + `select_for_update()` inside
`transaction.atomic()`. Deliberately does NOT enforce business rules (is_orderable check
is caller's responsibility — order placement, Day 12).

**`clean()` validates:**
1. Price cannot be negative
2. `stock_quantity < 0` requires `allow_over_order=True`
3. Per-category name uniqueness (case-insensitive)

**`UniqueConstraint(category, name)`** at DB level.

**Views call `form.instance.full_clean()`** explicitly so model-level validation
surfaces as form errors (not 500s).

**Three-hop ownership:** `get_object_or_404(Product, pk=id, category_id=cat_id, category__shop__slug=slug, category__shop__owner=request.user)`

---

### `qr_manager` app
**Views:**
- `short_redirect_view(request, shop_id)` — no login, redirects to `shop.get_menu_url()`, 404 if inactive
- `qr_detail_view(request, shop_slug)` — owner only, HTML page with download buttons
- `qr_png_view(request, shop_slug)` — owner only, returns `image/png`
- `qr_svg_view(request, shop_slug)` — owner only, returns `image/svg+xml`, forced download
- `qr_download_png_view(request, shop_slug)` — owner only, forced download

**URL config in `qr_manager/urls.py`** (mounted at `"s/"` in config/urls.py):
```python
path("<int:shop_id>/",                views.short_redirect_view,    name="redirect")
path("qr/<slug:shop_slug>/",          views.qr_detail_view,         name="detail")
path("qr/<slug:shop_slug>/png/",      views.qr_png_view,            name="png")
path("qr/<slug:shop_slug>/svg/",      views.qr_svg_view,            name="svg")
path("qr/<slug:shop_slug>/download/png/", views.qr_download_png_view, name="download_png")
```

**QR settings:** `ERROR_CORRECT_M`, `box_size=10`, `border=4`, `SvgPathImage` factory.

---

### `orders` app (Day 11 — models + admin + url stub only)
**Models:**

`OrderStatus(TextChoices)`: PENDING, PREPARING, READY, CANCELLED
`PaymentStatus(TextChoices)`: UNPAID, PAID
`PaymentMethod(TextChoices)`: CASH, FONEPAY (eSewa/Khalti future)

**`Order`** fields: `shop` (FK), `order_number` (PositiveIntegerField, sequential per shop),
`order_token` (UUID, unique — duplicate submission prevention), `table_number` (CharField,
free text e.g. "Table 3", "Counter", "Takeaway"), `customer_note`, `status`,
`payment_status`, `payment_method`, `subtotal` (DecimalField, stored at order time).
`UniqueConstraint(shop, order_number)`.

**`OrderItem`** fields: `order` (FK CASCADE), `product` (FK SET_NULL — survives product deletion),
`product_name` (snapshot), `unit_price` (snapshot), `quantity`, `line_total`.

**`Order.get_next_order_number(shop)`** — uses `select_for_update()` on most recent order
to prevent race condition (two simultaneous orders both reading same max → same number).
Must be called inside `transaction.atomic()`.

**`Order.create_from_cart(shop, cart_items, ...)`** — atomic, creates Order + all
OrderItems, computes subtotal. Does NOT decrement stock (that's Day 12 on status change).

**`orders/urls.py`** has a stub `_place_order_stub` at `orders:place` so
`{% url 'orders:place' shop.slug %}` in `public_menu.html` resolves without NoReverseMatch.
Day 12 replaces this stub with the real placement view.

---

## Cart (Day 10 — pure JavaScript)
`static/js/cart.js` — localStorage-based, scoped per shop (`sajilo_cart_<shop_id>`).
**Public API via `window.SajiloCart`:**
- `SajiloCart.init(shopId)` — must be called on menu page load
- `SajiloCart.add(productId, name, price)`
- `SajiloCart.remove(productId)`
- `SajiloCart.updateQuantity(productId, delta)`
- `SajiloCart.clear()`
- `SajiloCart.openDrawer()` / `SajiloCart.closeDrawer()`

Cart UI: sticky bottom bar (hidden when empty), slide-in drawer from bottom.
Place Order button in drawer links to `{% url 'orders:place' shop.slug %}`.

---

## Cloudinary configuration (Day 7)
```python
# In settings.py — conditional storage:
DATABASE_URL = config("DATABASE_URL", default=None)
if DATABASE_URL:
    # Cloudinary
    cloudinary.config(cloud_name=..., api_key=..., api_secret=..., secure=True)
    DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"
else:
    # Local dev
    MEDIA_URL = "/media/"
    MEDIA_ROOT = BASE_DIR / "media"
```

---

## Client-side image compression (Day 7)
`static/js/image_compressor.js` — Compressor.js (CDN) compresses images to max 1200×1200,
75% JPEG quality before form submission. Only loaded on pages with file upload inputs
(shop_form.html, product_form.html). Falls back to original file on error.
Call: `initImageCompressor(inputId, previewId, statusId)`.

---

## Key conventions to follow
1. **Commit splitting:** model+migration → forms → views+urls → admin → templates → tests
2. **Tests go in each app's own `tests.py`** (appended, not separate files)
3. **Ownership checks:** always single joined queryset (never two separate checks)
4. **404 not 403** for cross-owner access (don't confirm object existence)
5. **`full_clean()` in views** (create + edit) so model validators surface as form errors
6. **`transaction.atomic()` + `select_for_update()`** for any race-sensitive writes
7. **SQLite concurrency tests** use `REQUIRES_ROW_LOCKING` skip decorator
8. **`secrets.randbelow()`** not `random.randint()` for auth-related codes
9. **OTP stored as SHA-256 hash** (`otp_code_hash`), never plaintext
10. **`TextChoices`** for all enum-style fields (not raw string lists)
11. **No `reverse()` in models until the URL pattern exists** — use hardcoded string with
    a comment saying which day to switch
12. **Nepali bilingual labels** — `get_shop_type_display_nepali()` on Shop model

---

## SDD-mandated future features (not yet built)
- Day 12: Order placement view (POST from cart, stock decrement on Preparing)
- Day 13: Owner order dashboard
- Day 14: Order status updates
- Day 15: Payment status + payment method tracking
- Day 16: Inventory logs (InventoryLog model)
- Day 17: Low stock notifications
- Day 18: Availability toggle (already exists on product, Day 18 adds UI)
- Day 19: allow_over_order functionality (already exists on product, Day 19 adds UI)
- Day 20: HTMX polling for new orders (owner dashboard auto-refresh)

---

## Test suite status (as of Day 11)
```bash
python manage.py test accounts -v 2    # 39 tests, all pass
python manage.py test shops -v 2       # 75 tests (should all pass after Day 11 fixes)
python manage.py test categories -v 2  # 23 tests, all pass
python manage.py test products -v 2    # 55 tests, 1 skipped (SQLite concurrency)
python manage.py test orders -v 2      # Day 11 tests
python manage.py test qr_manager -v 2  # Day 8 tests
```

---

## How to continue (for a new Claude session)

Read this file fully before writing any code. Then:

1. Ask which day we're on if not told
2. Follow the commit-splitting convention above
3. Match the flat app structure (no `apps.` prefix)
4. Use `python-decouple`'s `config()` for env vars (not `os.environ.get`)
5. All test classes append to the relevant app's `tests.py`
6. Helper functions (`make_verified_user`, `make_shop`, etc.) are already defined
   at the top of each app's `tests.py` — don't redefine them, just use them
7. Run tests before presenting files, fix any failures before presenting
8. Present files using the present_files tool so they're downloadable
9. Give commit messages at the end of each day in the multi-commit format
