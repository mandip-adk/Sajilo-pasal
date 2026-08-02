# Sajilo Pasal (सजिलो पसल) — Complete Project Brief for Claude
# Days 1–30 | Last updated: Day 20 complete

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
- **Env vars:** Uses `python-decouple`'s `config()` — NOT `os.environ.get`
- **Python packages:** django, psycopg2-binary, cloudinary, django-cloudinary-storage,
  pillow, qrcode[pil], python-decouple, python-dotenv, gunicorn, whitenoise

---

## Project structure (flat apps at root — NO `apps/` prefix anywhere)
```
QR_Menu_Inventory_Tracker/
├── config/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── accounts/
├── shops/
├── categories/
├── products/
├── orders/
├── qr_manager/
├── inventory/
├── dashboard/
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

## COMPLETED: Days 1–20

### `accounts` app (Days 2–3)
**Models:**
- `User` — custom, email-based (no username). `is_active=False` until OTP verified.
  Fields: `email`, `first_name`, `last_name`, `is_active`, `is_staff`, `is_verified`,
  `date_joined`. `AUTH_USER_MODEL = "accounts.User"`.
- `EmailVerificationOTP` — OTP stored as SHA-256 hash (`otp_code_hash`, 64-char hex,
  never plaintext). `check_code(raw_code)` uses `hmac.compare_digest` (constant-time).
  `create_for_user()` retires old OTPs atomically, returns `(otp, raw_code)` tuple.
  Uses `secrets.randbelow(1_000_000)` for code generation.
  Fields: `user`, `otp_code_hash`, `expires_at`, `is_used`, `created_at`,
  `failed_attempts`, `locked_until`.
  `LOCKOUT_SCHEDULE = {1:60, 2:60, 3:300, 4:600, 5:1800, 6:3600, 7:18000}`.
  `MAX_ATTEMPTS_BEFORE_RETIRE = 8` — retires OTP as `is_used=True`, no separate lock flag.
- `DailyOTPAttemptLimit` — `MAX_ATTEMPTS_PER_DAY = 20`, resets daily. Closes
  "resend resets counter" loophole.
- `otp_format_validator` — module-level `RegexValidator(r"^\d{6}$")`.

**Key views:** `register_view`, `login_view`, `logout_view`, `verify_otp_view`,
`resend_otp_view`. `_check_unverified_credentials(email, password)` handles the case
where `authenticate()` silently rejects `is_active=False` users — without this,
correct password + unverified account shows "Invalid email or password" instead of
routing to OTP.

**Security decisions:**
- `session.cycle_key()` after every successful login (session fixation prevention)
- Rate limiting on resend is DB-based (not session-based) — incognito doesn't bypass it
- Lockout tracked on OTP row itself, not in session
- `pending_user_id` (PK) stored in session, not `pending_email`

**Settings additions:**
```python
AUTH_USER_MODEL = "accounts.User"
OTP_EXPIRY_MINUTES = 5
if DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
```

---

### `shops` app (Days 4, 7, 9)
**Model: `Shop`**
Fields: `owner` (FK→User), `name`, `slug` (unique, auto-generated, locked on creation),
`shop_type` (`ShopType(TextChoices)`: KIRANA, RESTAURANT, FRUIT_VEG, TEA_CAFE, OTHER),
`logo` (ImageField, Cloudinary/local), `phone` (Nepal mobile: `^(98|97)\d{8}$`),
`address`, `description`, `is_active`, `created_at`, `updated_at`.

**Slug:** `_save_with_unique_slug()` — retry-on-IntegrityError loop (10 retries),
base truncated to 160 chars (10 chars headroom before max_length=170).
Slug NEVER changes on rename. Nepali names fall back to `"shop"`, `"shop-2"`, etc.

**Logo validation:** `validate_logo_image()` — 5MB max, extension allowlist
(jpg/jpeg/png/webp), Pillow content-sniff (catches renamed TIFFs).

**`get_menu_url()` method** (NOT a @property) — `reverse("menu:detail", kwargs={"slug": self.slug})`.

**`get_shop_type_display_nepali()`** — returns Nepali label for bilingual UI.

**Public menu:** `shops/menu_views.py` → `public_menu_view(request, slug)` — no login,
404 for inactive shops, `prefetch_related("products")` to avoid N+1.
`shops/menu_urls.py` — `path("<slug:slug>/", ..., name="detail")`, `app_name = "menu"`.

---

### `categories` app (Day 5)
**Model: `Category`**
Fields: `shop` (FK CASCADE), `name`, `display_order` (PositiveIntegerField, default=0,
**reserved but UNUSED** — ordering still by `created_at`), `created_at`, `updated_at`.

**Uniqueness:** `UniqueConstraint(shop, name)` at DB level + `clean()` with
`name__iexact` (case-insensitive form-layer check). Same name allowed across shops.

**Two-hop ownership:**
`get_object_or_404(Category, pk=id, shop__slug=slug, shop__owner=request.user)`

---

### `products` app (Day 6)
**Model: `Product`**
Fields: `category` (FK CASCADE), `name`, `description`, `price` (DecimalField 10,2),
`image` (ImageField Cloudinary), `stock_quantity` (IntegerField — **signed**, allows
negative for allow_over_order), `allow_over_order` (BooleanField), `is_available`
(BooleanField), `created_at`, `updated_at`.

**Orderability truth table (`is_orderable` property — single source of truth):**
```
stock | allow_over_order | is_available | can_order?
  0   |      False       |     True     |    No
  0   |      True        |     True     |    Yes
  3   |      False       |     True     |    Yes
 -2   |      True        |     True     |    Yes
 any  |      any         |     False    |    No   ← owner manual override
