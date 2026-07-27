"""
Automated QA tests for Sajilo Pasal — Orders app.

Day 11 covers:
  - Order and OrderItem model creation
  - Sequential per-shop order numbering (race-safe)
  - Duplicate order token prevention
  - Price/name snapshot at order time
  - TextChoices for status, payment_status, payment_method
  - OrderItem survives product deletion (SET_NULL)

Day 12 adds:
  - place_order_view: POST from the cart drawer (fetch-based)
  - Server-side re-pricing / availability re-check (client cart is
    untrusted input — only product id + quantity are taken from it)
  - Idempotency via order_token (double-submit protection over HTTP,
    not just at the model layer)
  - Cross-shop product isolation
  - Request validation (empty cart, bad JSON, bad quantity, bad
    payment method, wrong HTTP method, inactive shop)

Day 14 will add:
  - Stock decrement on status change to Preparing
  - Owner dashboard order management

Run with:
    python manage.py test orders -v 2
"""

import json
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, Client
from django.urls import reverse

from accounts.models import User
from shops.models import Shop
from categories.models import Category
from products.models import Product
from .models import Order, OrderItem, OrderStatus, PaymentStatus, PaymentMethod


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


def make_product(category, name="Test Product", price="100.00", stock=10):
    return Product.objects.create(
        category=category,
        name=name,
        price=Decimal(price),
        stock_quantity=stock,
        is_available=True,
    )


def make_cart_items(*products):
    """Build a cart_items list from Product instances."""
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "price": str(p.price),
            "quantity": 1,
        }
        for p in products
    ]


# ─────────────────────────────────────────────
# Order number generation
# ─────────────────────────────────────────────

class OrderNumberSequenceTests(TestCase):

    def setUp(self):
        owner = make_verified_user("ordernums@example.com")
        self.shop = make_shop(owner)
        cat = make_category(self.shop)
        self.product = make_product(cat)

    def _place(self, token=None):
        return Order.create_from_cart(
            shop=self.shop,
            cart_items=make_cart_items(self.product),
            order_token=token or uuid.uuid4(),
        )

    def test_first_order_gets_number_1(self):
        order = self._place()
        self.assertEqual(order.order_number, 1)

    def test_second_order_gets_number_2(self):
        self._place()
        order2 = self._place()
        self.assertEqual(order2.order_number, 2)

    def test_order_numbers_are_sequential(self):
        numbers = [self._place().order_number for _ in range(5)]
        self.assertEqual(numbers, [1, 2, 3, 4, 5])

    def test_order_numbers_are_per_shop(self):
        """Two different shops each start their own sequence from 1."""
        owner2 = make_verified_user("ordernums2@example.com")
        shop2 = make_shop(owner2, "Shop Two")
        cat2 = make_category(shop2)
        product2 = make_product(cat2)

        order_a = self._place()
        order_b = Order.create_from_cart(
            shop=shop2,
            cart_items=[{"id": str(product2.id), "name": product2.name,
                         "price": str(product2.price), "quantity": 1}],
        )
        self.assertEqual(order_a.order_number, 1)
        self.assertEqual(order_b.order_number, 1)
        self.assertNotEqual(order_a.shop, order_b.shop)

    def test_order_number_display_zero_padded(self):
        order = self._place()
        self.assertEqual(order.order_number_display, "#001")

    def test_order_number_display_large_number(self):
        for _ in range(99):
            self._place()
        order = self._place()
        self.assertEqual(order.order_number, 100)
        self.assertEqual(order.order_number_display, "#100")

    def test_unique_constraint_prevents_duplicate_order_numbers(self):
        """Belt-and-suspenders: DB constraint catches any edge case the
        application logic misses."""
        self._place()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Order.objects.create(
                    shop=self.shop,
                    order_number=1,  # already taken
                    order_token=uuid.uuid4(),
                    subtotal=Decimal("0"),
                )


# ─────────────────────────────────────────────
# Duplicate order token prevention (model layer)
# ─────────────────────────────────────────────

