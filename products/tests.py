"""
Automated QA tests for Sajilo Pasal — Products app (Day 6).

Covers checklist sections:
  4. Menu Items — add, edit, delete, mark available/unavailable,
                  verify category relationship
  7. Permissions — three-hop ownership (product -> category -> shop -> owner)
  8. Forms       — empty/invalid/negative price, long text, image validation
  9. URLs        — no 500s, cross-owner access attempts

Plus core Day 6 logic:
  - is_orderable truth table (allow_over_order semantics)
  - adjust_stock() race-safety under concurrent calls

Run with:
    python manage.py test products -v 2
"""

import io
import threading

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, TransactionTestCase, Client
from django.urls import reverse

from accounts.models import User
from shops.models import Shop
from categories.models import Category
from .models import Product, validate_product_image


VALID_PASSWORD = "StrongPass123!"


def make_verified_user(email):
    user = User.objects.create_user(email=email, password=VALID_PASSWORD)
    user.is_active = True
    user.is_verified = True
    user.save()
    return user


def make_shop(owner, name="Test Shop"):
    return Shop.objects.create(owner=owner, name=name)


def make_category(shop, name="Test Category"):
    return Category.objects.create(shop=shop, name=name)


def make_test_image_bytes(format="JPEG"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format=format)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────
# is_orderable truth table — the core Day 6 logic
# ─────────────────────────────────────────────

class OrderabilityTruthTableTests(TestCase):
    """
    Directly verifies every row of the spec table:

        stock | allow_over_order | can_order?
          0    |      False       |    No
          0    |      True        |    Yes
          3    |      False       |    Yes
         -2    |      True        |    Yes
    """

    def setUp(self):
        owner = make_verified_user("orderable@example.com")
        shop = make_shop(owner)
        self.category = make_category(shop)

    def _make_product(self, stock, allow_over_order):
        return Product.objects.create(
            category=self.category,
            name="Test Product",
            price=Decimal("100.00"),
            stock_quantity=stock,
            allow_over_order=allow_over_order,
        )

    def test_zero_stock_no_over_order_not_orderable(self):
        p = self._make_product(stock=0, allow_over_order=False)
        self.assertFalse(p.is_orderable)

    def test_zero_stock_with_over_order_is_orderable(self):
        p = self._make_product(stock=0, allow_over_order=True)
        self.assertTrue(p.is_orderable)

    def test_positive_stock_no_over_order_is_orderable(self):
        p = self._make_product(stock=3, allow_over_order=False)
        self.assertTrue(p.is_orderable)

    def test_negative_stock_with_over_order_is_orderable(self):
        p = self._make_product(stock=-2, allow_over_order=True)
        self.assertTrue(p.is_orderable)

    def test_negative_stock_without_over_order_not_orderable(self):
        """Not explicitly in the spec table, but logically required:
        negative stock with allow_over_order=False must still block."""
        p = self._make_product(stock=-2, allow_over_order=False)
        self.assertFalse(p.is_orderable)

    def test_is_available_false_overrides_everything(self):
        """An owner's manual 'unavailable' toggle blocks ordering
        regardless of stock or allow_over_order — this is an explicit
        override, not just another stock state."""
        p = self._make_product(stock=10, allow_over_order=True)
        p.is_available = False
        p.save()
        self.assertFalse(p.is_orderable)


class LowStockTests(TestCase):

    def setUp(self):
        owner = make_verified_user("lowstock@example.com")
        shop = make_shop(owner)
        self.category = make_category(shop)

    def test_low_stock_threshold(self):
        p = Product.objects.create(category=self.category, name="P", price=Decimal("10"), stock_quantity=3)
        self.assertTrue(p.is_low_stock)

    def test_zero_stock_not_flagged_low_stock(self):
        """Zero is 'out of stock', a distinct state from 'low stock'."""
        p = Product.objects.create(category=self.category, name="P", price=Decimal("10"), stock_quantity=0)
        self.assertFalse(p.is_low_stock)

    def test_high_stock_not_low(self):
        p = Product.objects.create(category=self.category, name="P", price=Decimal("10"), stock_quantity=50)
        self.assertFalse(p.is_low_stock)

    def test_allow_over_order_never_flagged_low_stock(self):
        """Made-to-order items don't have a meaningful 'low stock'
        concept, even if their stock number happens to be small."""
        p = Product.objects.create(
            category=self.category, name="P", price=Decimal("10"),
            stock_quantity=2, allow_over_order=True,
        )
        self.assertFalse(p.is_low_stock)