```

**`is_low_stock` property:** `True` when `0 < stock_quantity <= 5` AND `allow_over_order=False`.
Also mirrored as `LOW_STOCK_FILTER` dict in `dashboard/views.py` for queryset use.

**`adjust_stock(delta)`:** `F()` expression + `select_for_update()` inside
`transaction.atomic()`. Returns refreshed `stock_quantity`. Does NOT enforce business
rules — caller's job.

**`clean()` validates:** price ≥ 0; `stock_quantity < 0` requires `allow_over_order=True`;
per-category name uniqueness (case-insensitive).

**`UniqueConstraint(category, name)`** at DB level.

**Views call `form.instance.full_clean()`** so model validators surface as form errors.

**Three-hop ownership:**
`get_object_or_404(Product, pk=id, category_id=cat_id, category__shop__slug=slug, category__shop__owner=request.user)`

**`validate_product_image()`** — same two-layer validation as logo (5MB, extension,
Pillow content-sniff).

---

### `inventory` app (Day 16)
**Model: `InventoryLog`** — append-only audit trail of every stock change.

```python
class InventoryLogReason(models.TextChoices):
    INITIAL_STOCK = "initial_stock", "Initial Stock"
    SALE          = "sale",         "Sale"
    CANCELLATION  = "cancellation", "Cancelled (Restored)"
    MANUAL        = "manual",       "Manual Adjustment"
```

Fields: `product` (FK→Product, SET_NULL), `product_name` (snapshot),
`shop` (FK→Shop CASCADE), `reason`, `delta` (IntegerField signed),
`resulting_stock` (IntegerField snapshot after change), `note`, `created_by` (FK→User
SET_NULL), `created_at`.

**`InventoryLog.record(product, delta, reason, actor=None, note="")`** — sole entry
point for audited stock changes. Calls `product.adjust_stock(delta)` (inheriting its
race-safety), then writes the log row. NOT wrapped in its own `transaction.atomic()` —
callers provide their own (see `Order._adjust_stock_for_items()`).

`inventory/views.py` is currently empty.

---

### `orders` app (Days 11–15)

**TextChoices:**
```python
class OrderStatus(models.TextChoices):
    PENDING   = "pending",   "Pending"
    PREPARING = "preparing", "Preparing"
    READY     = "ready",     "Ready"
    CANCELLED = "cancelled", "Cancelled"

class PaymentStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PAID   = "paid",   "Paid"

class PaymentMethod(models.TextChoices):
    CASH    = "cash",    "Cash"
    FONEPAY = "fonepay", "Fonepay QR"
    # eSewa/Khalti reserved for future enhancement