class DuplicateOrderTokenTests(TestCase):
    """
    order_token is a UUID issued by the cart JS before submission.
    If a customer double-taps "Place Order" on a slow connection, the
    second request carries the same token and must be rejected at the
    DB level (unique=True on order_token).

    See PlaceOrderIdempotencyTests below for the HTTP-layer behaviour
    (place_order_view catches this and returns the existing order
    instead of letting the IntegrityError surface).
    """

    def setUp(self):
        owner = make_verified_user("duptoken@example.com")
        self.shop = make_shop(owner)
        cat = make_category(self.shop)
        self.product = make_product(cat)
        self.token = uuid.uuid4()

    def test_same_token_cannot_be_used_twice(self):
        Order.create_from_cart(
            shop=self.shop,
            cart_items=make_cart_items(self.product),
            order_token=self.token,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Order.create_from_cart(
                    shop=self.shop,
                    cart_items=make_cart_items(self.product),
                    order_token=self.token,
                )

    def test_different_tokens_both_succeed(self):
        o1 = Order.create_from_cart(
            shop=self.shop,
            cart_items=make_cart_items(self.product),
            order_token=uuid.uuid4(),
        )
        o2 = Order.create_from_cart(
            shop=self.shop,
            cart_items=make_cart_items(self.product),
            order_token=uuid.uuid4(),
        )
        self.assertNotEqual(o1.order_token, o2.order_token)
        self.assertEqual(Order.objects.count(), 2)


# ─────────────────────────────────────────────
# create_from_cart correctness
# ─────────────────────────────────────────────

class OrderCreationTests(TestCase):

    def setUp(self):
        owner = make_verified_user("ordercreate@example.com")
        self.shop = make_shop(owner)
        cat = make_category(self.shop)
        self.product_a = make_product(cat, "Chicken Momo", "150.00")
        self.product_b = make_product(cat, "Coke", "60.00")

    def test_order_created_with_correct_shop(self):
        order = Order.create_from_cart(
            shop=self.shop,
            cart_items=make_cart_items(self.product_a),
        )
        self.assertEqual(order.shop, self.shop)

    def test_subtotal_computed_correctly(self):
        cart = [
            {"id": str(self.product_a.id), "name": "Chicken Momo",
             "price": "150.00", "quantity": 2},
            {"id": str(self.product_b.id), "name": "Coke",
             "price": "60.00", "quantity": 3},
        ]
        order = Order.create_from_cart(shop=self.shop, cart_items=cart)
        # 2 * 150 + 3 * 60 = 300 + 180 = 480
        self.assertEqual(order.subtotal, Decimal("480.00"))

    def test_order_items_created_for_each_cart_item(self):
        cart = make_cart_items(self.product_a, self.product_b)
        order = Order.create_from_cart(shop=self.shop, cart_items=cart)
        self.assertEqual(order.items.count(), 2)

    def test_order_item_line_total_correct(self):
        cart = [{"id": str(self.product_a.id), "name": "Chicken Momo",
                 "price": "150.00", "quantity": 3}]
        order = Order.create_from_cart(shop=self.shop, cart_items=cart)
        item = order.items.first()
        self.assertEqual(item.line_total, Decimal("450.00"))

    def test_table_number_stored(self):
        order = Order.create_from_cart(
            shop=self.shop,
            cart_items=make_cart_items(self.product_a),
            table_number="Table 5",
        )
        self.assertEqual(order.table_number, "Table 5")

    def test_customer_note_stored(self):
        order = Order.create_from_cart(
            shop=self.shop,
            cart_items=make_cart_items(self.product_a),
            customer_note="Extra spicy please",
        )
        self.assertEqual(order.customer_note, "Extra spicy please")

    def test_payment_method_stored(self):
        order = Order.create_from_cart(
            shop=self.shop,
            cart_items=make_cart_items(self.product_a),
            payment_method=PaymentMethod.FONEPAY,
        )
        self.assertEqual(order.payment_method, PaymentMethod.FONEPAY)

    def test_default_status_is_pending(self):
        order = Order.create_from_cart(
            shop=self.shop,
            cart_items=make_cart_items(self.product_a),
        )
        self.assertEqual(order.status, OrderStatus.PENDING)

    def test_default_payment_status_is_unpaid(self):
        order = Order.create_from_cart(
            shop=self.shop,
            cart_items=make_cart_items(self.product_a),
        )
        self.assertEqual(order.payment_status, PaymentStatus.UNPAID)


# ─────────────────────────────────────────────
# Price and name snapshots
# ─────────────────────────────────────────────

class OrderSnapshotTests(TestCase):
    """
    Prices and product names are stored at order time. If the owner
    edits or deletes a product later, historical order records must
    still show the correct original values.
    """

    def setUp(self):
        owner = make_verified_user("snapshot@example.com")
        self.shop = make_shop(owner)
        cat = make_category(self.shop)
        self.product = make_product(cat, "Dal Bhat", "200.00")

    def test_product_name_snapshot_preserved_after_rename(self):
        order = Order.create_from_cart(
            shop=self.shop,
            cart_items=[{"id": str(self.product.id), "name": "Dal Bhat",
                         "price": "200.00", "quantity": 1}],
        )
        self.product.name = "Dal Bhat Tarkari"
        self.product.save()

        item = order.items.first()
        self.assertEqual(item.product_name, "Dal Bhat")  # unchanged

    def test_unit_price_snapshot_preserved_after_reprice(self):
        order = Order.create_from_cart(
            shop=self.shop,
            cart_items=[{"id": str(self.product.id), "name": "Dal Bhat",
                         "price": "200.00", "quantity": 1}],
        )
        self.product.price = Decimal("250.00")
        self.product.save()

        item = order.items.first()
        self.assertEqual(item.unit_price, Decimal("200.00"))  # unchanged

    def test_order_item_survives_product_deletion(self):
        """
        product FK is SET_NULL — deleting the product must not
        cascade-delete the order history.
        """
        order = Order.create_from_cart(
            shop=self.shop,
            cart_items=[{"id": str(self.product.id), "name": "Dal Bhat",
                         "price": "200.00", "quantity": 1}],
        )
        product_id = self.product.id
        self.product.delete()

        item = order.items.first()
        self.assertIsNotNone(item)
        self.assertIsNone(item.product)          # FK is now NULL
        self.assertEqual(item.product_name, "Dal Bhat")  # snapshot intact
        self.assertEqual(item.unit_price, Decimal("200.00"))


# ─────────────────────────────────────────────
# TextChoices
# ─────────────────────────────────────────────

class OrderChoicesTests(TestCase):

    def test_order_status_choices(self):
        self.assertEqual(OrderStatus.PENDING,   "pending")
        self.assertEqual(OrderStatus.PREPARING, "preparing")
        self.assertEqual(OrderStatus.READY,     "ready")
        self.assertEqual(OrderStatus.CANCELLED, "cancelled")

    def test_payment_status_choices(self):
        self.assertEqual(PaymentStatus.UNPAID, "unpaid")
        self.assertEqual(PaymentStatus.PAID,   "paid")

    def test_payment_method_choices(self):
        self.assertEqual(PaymentMethod.CASH,    "cash")
        self.assertEqual(PaymentMethod.FONEPAY, "fonepay")

    def test_order_status_can_be_updated(self):
        owner = make_verified_user("choices@example.com")
        shop = make_shop(owner)
        cat = make_category(shop)
        product = make_product(cat)
        order = Order.create_from_cart(shop=shop, cart_items=make_cart_items(product))

        order.status = OrderStatus.PREPARING
        order.save(update_fields=["status"])
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PREPARING)