# ─────────────────────────────────────────────
# Stock mutation race-safety
# ─────────────────────────────────────────────

class StockAdjustmentTests(TestCase):

    def setUp(self):
        owner = make_verified_user("stockadjust@example.com")
        shop = make_shop(owner)
        self.category = make_category(shop)
        self.product = Product.objects.create(
            category=self.category, name="P", price=Decimal("10"), stock_quantity=10,
        )

    def test_positive_delta_increases_stock(self):
        result = self.product.adjust_stock(5)
        self.assertEqual(result, 15)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 15)

    def test_negative_delta_decreases_stock(self):
        result = self.product.adjust_stock(-3)
        self.assertEqual(result, 7)

    def test_uses_f_expression_not_python_arithmetic(self):
        """
        Confirms adjust_stock reads the CURRENT DB value via F(), not
        a possibly-stale Python-side self.stock_quantity. We simulate
        staleness by mutating the DB directly (bypassing this instance)
        before calling adjust_stock on the original, stale instance.
        """
        # self.product thinks stock is 10, but the DB has been changed
        # to 100 by something else since this instance was loaded.
        Product.objects.filter(pk=self.product.pk).update(stock_quantity=100)

        # If adjust_stock used self.stock_quantity (10) instead of an
        # F() expression against the live DB value, this would wrongly
        # produce 10 - 3 = 7. The correct, race-safe answer is 100 - 3 = 97.
        result = self.product.adjust_stock(-3)
        self.assertEqual(result, 97)


import unittest
from django.db import connection as db_connection

# select_for_update() relies on real row-level locking, which SQLite
# does not provide — it locks the entire database FILE during a write
# transaction instead, so two concurrent writers don't queue politely
# like they would on PostgreSQL; one of them just gets rejected with
# "database is locked" (OperationalError). That's a SQLite limitation
# being surfaced, not a bug in adjust_stock() or this test's logic.
# Since this project runs PostgreSQL in production (Neon, per the SDD)
# and only falls back to SQLite for local dev when DATABASE_URL isn't
# set, this test is skipped on SQLite and should be run against
# Postgres to get a meaningful answer about row-locking behavior.
REQUIRES_ROW_LOCKING = unittest.skipIf(
    db_connection.vendor == "sqlite",
    "select_for_update() row-level locking cannot be meaningfully tested "
    "on SQLite, which locks the whole database file rather than individual "
    "rows. Run this test against PostgreSQL (e.g. set DATABASE_URL) to "
    "verify real concurrent-write behavior.",
)


@REQUIRES_ROW_LOCKING
class StockAdjustmentConcurrencyTests(TransactionTestCase):
    """
    Uses TransactionTestCase (not TestCase) because we need REAL
    separate database transactions running in separate threads to
    actually exercise select_for_update()'s row locking — TestCase
    wraps each test in a single outer transaction that would make
    this test meaningless (everything would appear to run in the
    same transaction, masking any race condition entirely).
    """

    def setUp(self):
        self.owner = make_verified_user("concurrency@example.com")
        self.shop = make_shop(self.owner)
        self.category = make_category(self.shop)
        self.product = Product.objects.create(
            category=self.category, name="Last Item", price=Decimal("10"), stock_quantity=1,
        )

    def test_concurrent_decrements_do_not_lose_updates(self):
        """
        Simulates two near-simultaneous orders both trying to
        decrement the same product's stock by 1, starting from
        stock_quantity=1. Without select_for_update() locking, both
        threads could read stock=1, both compute 1-1=0, and the final
        state would be 0 with both "sales" believing they succeeded —
        even though only one unit of stock actually existed.

        With proper locking, the second thread's transaction waits for
        the first to commit, reads the now-updated value, and the
        final stock correctly reflects BOTH decrements (down to -1),
        proving no update was silently lost.
        """
        results = []
        errors = []

        def decrement():
            try:
                # Each thread needs its own DB connection — Django
                # connections aren't thread-safe to share.
                product = Product.objects.get(pk=self.product.pk)
                new_stock = product.adjust_stock(-1)
                results.append(new_stock)
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        t1 = threading.Thread(target=decrement)
        t2 = threading.Thread(target=decrement)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")

        self.product.refresh_from_db()
        # Both decrements must be reflected — final stock is 1 - 1 - 1 = -1,
        # NOT -0 or 0, which would indicate one decrement was lost to the race.
        self.assertEqual(self.product.stock_quantity, -1)
        # And the two individual results returned should be different
        # values (0 and -1), proving they did NOT both see/return the
        # same stale starting point.
        self.assertEqual(sorted(results), [-1, 0])