```

**Custom exceptions:** `OrderTransitionError`, `OrderPaymentError`.

**State machine (`ALLOWED_TRANSITIONS`):**
```python
ALLOWED_TRANSITIONS = {
    OrderStatus.PENDING:   {OrderStatus.PREPARING, OrderStatus.CANCELLED},
    OrderStatus.PREPARING: {OrderStatus.READY, OrderStatus.CANCELLED},
    OrderStatus.READY:     set(),      # terminal
    OrderStatus.CANCELLED: set(),      # terminal
}
```

**`Order` fields:** `shop` (FK CASCADE), `order_number` (PositiveIntegerField, sequential
per shop), `order_token` (UUID unique), `table_number` (CharField free text),
`customer_note`, `status`, `payment_status`, `payment_method`, `subtotal` (DecimalField
stored at order time), `created_at`, `updated_at`.
`UniqueConstraint(shop, order_number)`. `order_number_display` → `"#001"`.

**`Order.get_next_order_number(shop)`** — `select_for_update()` on most recent order.
Must be called inside `transaction.atomic()`.

**`Order.create_from_cart(shop, cart_items, ...)`** — atomic, creates Order + all
OrderItems, computes subtotal. Does NOT decrement stock.

**`Order.transition_status(new_status, actor=None)`** — validates against
`ALLOWED_TRANSITIONS`, raises `OrderTransitionError` if invalid.
Stock effects:
- `PENDING → PREPARING`: decrements stock via `_adjust_stock_for_items(sign=-1, reason=SALE)`
- `PREPARING → CANCELLED`: restores stock via `_adjust_stock_for_items(sign=+1, reason=CANCELLATION)`
Wrapped in `select_for_update()` on the order row.

**`Order._adjust_stock_for_items(sign, reason, actor=None)`** — iterates line items,
skips where `product_id is None`, calls `InventoryLog.record(...)`.

**`Order.update_payment(payment_status=None, payment_method=None)`** — both args
optional. Blocks `payment_status` changes on cancelled orders (allows `payment_method`
corrections). Wrapped in `select_for_update()`. Raises `OrderPaymentError`.

**`OrderItem` fields:** `order` (FK CASCADE), `product` (FK→Product SET_NULL),
`product_name` (snapshot), `unit_price` (snapshot), `quantity`, `line_total`.

**`orders/views.py` — `place_order_view(request, shop_slug)`** (Day 12):
- `@require_POST @csrf_protect`, accepts JSON POST from `cart.js` fetch()
- Idempotent on `order_token` — returns existing order if token already used
- Re-reads price/name from DB (never trusts client-side prices)
- Validates `is_orderable` and quantity vs stock for each item
- Returns `JsonResponse {"success": bool, "duplicate": bool, "order": {...}}`
- Error codes: `bad_json`, `cart_empty`, `bad_item`, `bad_quantity`,
  `invalid_payment_method`, `items_unavailable`, `order_conflict`
- `MAX_TABLE_NUMBER_LENGTH=50`, `MAX_NOTE_LENGTH=500`, `MAX_ITEM_QUANTITY=99`

**`orders/urls.py`:**
```python
path("<slug:shop_slug>/place/", views.place_order_view, name="place")
```

---

### `qr_manager` app (Day 8)
**Views (all mounted at `"s/"`):**
- `short_redirect_view(request, shop_id)` — no login, → `shop.get_menu_url()`, 404 if inactive
- `qr_detail_view(request, shop_slug)` — owner only, HTML with download buttons
- `qr_png_view(request, shop_slug)` — owner only, `image/png` inline, 24h cache
- `qr_svg_view(request, shop_slug)` — owner only, `image/svg+xml` forced download
- `qr_download_png_view(request, shop_slug)` — owner only, forced download

**URL patterns:**
```python
path("<int:shop_id>/",                views.short_redirect_view, name="redirect")
path("qr/<slug:shop_slug>/",          views.qr_detail_view,      name="detail")
path("qr/<slug:shop_slug>/png/",      views.qr_png_view,         name="png")
path("qr/<slug:shop_slug>/svg/",      views.qr_svg_view,         name="svg")
path("qr/<slug:shop_slug>/download/png/", views.qr_download_png_view, name="download_png")
```

QR settings: `ERROR_CORRECT_M`, `box_size=10`, `border=4`, `SvgPathImage` factory.
Encodes `/s/<shop_pk>/` (numeric, ~24 chars, alphanumeric mode — small dense QR).

---

### `dashboard` app (Days 13–20)
**No models** — purely views/urls/templates over Shop, Order, Product.

**`LOW_STOCK_FILTER` dict** (module-level, mirrors `Product.is_low_stock`):
```python
LOW_STOCK_FILTER = dict(allow_over_order=False, stock_quantity__gt=0, stock_quantity__lte=5)
```

**`NEXT_STATUS_ACTIONS` dict** — maps current status → `[(new_status, label, css_class)]`.
UI only; real validation in `Order.transition_status()`.

**Views:**
- `dashboard_home_view` — shop switcher, annotates each shop with `pending_count` and
  `low_stock_count` in two queries (not N per shop via annotate+values_list)
- `shop_orders_view(shop_slug)` — filterable by `?status=`, paginated (20/page),
  `status_tabs` with counts, `low_stock_count` banner, `latest_seen_id` for HTMX baseline
- `order_detail_view(shop_slug, order_id)` — three-hop ownership, passes `next_actions`
- `update_order_status_view(shop_slug, order_id)` — `@require_POST`, calls
  `order.transition_status(new_status, actor=request.user)`, catches `OrderTransitionError`
- `update_order_payment_view(shop_slug, order_id)` — `@require_POST`, calls
  `order.update_payment(...)`, catches `OrderPaymentError`
- `low_stock_view(shop_slug)` — uses `LOW_STOCK_FILTER`, ordered by `stock_quantity` asc
- `new_orders_check_view(shop_slug)` — **Day 20 HTMX polling**, reads `?since=<order_pk>`
  and `?status=`, returns COUNT-only HTML partial (`dashboard/_new_orders_banner.html`),
  cheap indexed `id__gt=since_id` query. Polled every 10-15s from `shop_orders.html`.

**`dashboard/urls.py`:**
```python
path("",                                                  views.dashboard_home_view,       name="home")
path("<slug:shop_slug>/",                                 views.shop_orders_view,          name="shop_orders")
path("<slug:shop_slug>/orders/<int:order_id>/",           views.order_detail_view,         name="order_detail")
path("<slug:shop_slug>/orders/<int:order_id>/status/",    views.update_order_status_view,  name="update_order_status")
path("<slug:shop_slug>/orders/<int:order_id>/payment/",   views.update_order_payment_view, name="update_order_payment")
path("<slug:shop_slug>/low-stock/",                       views.low_stock_view,            name="low_stock")
path("<slug:shop_slug>/orders/new-check/",                views.new_orders_check_view,     name="new_orders_check")
```

---

### Cart (Day 10 — pure JavaScript)
`static/js/cart.js` — localStorage, scoped per shop (`sajilo_cart_<shop_id>`).

**`window.SajiloCart` public API:**
`init(shopId)`, `add(productId, name, price)`, `remove(productId)`,
`updateQuantity(productId, delta)`, `clear()`, `openDrawer()`, `closeDrawer()`,
`get()`, `totalItems()`, `totalPrice()`.

Storage format: `{"<product_id>": {"id", "name", "price": float, "quantity": int}}`

Cart UI: sticky bottom bar (hidden when empty) + slide-in drawer from bottom.
Place Order button sends JSON `fetch()` POST to `{% url 'orders:place' shop.slug %}`.
`_escapeHtml()` prevents XSS when injecting product names into innerHTML.
Falls back to in-memory if localStorage unavailable.

---

### Cloudinary config (Day 7)
```python
DATABASE_URL = config("DATABASE_URL", default=None)
if DATABASE_URL:
    cloudinary.config(cloud_name=..., api_key=..., api_secret=..., secure=True)
    DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"