# ─────────────────────────────────────────────
# Race condition — order number (SQLite skip)
# ─────────────────────────────────────────────

import unittest
from django.db import connection as db_connection

REQUIRES_ROW_LOCKING = unittest.skipIf(
    db_connection.vendor == "sqlite",
    "select_for_update() row-level locking cannot be meaningfully tested "
    "on SQLite. Run against PostgreSQL (set DATABASE_URL) for real "
    "concurrent order number race condition verification.",
)


@REQUIRES_ROW_LOCKING
class OrderNumberRaceConditionTests(TransactionTestCase):
    """
    Two simultaneous order submissions must not get the same order number.
    Uses real threads + TransactionTestCase (not TestCase) to exercise
    select_for_update() properly — same pattern as Day 6's stock tests.
    """

    def setUp(self):
        self.owner = make_verified_user("race@example.com")
        self.shop = make_shop(self.owner)
        cat = make_category(self.shop)
        self.product = make_product(cat)

    def test_concurrent_orders_get_unique_numbers(self):
        import threading
        from django.db import connection

        results = []
        errors = []

        def place_order():
            try:
                order = Order.create_from_cart(
                    shop=Shop.objects.get(pk=self.shop.pk),
                    cart_items=[{
                        "id": str(self.product.id),
                        "name": self.product.name,
                        "price": str(self.product.price),
                        "quantity": 1,
                    }],
                )
                results.append(order.order_number)
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        t1 = threading.Thread(target=place_order)
        t2 = threading.Thread(target=place_order)
        t1.start(); t2.start()
        t1.join(); t2.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(results), 2)
        self.assertEqual(len(set(results)), 2)  # both unique
        self.assertEqual(sorted(results), [1, 2])