# ─────────────────────────────────────────────
# Image validation (same pattern as Shop.logo)
# ─────────────────────────────────────────────

class ProductImageValidationTests(TestCase):

    def test_valid_jpeg_passes(self):
        content = make_test_image_bytes("JPEG")
        upload = SimpleUploadedFile("p.jpg", content, content_type="image/jpeg")
        validate_product_image(upload)

    def test_oversized_rejected(self):
        content = make_test_image_bytes("JPEG")
        upload = SimpleUploadedFile("p.jpg", content, content_type="image/jpeg")
        upload.size = 6 * 1024 * 1024
        with self.assertRaises(ValidationError):
            validate_product_image(upload)

    def test_disguised_tiff_rejected(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="TIFF")
        buf.seek(0)
        upload = SimpleUploadedFile("p.jpg", buf.read(), content_type="image/jpeg")
        with self.assertRaises(ValidationError):
            validate_product_image(upload)


# ─────────────────────────────────────────────
# CRUD via views
# ─────────────────────────────────────────────

class ProductCRUDTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.owner = make_verified_user("prodcrud@example.com")
        self.shop = make_shop(self.owner)
        self.category = make_category(self.shop)
        self.client.login(username=self.owner.email, password=VALID_PASSWORD)

    def test_create_product(self):
        resp = self.client.post(
            reverse("products:create", args=[self.shop.slug, self.category.id]),
            {"name": "Chicken Momo", "description": "Tasty", "price": "150.00",
             "stock_quantity": "20", "is_available": "on"},
        )
        self.assertEqual(Product.objects.filter(category=self.category).count(), 1)
        product = Product.objects.get(category=self.category)
        self.assertEqual(product.price, Decimal("150.00"))

    def test_edit_product_saves_changes(self):
        p = Product.objects.create(category=self.category, name="Old", price=Decimal("100"))
        self.client.post(
            reverse("products:edit", args=[self.shop.slug, self.category.id, p.id]),
            {"name": "New Name", "price": "200.00", "stock_quantity": "5", "is_available": "on"},
        )
        p.refresh_from_db()
        self.assertEqual(p.name, "New Name")
        self.assertEqual(p.price, Decimal("200.00"))

    def test_delete_product(self):
        p = Product.objects.create(category=self.category, name="ToDelete", price=Decimal("10"))
        self.client.post(reverse("products:delete", args=[self.shop.slug, self.category.id, p.id]))
        self.assertFalse(Product.objects.filter(pk=p.pk).exists())

    def test_toggle_availability(self):
        p = Product.objects.create(category=self.category, name="P", price=Decimal("10"), is_available=True)
        self.client.post(reverse("products:toggle_availability", args=[self.shop.slug, self.category.id, p.id]))
        p.refresh_from_db()
        self.assertFalse(p.is_available)
        self.client.post(reverse("products:toggle_availability", args=[self.shop.slug, self.category.id, p.id]))
        p.refresh_from_db()
        self.assertTrue(p.is_available)

    def test_product_list_scoped_to_category(self):
        Product.objects.create(category=self.category, name="InThisCategory", price=Decimal("10"))
        other_category = make_category(self.shop, "Other Category")
        Product.objects.create(category=other_category, name="NotInThisCategory", price=Decimal("10"))

        resp = self.client.get(reverse("products:list", args=[self.shop.slug, self.category.id]))
        names = [p.name for p in resp.context["products"]]
        self.assertIn("InThisCategory", names)
        self.assertNotIn("NotInThisCategory", names)


# ─────────────────────────────────────────────
# Form validation
# ─────────────────────────────────────────────

class ProductFormValidationTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.owner = make_verified_user("prodform@example.com")
        self.shop = make_shop(self.owner)
        self.category = make_category(self.shop)
        self.client.login(username=self.owner.email, password=VALID_PASSWORD)

    def test_empty_name_rejected(self):
        resp = self.client.post(
            reverse("products:create", args=[self.shop.slug, self.category.id]),
            {"name": "", "price": "100"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Product.objects.count(), 0)

    def test_missing_price_rejected(self):
        resp = self.client.post(
            reverse("products:create", args=[self.shop.slug, self.category.id]),
            {"name": "Valid Name"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Product.objects.count(), 0)

    def test_negative_price_rejected(self):
        resp = self.client.post(
            reverse("products:create", args=[self.shop.slug, self.category.id]),
            {"name": "Valid Name", "price": "-50.00"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Product.objects.count(), 0)

    def test_non_numeric_price_rejected(self):
        resp = self.client.post(
            reverse("products:create", args=[self.shop.slug, self.category.id]),
            {"name": "Valid Name", "price": "free"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Product.objects.count(), 0)

    def test_very_long_name_does_not_crash(self):
        resp = self.client.post(
            reverse("products:create", args=[self.shop.slug, self.category.id]),
            {"name": "A" * 5000, "price": "100"},
        )
        self.assertIn(resp.status_code, (200, 302))

    def test_decimal_price_with_paisa_accepted(self):
        resp = self.client.post(
            reverse("products:create", args=[self.shop.slug, self.category.id]),
            {"name": "Precise Price Item", "price": "150.50"},
        )
        product = Product.objects.get(name="Precise Price Item")
        self.assertEqual(product.price, Decimal("150.50"))


# ─────────────────────────────────────────────
# Three-hop ownership permissions
# ─────────────────────────────────────────────

class ProductOwnershipPermissionTests(TestCase):
    """
    product -> category -> shop -> owner, all three hops must hold.
    """

    def setUp(self):
        self.user_a = make_verified_user("prodownera@example.com")
        self.user_b = make_verified_user("prodownerb@example.com")
        self.shop_a = make_shop(self.user_a, "Shop A")
        self.category_a = make_category(self.shop_a, "Category A")
        self.product = Product.objects.create(category=self.category_a, name="A's Product", price=Decimal("10"))

        self.client_a = Client()
        self.client_a.login(username=self.user_a.email, password=VALID_PASSWORD)
        self.client_b = Client()
        self.client_b.login(username=self.user_b.email, password=VALID_PASSWORD)

    def test_owner_can_view_own_product_list(self):
        resp = self.client_a.get(reverse("products:list", args=[self.shop_a.slug, self.category_a.id]))
        self.assertEqual(resp.status_code, 200)

    def test_other_user_cannot_view_product_list(self):
        resp = self.client_b.get(reverse("products:list", args=[self.shop_a.slug, self.category_a.id]))
        self.assertEqual(resp.status_code, 404)

    def test_other_user_cannot_create_product(self):
        resp = self.client_b.post(
            reverse("products:create", args=[self.shop_a.slug, self.category_a.id]),
            {"name": "Injected", "price": "10"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(Product.objects.filter(name="Injected").exists())

    def test_other_user_cannot_edit_product(self):
        resp = self.client_b.post(
            reverse("products:edit", args=[self.shop_a.slug, self.category_a.id, self.product.id]),
            {"name": "HIJACKED", "price": "999"},
        )
        self.assertEqual(resp.status_code, 404)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "A's Product")

    def test_other_user_cannot_delete_product(self):
        resp = self.client_b.post(
            reverse("products:delete", args=[self.shop_a.slug, self.category_a.id, self.product.id])
        )
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_other_user_cannot_toggle_availability(self):
        original = self.product.is_available
        resp = self.client_b.post(
            reverse("products:toggle_availability", args=[self.shop_a.slug, self.category_a.id, self.product.id])
        )
        self.assertEqual(resp.status_code, 404)
        self.product.refresh_from_db()
        self.assertEqual(self.product.is_available, original)

    def test_product_under_wrong_category_id_returns_404(self):
        """
        The real three-hop test: a product that's genuinely real, but
        the category_id in the URL doesn't match the product's actual
        category — even if that other category belongs to the SAME
        owner via a different shop. Catches ownership checks done as
        independent conditions rather than one fully joined query.
        """
        other_shop_same_owner = make_shop(self.user_a, "User A's Other Shop")
        other_category_same_owner = make_category(other_shop_same_owner, "Other Category")

        resp = self.client_a.get(
            reverse("products:edit", args=[other_shop_same_owner.slug, other_category_same_owner.id, self.product.id])
        )
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_user_redirected_to_login(self):
        anon = Client()
        resp = anon.get(reverse("products:list", args=[self.shop_a.slug, self.category_a.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp.url)

    def test_nonexistent_product_id_returns_404(self):
        resp = self.client_a.get(
            reverse("products:edit", args=[self.shop_a.slug, self.category_a.id, 999999])
        )
        self.assertEqual(resp.status_code, 404)


# ─────────────────────────────────────────────
# Second-review fixes: stock business rule, name
# uniqueness, IntegerField confirmation
# ─────────────────────────────────────────────

class StockBusinessRuleValidationTests(TestCase):
    """
    Point 2: negative stock without allow_over_order should be
    rejected at the model validation layer, catching the case where
    Django admin or a shell session saves an invalid combination
    directly.
    """

    def setUp(self):
        owner = make_verified_user("stockrule@example.com")
        shop = make_shop(owner)
        self.category = make_category(shop)

    def test_negative_stock_without_over_order_rejected_by_clean(self):
        p = Product(
            category=self.category, name="Bad State Product",
            price=Decimal("10"), stock_quantity=-5, allow_over_order=False,
        )
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_negative_stock_with_over_order_passes_clean(self):
        p = Product(
            category=self.category, name="Made To Order Product",
            price=Decimal("10"), stock_quantity=-5, allow_over_order=True,
        )
        p.full_clean()  # should NOT raise

    def test_positive_stock_without_over_order_passes_clean(self):
        p = Product(
            category=self.category, name="Normal Product",
            price=Decimal("10"), stock_quantity=5, allow_over_order=False,
        )
        p.full_clean()

    def test_zero_stock_without_over_order_passes_clean(self):
        p = Product(
            category=self.category, name="Out Of Stock Product",
            price=Decimal("10"), stock_quantity=0, allow_over_order=False,
        )
        p.full_clean()

    def test_view_surfaces_stock_rule_violation_as_form_error_not_500(self):
        """The real-world path: an owner submits a form with
        allow_over_order unchecked but a negative stock number somehow
        present — must fail gracefully via the view, not 500."""
        client = Client()
        owner = make_verified_user("stockview@example.com")
        shop = make_shop(owner)
        category = make_category(shop)
        client.login(username=owner.email, password=VALID_PASSWORD)

        resp = client.post(
            reverse("products:create", args=[shop.slug, category.id]),
            {"name": "Invalid Stock Product", "price": "10", "stock_quantity": "-5"},
            # allow_over_order checkbox omitted = False
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Product.objects.filter(name="Invalid Stock Product").exists())

    def test_adjust_stock_does_not_enforce_business_rule_by_design(self):
        """
        Documents the deliberate gap: adjust_stock() bypasses clean()
        for speed, so it CAN drive a non-allow_over_order product
        negative if the caller doesn't check is_orderable first. This
        is intentional (see docstring) — this test exists so that if
        someone "fixes" this later, they do so knowingly rather than
        by accident.
        """
        p = Product.objects.create(
            category=self.category, name="No Guard Product",
            price=Decimal("10"), stock_quantity=0, allow_over_order=False,
        )
        result = p.adjust_stock(-1)  # does NOT raise, even though this violates the business rule
        self.assertEqual(result, -1)


class ProductNameUniquenessTests(TestCase):
    """Point 3: UniqueConstraint(category, name), same pattern as
    Category's per-shop uniqueness."""

    def setUp(self):
        owner = make_verified_user("prodname@example.com")
        self.shop = make_shop(owner)
        self.drinks = make_category(self.shop, "Drinks")
        self.momos = make_category(self.shop, "Momos")

    def test_duplicate_name_within_same_category_rejected(self):
        Product.objects.create(category=self.drinks, name="Coke", price=Decimal("100"))
        dup = Product(category=self.drinks, name="Coke", price=Decimal("100"))
        with self.assertRaises(ValidationError):
            dup.full_clean()

    def test_duplicate_name_case_insensitive_within_same_category(self):
        Product.objects.create(category=self.drinks, name="Coke", price=Decimal("100"))
        dup = Product(category=self.drinks, name="coke", price=Decimal("100"))
        with self.assertRaises(ValidationError):
            dup.full_clean()

    def test_same_name_allowed_across_different_categories(self):
        """'Veg Momo' in Momos and 'Veg Momo' in a hypothetical second
        Momos-like category should both be fine — uniqueness is scoped
        to category, not global or per-shop."""
        Product.objects.create(category=self.drinks, name="Tea", price=Decimal("50"))
        Product.objects.create(category=self.momos, name="Tea", price=Decimal("50"))  # different category
        self.assertEqual(Product.objects.filter(name="Tea").count(), 2)

    def test_editing_product_to_its_own_name_does_not_self_collide(self):
        p = Product.objects.create(category=self.drinks, name="Coke", price=Decimal("100"))
        p.price = Decimal("120")  # name unchanged
        p.full_clean()  # should NOT raise

    def test_db_constraint_is_final_backstop(self):
        from django.db import IntegrityError, transaction
        Product.objects.create(category=self.drinks, name="Sprite", price=Decimal("100"))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Product.objects.create(category=self.drinks, name="Sprite", price=Decimal("100"))

    def test_view_rejects_duplicate_with_form_error_not_500(self):
        client = Client()
        client.login(username=self.shop.owner.email, password=VALID_PASSWORD)
        Product.objects.create(category=self.drinks, name="Fanta", price=Decimal("100"))

        resp = client.post(
            reverse("products:create", args=[self.shop.slug, self.drinks.id]),
            {"name": "Fanta", "price": "100"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Product.objects.filter(category=self.drinks, name="Fanta").count(), 1)


class StockFieldTypeTests(TestCase):
    """Point 1: confirms stock_quantity stays IntegerField (signed),
    not PositiveIntegerField — a regression guard against someone
    'fixing' this later without realizing it breaks allow_over_order."""

    def test_stock_quantity_field_allows_negative_values_at_db_level(self):
        owner = make_verified_user("fieldtype@example.com")
        shop = make_shop(owner)
        category = make_category(shop)
        # If stock_quantity were PositiveIntegerField, this would raise
        # an IntegrityError or ValidationError at the DB/field level —
        # it must succeed, since allow_over_order=True products are
        # explicitly allowed to carry negative stock as a signal.
        p = Product.objects.create(
            category=category, name="Negative Stock OK Product",
            price=Decimal("10"), stock_quantity=-10, allow_over_order=True,
        )
        p.refresh_from_db()
        self.assertEqual(p.stock_quantity, -10)

 # ─────────────────────────────────────────────
# Day 7 — Cloudinary regression guards
# ─────────────────────────────────────────────

class ProductImageValidatorCloudinaryRegressionTests(TestCase):
    """
    Confirms validate_product_image still rejects bad files after
    Cloudinary config — validators must fire BEFORE the storage
    backend sees the file.
    """

    def test_valid_jpeg_still_passes_after_cloudinary_config(self):
        content = make_test_image_bytes("JPEG")
        upload = SimpleUploadedFile("p.jpg", content, content_type="image/jpeg")
        validate_product_image(upload)  # must not raise

    def test_disguised_tiff_still_rejected(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="TIFF")
        buf.seek(0)
        upload = SimpleUploadedFile("p.jpg", buf.read(), content_type="image/jpeg")
        with self.assertRaises(ValidationError):
            validate_product_image(upload)

    def test_oversized_still_rejected(self):
        content = make_test_image_bytes("JPEG")
        upload = SimpleUploadedFile("p.jpg", content, content_type="image/jpeg")
        upload.size = 6 * 1024 * 1024
        with self.assertRaises(ValidationError):
            validate_product_image(upload)


class CompressorJsProductTemplateTests(TestCase):
    """
    Confirms Compressor.js is rendered in product upload forms
    but not on non-upload pages.
    """

    def setUp(self):
        self.client = Client()
        self.owner = make_verified_user("prodcompressor@example.com")
        self.shop = make_shop(self.owner)
        self.category = make_category(self.shop)
        self.client.login(username=self.owner.email, password=VALID_PASSWORD)

    def test_product_create_form_includes_compressorjs(self):
        resp = self.client.get(
            reverse("products:create", args=[self.shop.slug, self.category.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "compressorjs")
        self.assertContains(resp, "initImageCompressor")

    def test_product_edit_form_includes_compressorjs(self):
        product = Product.objects.create(
            category=self.category, name="Test Product", price=Decimal("100")
        )
        resp = self.client.get(
            reverse("products:edit", args=[self.shop.slug, self.category.id, product.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "compressorjs")
        self.assertContains(resp, "initImageCompressor")
        
               