else:
    MEDIA_URL = "/media/"
    MEDIA_ROOT = BASE_DIR / "media"
```

### Client-side image compression (Day 7)
`static/js/image_compressor.js` — Compressor.js CDN, max 1200×1200, 75% JPEG quality.
Only loaded on pages with file upload inputs. `initImageCompressor(inputId, previewId, statusId)`.

---

## TODO: Days 21–30 (not yet built)

### Day 21 — Audio notifications for new orders
**Goal:** Play an HTML5 audio alert when new orders arrive on the owner's dashboard.
The HTMX polling from Day 20 already hits `new_orders_check_view` every 10-15s and
swaps in `dashboard/_new_orders_banner.html`. Day 21 adds audio to that banner.

**What to build:**
- An audio file (short notification beep) in `static/audio/`
- In `dashboard/_new_orders_banner.html`, when `new_count > 0`, include JS that plays
  the audio via `new Audio('/static/audio/notify.mp3').play()`. Must handle browser
  autoplay policy (audio must be triggered by prior user interaction — note this
  constraint and handle gracefully if it fails)
- A mute toggle the owner can use (store preference in localStorage)
- Commit: `"Add audio notifications for new orders"`

---

### Day 22 — Bilingual UI labels
**Goal:** Add Nepali translations alongside English labels throughout the customer-facing
menu and owner dashboard. The SDD specifies bilingual support (English/Nepali).

**What to build:**
- Template tags or context processors that supply Nepali labels for common terms:
  Price/मूल्य, Total/जम्मा, Place Order/अर्डर गर्नुहोस्, Add/थप्नुहोस्,
  Unavailable/उपलब्ध छैन, Out of stock/स्टक सकियो, etc.
- `get_shop_type_display_nepali()` already exists on Shop model — used here
- Apply bilingual labels to `public_menu.html` (customer-facing) first, then
  optionally dashboard
- No Django i18n/gettext required — simple template-level bilingual strings is fine
  for MVP scope
- Commit: `"Implement bilingual UI labels"`

---

### Day 23 — Mobile responsiveness improvements
**Goal:** Audit and improve the UI across all pages for low-end Android screens (360px+).

**What to build:**
- Review every template with Chrome DevTools mobile emulation
- Fix any overflowing tables, truncated text, too-small tap targets (<44px)
- Dashboard order list: collapse less-important columns on small screens
- Cart drawer: ensure full usability on 360px width
- Product cards on public menu: verify image + text layout at small sizes
- CSS additions to `main.css` under `@media (max-width: 576px)` and `(max-width: 375px)`
- Commit: `"Improve mobile responsiveness"`

---

### Day 24 — Database query optimisation
**Goal:** Audit all views for N+1 queries using Django Debug Toolbar or manual review.

**What to build:**
- Install/configure `django-debug-toolbar` for local dev (not production)
- Add `select_related` / `prefetch_related` where missing
- Key places to check:
  - `shop_orders_view` — `order.items.count()` is already annotated, check items prefetch
  - `order_detail_view` — already uses `prefetch_related("items")`, verify
  - `dashboard_home_view` — `pending_counts` and `low_stock_counts` already one query each
  - `public_menu_view` — already uses `prefetch_related("products")`
- Add DB indexes if any queries show sequential scans on large tables
- Commit: `"Optimize database queries"`

---

### Day 25 — Dashboard analytics widgets
**Goal:** Add summary statistics to the owner dashboard home page.

**What to build:**
- In `dashboard_home_view`, annotate/aggregate per-shop stats:
  - Total orders today / this week / this month
  - Total revenue today / this week / this month (sum of subtotals for non-cancelled)
  - Top 3 selling products (by quantity in OrderItems)
  - Average order value
- Display as stat cards on `dashboard/home.html`
- Keep queries efficient — aggregate in the DB, not Python loops
- Commit: `"Add dashboard analytics widgets"`

---

### Day 26 — Security hardening
**Goal:** Audit and harden the application before production deployment.

**What to build:**
- Add `django-ratelimit` or manual rate limiting on `place_order_view` and
  `verify_otp_view` (already has DB-based rate limiting, but add IP-level too)
- Review all views for missing `@login_required` or ownership checks
- Add `Content-Security-Policy` header in settings/middleware
- Ensure `SECURE_BROWSER_XSS_FILTER`, `SECURE_CONTENT_TYPE_NOSNIFF`,
  `X_FRAME_OPTIONS = "DENY"` are set in production settings
- Audit `place_order_view` for any remaining injection risks
- Add `ALLOWED_HOSTS` properly for Render domain
- Review `DEBUG=False` production checklist
- Commit: `"Implement security hardening"`

---

### Day 27 — Automated tests (final coverage pass)
**Goal:** Fill any remaining test gaps across all apps before deployment.

**What to build:**
- Run `python manage.py test` across all apps and fix any failures
- Add missing test classes for Days 21-26 features
- Check coverage on: audio notification template rendering, bilingual labels,
  analytics queries, security headers
- Integration test: full customer journey (scan QR → browse menu → add to cart →
  place order → owner sees order → status update → stock decremented)
- Commit: `"Write automated tests"`

---

### Day 28 — Configure Neon production database
**Goal:** Switch from SQLite to Neon PostgreSQL for production.

**What to build:**
- Create Neon project and get `DATABASE_URL` connection string
- Add `DATABASE_URL` to `.env` and Render environment variables
- Run `python manage.py migrate` against Neon
- Verify `select_for_update()` behavior (concurrency tests that were skipped on SQLite
  should now pass — `REQUIRES_ROW_LOCKING` skip decorator will not skip on PostgreSQL)
- Run `python manage.py test` against Neon to verify the full suite including the
  previously-skipped concurrency tests in `products/tests.py` and `orders/tests.py`
- `CONN_MAX_AGE = 60` in settings for Neon serverless connection pooling
- Commit: `"Configure Neon production database"`

---

### Day 29 — Configure Render deployment
**Goal:** Deploy to Render with all environment variables configured.

**What to build:**
- `render.yaml` (already exists from Day 1, verify it's current):
  ```yaml
  buildCommand: "pip install -r requirements.txt && python manage.py collectstatic --no-input && python manage.py migrate"
  startCommand: "gunicorn config.wsgi:application --workers 2 --bind 0.0.0.0:$PORT"
  ```
- Set all Render environment variables: `SECRET_KEY`, `DEBUG=False`, `DATABASE_URL`,
  `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`,
  `ALLOWED_HOSTS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