# ─────────────────────────────────────────────
# orders:place URL — resolution + method enforcement
# ─────────────────────────────────────────────

class OrdersUrlTests(TestCase):
    """
    Day 12: the stub is gone, orders:place now points at the real
    place_order_view, which is POST-only (@require_POST).
    """

    def setUp(self):
        owner = make_verified_user("urlstub@example.com")
        self.shop = make_shop(owner)

    def test_orders_place_url_resolves(self):
        url = reverse("orders:place", args=[self.shop.slug])
        self.assertIn(self.shop.slug, url)

    def test_get_request_not_allowed(self):
        url = reverse("orders:place", args=[self.shop.slug])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 405)


# ─────────────────────────────────────────────
# place_order_view — happy path
# ─────────────────────────────────────────────

class PlaceOrderViewTests(TestCase):

    def setUp(self):
        owner = make_verified_user("placeorder@example.com")
        self.shop = make_shop(owner)
        self.cat = make_category(self.shop)
        self.product_a = make_product(self.cat, "Chicken Momo", "150.00", stock=10)
        self.product_b = make_product(self.cat, "Coke", "60.00", stock=10)
        self.url = reverse("orders:place", args=[self.shop.slug])

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_valid_order_returns_201(self):
        resp = self._post({
            "items": [{"id": self.product_a.id, "quantity": 2}],
            "order_token": str(uuid.uuid4()),
        })
        self.assertEqual(resp.status_code, 201)

    def test_response_success_and_order_number(self):
        resp = self._post({
            "items": [{"id": self.product_a.id, "quantity": 1}],
            "order_token": str(uuid.uuid4()),
        })
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertFalse(data["duplicate"])
        self.assertEqual(data["order"]["order_number"], 1)
        self.assertEqual(data["order"]["order_number_display"], "#001")

    def test_subtotal_computed_from_server_price(self):
        resp = self._post({
            "items": [
                {"id": self.product_a.id, "quantity": 2},  # 150 * 2 = 300
                {"id": self.product_b.id, "quantity": 3},  # 60 * 3 = 180
            ],
            "order_token": str(uuid.uuid4()),
        })
        data = resp.json()
        self.assertEqual(data["order"]["subtotal"], "480.00")

    def test_order_and_items_persisted(self):
        self._post({
            "items": [{"id": self.product_a.id, "quantity": 2}],
            "order_token": str(uuid.uuid4()),
        })
        order = Order.objects.get(shop=self.shop)
        self.assertEqual(order.items.count(), 1)
        item = order.items.first()
        self.assertEqual(item.product_name, "Chicken Momo")
        self.assertEqual(item.unit_price, Decimal("150.00"))
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.line_total, Decimal("300.00"))

    def test_client_supplied_price_is_ignored(self):
        """
        Security: even if a tampered client sends a fake low price, the
        server must charge the real DB price, not the client's number.
        """
        resp = self._post({
            "items": [{"id": self.product_a.id, "quantity": 1, "price": "1.00"}],
            "order_token": str(uuid.uuid4()),
        })
        data = resp.json()
        self.assertEqual(data["order"]["subtotal"], "150.00")

    def test_table_number_and_note_saved(self):
        resp = self._post({
            "items": [{"id": self.product_a.id, "quantity": 1}],
            "table_number": "Table 7",
            "customer_note": "No onions",
            "order_token": str(uuid.uuid4()),
        })
        data = resp.json()
        self.assertEqual(data["order"]["table_number"], "Table 7")
        self.assertEqual(data["order"]["customer_note"], "No onions")

    def test_payment_method_saved(self):
        resp = self._post({
            "items": [{"id": self.product_a.id, "quantity": 1}],
            "payment_method": "fonepay",
            "order_token": str(uuid.uuid4()),
        })
        data = resp.json()
        self.assertEqual(data["order"]["payment_method"], "fonepay")

    def test_defaults_to_cash_when_payment_method_omitted(self):
        resp = self._post({
            "items": [{"id": self.product_a.id, "quantity": 1}],
            "order_token": str(uuid.uuid4()),
        })
        data = resp.json()
        self.assertEqual(data["order"]["payment_method"], "cash")

    def test_response_includes_order_token(self):
        resp = self._post({
            "items": [{"id": self.product_a.id, "quantity": 1}],
            "order_token": str(uuid.uuid4()),
        })
        data = resp.json()
        order = Order.objects.get(shop=self.shop)
        self.assertEqual(data["order"]["token"], str(order.order_token))

    def test_stock_not_decremented_on_placement(self):
        """Day 12 placement must not touch stock — that's Day 14."""
        self._post({
            "items": [{"id": self.product_a.id, "quantity": 3}],
            "order_token": str(uuid.uuid4()),
        })
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock_quantity, 10)