- Verify `whitenoise` serves static files correctly (`STATICFILES_STORAGE`)
- Test production deploy: register → OTP → login → create shop → add products →
  generate QR → customer scans → places order → owner sees it
- Commit: `"Configure Render deployment"`

---

### Day 30 — Production release and documentation
**Goal:** Final checks, README, and public release.

**What to build:**
- `README.md` with: project description, features, setup instructions, environment
  variables list, how to run locally, how to run tests, deployment notes, screenshots
- Final `python manage.py check --deploy` — fix any warnings
- Add `createsuperuser` instructions for first admin setup
- Tag the release: `git tag v1.0.0`
- Final commit: `"Production release and documentation"`

---

## Key conventions — MUST follow in all future days

1. **Commit splitting:** model+migration → forms → views+urls → admin → templates → tests
2. **Tests append to each app's own `tests.py`** (never separate files)
3. **Helper functions** (`make_verified_user`, `make_shop`, `make_category`,
   `make_product`) are at the top of each app's `tests.py` — don't redefine them
4. **Ownership checks:** single joined queryset, never two separate checks
5. **404 not 403** for cross-owner access (don't confirm object existence)
6. **`full_clean()` in create/edit views** so model validators surface as form errors
7. **`transaction.atomic()` + `select_for_update()`** for race-sensitive writes
8. **SQLite concurrency tests** — use `REQUIRES_ROW_LOCKING` skip decorator
9. **`secrets.randbelow()`** not `random.randint()` for auth codes
10. **`TextChoices`** for all enum-style fields (not raw string lists)
11. **Env vars via `config("VAR_NAME")`** from python-decouple, not `os.environ.get`
12. **`reverse()` in models** only once the URL pattern exists
13. **Nepali bilingual labels** via `get_shop_type_display_nepali()` on Shop
14. **Run tests before presenting files**, fix failures before presenting
15. **Present files using the `present_files` tool** so they're downloadable
16. **`InventoryLog.record()`** is the ONLY entry point for audited stock changes
17. **`Order.transition_status()`** is the ONLY place order status changes happen
18. **`Order.update_payment()`** is the ONLY place payment status/method changes happen
19. **`Product.is_orderable`** is the single source of truth for orderability
20. **`LOW_STOCK_FILTER` dict** in `dashboard/views.py` mirrors `Product.is_low_stock`
    — keep them in sync if the threshold ever changes (currently `0 < stock <= 5`)

---

## Test suite status (as of Day 20)
```bash
python manage.py test accounts    # 39 tests, all pass
python manage.py test shops       # 75+ tests, all pass
python manage.py test categories  # 23 tests, all pass
python manage.py test products    # 55 tests, 1 skipped (SQLite concurrency)
python manage.py test orders      # pass
python manage.py test qr_manager  # pass
python manage.py test inventory   # pass
python manage.py test dashboard   # pass
```

---

## How to start a new session with this file

1. Read this entire file before writing any code
2. Ask Mandip: "Which day are we on and what needs to be built?"
3. Match the flat app structure — no `apps.` prefix anywhere
4. Use `config()` from python-decouple for all env vars
5. Follow the commit-splitting convention (point 1 in conventions above)
6. All test classes append to the relevant app's `tests.py`
7. Run tests before presenting files, fix any failures first
8. Present files using the `present_files` tool

## SDD future enhancements (beyond Day 30)
eSewa integration, Khalti integration, SMS alerts, Viber/WhatsApp alerts, employee
accounts, multi-branch support, dedicated mobile app, advanced analytics/reporting.