# ─────────────────────────────────────────────
# place_order_view — availability / stock enforcement
# ─────────────────────────────────────────────

class PlaceOrderAvailabilityTests(TestCase):

    def setUp(self):
        owner = make_verified_user("placeavail@example.com")
        self.shop = make_shop(owner)
        self.cat = make_category(self.shop)
        self.url = reverse("orders:place", args=[self.shop.slug])

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_unavailable_product_rejected(self):
        product = make_product(self.cat, "Hidden Item", "50.00", stock=5)
        product.is_available = False
        product.save()

        resp = self._post({
            "items": [{"id": product.id, "quantity": 1}],
            "order_token": str(uuid.uuid4()),
        })
        data = resp.json()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(data["error"], "items_unavailable")
        self.assertEqual(data["unavailable_items"][0]["reason"], "unavailable")
        self.assertEqual(Order.objects.count(), 0)

    def test_out_of_stock_product_rejected(self):
        product = make_product(self.cat, "Sold Out Item", "50.00", stock=0)

        resp = self._post({
            "items": [{"id": product.id, "quantity": 1}],
            "order_token": str(uuid.uuid4()),
        })
        data = resp.json()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(data["unavailable_items"][0]["reason"], "out_of_stock")

    def test_allow_over_order_permits_ordering_past_zero_stock(self):
        product = make_product(self.cat, "Made To Order", "80.00", stock=0)
        product.allow_over_order = True
        product.save()

        resp = self._post({
            "items": [{"id": product.id, "quantity": 5}],
            "order_token": str(uuid.uuid4()),
        })
        self.assertEqual(resp.status_code, 201)

    def test_quantity_exceeding_stock_rejected_when_no_over_order(self):
        product = make_product(self.cat, "Limited Item", "40.00", stock=3)

        resp = self._post({
            "items": [{"id": product.id, "quantity": 5}],
            "order_token": str(uuid.uuid4()),
        })
        data = resp.json()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(data["unavailable_items"][0]["reason"], "insufficient_stock")
        self.assertEqual(data["unavailable_items"][0]["available"], 3)

    def test_quantity_within_stock_accepted(self):
        product = make_product(self.cat, "Limited Item", "40.00", stock=3)

        resp = self._post({
            "items": [{"id": product.id, "quantity": 3}],
            "order_token": str(uuid.uuid4()),
        })
        self.assertEqual(resp.status_code, 201)

    def test_mixed_cart_with_one_bad_item_rejects_whole_order(self):
        """
        Order.create_from_cart is atomic — a bad item anywhere in the
        cart must prevent the entire order from being created, not
        just skip that line.
        """
        good = make_product(self.cat, "Good Item", "50.00", stock=5)
        bad = make_product(self.cat, "Bad Item", "50.00", stock=0)

        resp = self._post({
            "items": [
                {"id": good.id, "quantity": 1},
                {"id": bad.id, "quantity": 1},
            ],
            "order_token": str(uuid.uuid4()),
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)


# ─────────────────────────────────────────────
# place_order_view — cross-shop isolation
# ─────────────────────────────────────────────

class PlaceOrderCrossShopTests(TestCase):

    def setUp(self):
        owner1 = make_verified_user("crossshop1@example.com")
        owner2 = make_verified_user("crossshop2@example.com")
        self.shop1 = make_shop(owner1, "Shop One")
        self.shop2 = make_shop(owner2, "Shop Two")
        cat2 = make_category(self.shop2)
        self.other_shop_product = make_product(cat2, "Not Yours", "99.00", stock=10)
        self.url = reverse("orders:place", args=[self.shop1.slug])

    def test_product_from_different_shop_treated_as_not_found(self):
        resp = self.client.post(
            self.url,
            data=json.dumps({
                "items": [{"id": self.other_shop_product.id, "quantity": 1}],
                "order_token": str(uuid.uuid4()),
            }),
            content_type="application/json",
        )
        data = resp.json()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(data["unavailable_items"][0]["reason"], "not_found")
        self.assertEqual(Order.objects.count(), 0)


# ─────────────────────────────────────────────
# place_order_view — idempotency (HTTP layer)
# ─────────────────────────────────────────────

class PlaceOrderIdempotencyTests(TestCase):

    def setUp(self):
        owner = make_verified_user("idempotency@example.com")
        self.shop = make_shop(owner)
        cat = make_category(self.shop)
        self.product = make_product(cat, "Momo", "150.00", stock=10)
        self.url = reverse("orders:place", args=[self.shop.slug])
        self.token = str(uuid.uuid4())

    def _post(self):
        return self.client.post(
            self.url,
            data=json.dumps({
                "items": [{"id": self.product.id, "quantity": 1}],
                "order_token": self.token,
            }),
            content_type="application/json",
        )

    def test_first_submission_creates_order(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Order.objects.count(), 1)

    def test_repeated_submission_returns_existing_order(self):
        first = self._post().json()
        second = self._post()

        self.assertEqual(second.status_code, 200)
        second_data = second.json()
        self.assertTrue(second_data["duplicate"])
        self.assertEqual(second_data["order"]["order_number"], first["order"]["order_number"])
        self.assertEqual(Order.objects.count(), 1)  # no duplicate row created


# ─────────────────────────────────────────────
# place_order_view — request validation
# ─────────────────────────────────────────────

class PlaceOrderValidationTests(TestCase):

    def setUp(self):
        owner = make_verified_user("placevalid@example.com")
        self.shop = make_shop(owner)
        self.cat = make_category(self.shop)
        self.product = make_product(self.cat, "Momo", "150.00", stock=10)
        self.url = reverse("orders:place", args=[self.shop.slug])

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_empty_cart_rejected(self):
        resp = self._post({"items": [], "order_token": str(uuid.uuid4())})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "cart_empty")

    def test_missing_items_key_rejected(self):
        resp = self._post({"order_token": str(uuid.uuid4())})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "cart_empty")

    def test_malformed_json_rejected(self):
        resp = self.client.post(
            self.url, data="not valid json {{{", content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "bad_json")

    def test_zero_quantity_rejected(self):
        resp = self._post({
            "items": [{"id": self.product.id, "quantity": 0}],
            "order_token": str(uuid.uuid4()),
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "bad_quantity")

    def test_negative_quantity_rejected(self):
        resp = self._post({
            "items": [{"id": self.product.id, "quantity": -1}],
            "order_token": str(uuid.uuid4()),
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "bad_quantity")

    def test_quantity_over_cap_rejected(self):
        resp = self._post({
            "items": [{"id": self.product.id, "quantity": 1000}],
            "order_token": str(uuid.uuid4()),
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "bad_quantity")

    def test_non_numeric_id_rejected(self):
        resp = self._post({
            "items": [{"id": "not-an-id", "quantity": 1}],
            "order_token": str(uuid.uuid4()),
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "bad_item")

    def test_invalid_payment_method_rejected(self):
        resp = self._post({
            "items": [{"id": self.product.id, "quantity": 1}],
            "payment_method": "bitcoin",
            "order_token": str(uuid.uuid4()),
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "invalid_payment_method")

    def test_table_number_truncated_to_max_length(self):
        resp = self._post({
            "items": [{"id": self.product.id, "quantity": 1}],
            "table_number": "X" * 200,
            "order_token": str(uuid.uuid4()),
        })
        self.assertEqual(resp.status_code, 201)
        order = Order.objects.get(shop=self.shop)
        self.assertEqual(len(order.table_number), 50)


# ─────────────────────────────────────────────
# place_order_view — shop status
# ─────────────────────────────────────────────

class PlaceOrderShopStatusTests(TestCase):

    def setUp(self):
        owner = make_verified_user("placeshopstatus@example.com")
        self.shop = make_shop(owner)
        self.shop.is_active = False
        self.shop.save()
        cat = make_category(self.shop)
        self.product = make_product(cat, "Momo", "150.00", stock=10)

    def test_inactive_shop_returns_404(self):
        url = reverse("orders:place", args=[self.shop.slug])
        resp = self.client.post(
            url,
            data=json.dumps({
                "items": [{"id": self.product.id, "quantity": 1}],
                "order_token": str(uuid.uuid4()),
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

        