"""
Automated QA tests for Sajilo Pasal — Dashboard app (Day 13).

Covers:
  - login_required on all three views
  - dashboard_home_view: shop switcher shows only the logged-in
    owner's shops, with correct per-shop pending counts
  - shop_orders_view: ownership isolation (single joined queryset —
    another owner's shop 404s), status filtering, pagination
  - order_detail_view: three-hop ownership isolation, including the
    case where the order belongs to a DIFFERENT shop owned by the
    same user (shop_slug in the URL must match the order's own shop)

Day 14 will add status-change tests once that view exists.

Run with:
    python manage.py test dashboard -v 2
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from shops.models import Shop
from categories.models import Category
from products.models import Product
from orders.models import Order, OrderStatus, PaymentStatus, PaymentMethod, OrderPaymentError


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
        category=category, name=name, price=Decimal(price),
        stock_quantity=stock, is_available=True,
    )


def make_order(shop, product, status=OrderStatus.PENDING, quantity=1):
    order = Order.create_from_cart(
        shop=shop,
        cart_items=[{
            "id": str(product.id), "name": product.name,
            "price": str(product.price), "quantity": quantity,
        }],
    )
    if status != OrderStatus.PENDING:
        order.status = status
        order.save(update_fields=["status"])
    return order


# ─────────────────────────────────────────────
# login_required across all three views
# ─────────────────────────────────────────────

class DashboardLoginRequiredTests(TestCase):

    def setUp(self):
        owner = make_verified_user("loginreq@example.com")
        self.shop = make_shop(owner)
        cat = make_category(self.shop)
        product = make_product(cat)
        self.order = make_order(self.shop, product)

    def test_home_redirects_anonymous_user(self):
        resp = self.client.get(reverse("dashboard:home"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.url)

    def test_shop_orders_redirects_anonymous_user(self):
        resp = self.client.get(reverse("dashboard:shop_orders", args=[self.shop.slug]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.url)

    def test_order_detail_redirects_anonymous_user(self):
        resp = self.client.get(
            reverse("dashboard:order_detail", args=[self.shop.slug, self.order.id])
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.url)


# ─────────────────────────────────────────────
# dashboard_home_view — shop switcher
# ─────────────────────────────────────────────

class DashboardHomeTests(TestCase):

    def setUp(self):
        self.owner = make_verified_user("home@example.com")
        self.other_owner = make_verified_user("otherhome@example.com")
        self.client.login(username="home@example.com", password=VALID_PASSWORD)

    def test_shows_only_own_shops(self):
        mine = make_shop(self.owner, "My Shop")
        make_shop(self.other_owner, "Someone Else's Shop")

        resp = self.client.get(reverse("dashboard:home"))
        shops_in_context = list(resp.context["shops"])

        self.assertEqual(len(shops_in_context), 1)
        self.assertEqual(shops_in_context[0].pk, mine.pk)

    def test_multiple_own_shops_all_shown(self):
        make_shop(self.owner, "Shop A")
        make_shop(self.owner, "Shop B")

        resp = self.client.get(reverse("dashboard:home"))
        self.assertEqual(len(resp.context["shops"]), 2)

    def test_no_shops_renders_empty_state(self):
        resp = self.client.get(reverse("dashboard:home"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["shops"]), 0)

    def test_pending_count_correct_per_shop(self):
        shop = make_shop(self.owner, "Busy Shop")
        cat = make_category(shop)
        product = make_product(cat)
        make_order(shop, product, status=OrderStatus.PENDING)
        make_order(shop, product, status=OrderStatus.PENDING)
        make_order(shop, product, status=OrderStatus.READY)  # not pending

        resp = self.client.get(reverse("dashboard:home"))
        shop_in_context = resp.context["shops"][0]
        self.assertEqual(shop_in_context.pending_count, 2)

    def test_pending_count_isolated_per_shop(self):
        shop_a = make_shop(self.owner, "Shop A")
        shop_b = make_shop(self.owner, "Shop B")
        cat_a = make_category(shop_a)
        cat_b = make_category(shop_b)
        product_a = make_product(cat_a)
        product_b = make_product(cat_b)
        make_order(shop_a, product_a, status=OrderStatus.PENDING)
        make_order(shop_b, product_b, status=OrderStatus.PENDING)
        make_order(shop_b, product_b, status=OrderStatus.PENDING)

        resp = self.client.get(reverse("dashboard:home"))
        counts = {s.slug: s.pending_count for s in resp.context["shops"]}
        self.assertEqual(counts[shop_a.slug], 1)
        self.assertEqual(counts[shop_b.slug], 2)

    def test_shop_with_zero_pending_shows_zero(self):
        shop = make_shop(self.owner, "Quiet Shop")
        resp = self.client.get(reverse("dashboard:home"))
        self.assertEqual(resp.context["shops"][0].pending_count, 0)


# ─────────────────────────────────────────────
# shop_orders_view — ownership isolation
# ─────────────────────────────────────────────

class ShopOrdersOwnershipTests(TestCase):

    def setUp(self):
        self.owner = make_verified_user("ownership@example.com")
        self.other_owner = make_verified_user("otherownership@example.com")
        self.shop = make_shop(self.owner, "My Shop")
        self.other_shop = make_shop(self.other_owner, "Not Mine")
        self.client.login(username="ownership@example.com", password=VALID_PASSWORD)

    def test_own_shop_returns_200(self):
        resp = self.client.get(reverse("dashboard:shop_orders", args=[self.shop.slug]))
        self.assertEqual(resp.status_code, 200)

    def test_other_owners_shop_returns_404(self):
        resp = self.client.get(reverse("dashboard:shop_orders", args=[self.other_shop.slug]))
        self.assertEqual(resp.status_code, 404)

    def test_nonexistent_shop_slug_returns_404(self):
        resp = self.client.get(reverse("dashboard:shop_orders", args=["does-not-exist"]))
        self.assertEqual(resp.status_code, 404)


# ─────────────────────────────────────────────
# shop_orders_view — order scoping, filtering, pagination
# ─────────────────────────────────────────────

class ShopOrdersListingTests(TestCase):

    def setUp(self):
        self.owner = make_verified_user("listing@example.com")
        self.shop = make_shop(self.owner, "Listing Shop")
        self.other_shop = make_shop(self.owner, "Other Shop")  # same owner, different shop
        self.cat = make_category(self.shop)
        self.product = make_product(self.cat)
        other_cat = make_category(self.other_shop)
        self.other_product = make_product(other_cat)
        self.client.login(username="listing@example.com", password=VALID_PASSWORD)
        self.url = reverse("dashboard:shop_orders", args=[self.shop.slug])

    def test_only_this_shops_orders_are_listed(self):
        mine = make_order(self.shop, self.product)
        make_order(self.other_shop, self.other_product)  # different shop, same owner

        resp = self.client.get(self.url)
        order_ids = [o.id for o in resp.context["page_obj"]]
        self.assertEqual(order_ids, [mine.id])

    def test_status_filter_returns_only_matching_orders(self):
        pending = make_order(self.shop, self.product, status=OrderStatus.PENDING)
        make_order(self.shop, self.product, status=OrderStatus.READY)

        resp = self.client.get(self.url, {"status": "pending"})
        order_ids = [o.id for o in resp.context["page_obj"]]
        self.assertEqual(order_ids, [pending.id])

    def test_invalid_status_filter_falls_back_to_all(self):
        make_order(self.shop, self.product, status=OrderStatus.PENDING)
        make_order(self.shop, self.product, status=OrderStatus.READY)

        resp = self.client.get(self.url, {"status": "not-a-real-status"})
        self.assertEqual(len(resp.context["page_obj"]), 2)

    def test_total_orders_count_ignores_active_filter(self):
        make_order(self.shop, self.product, status=OrderStatus.PENDING)
        make_order(self.shop, self.product, status=OrderStatus.READY)

        resp = self.client.get(self.url, {"status": "pending"})
        self.assertEqual(resp.context["total_orders"], 2)

    def test_status_tabs_have_correct_counts(self):
        make_order(self.shop, self.product, status=OrderStatus.PENDING)
        make_order(self.shop, self.product, status=OrderStatus.PENDING)
        make_order(self.shop, self.product, status=OrderStatus.READY)

        resp = self.client.get(self.url)
        tabs = {tab["value"]: tab["count"] for tab in resp.context["status_tabs"]}
        self.assertEqual(tabs[OrderStatus.PENDING], 2)
        self.assertEqual(tabs[OrderStatus.READY], 1)
        self.assertEqual(tabs[OrderStatus.PREPARING], 0)

    def test_item_count_annotation_correct(self):
        cat2_product = make_product(self.cat, "Second Item", "30.00")
        order = Order.create_from_cart(
            shop=self.shop,
            cart_items=[
                {"id": str(self.product.id), "name": self.product.name,
                 "price": str(self.product.price), "quantity": 1},
                {"id": str(cat2_product.id), "name": cat2_product.name,
                 "price": str(cat2_product.price), "quantity": 1},
            ],
        )
        resp = self.client.get(self.url)
        listed = resp.context["page_obj"][0]
        self.assertEqual(listed.item_count, 2)

    def test_orders_paginate(self):
        for _ in range(25):
            make_order(self.shop, self.product)

        resp = self.client.get(self.url)
        self.assertEqual(len(resp.context["page_obj"]), 20)  # ORDERS_PER_PAGE
        self.assertTrue(resp.context["page_obj"].has_other_pages())

    def test_second_page_returns_remainder(self):
        for _ in range(25):
            make_order(self.shop, self.product)

        resp = self.client.get(self.url, {"page": 2})
        self.assertEqual(len(resp.context["page_obj"]), 5)

    def test_orders_ordered_most_recent_first(self):
        first = make_order(self.shop, self.product)
        second = make_order(self.shop, self.product)

        resp = self.client.get(self.url)
        order_ids = [o.id for o in resp.context["page_obj"]]
        self.assertEqual(order_ids, [second.id, first.id])


# ─────────────────────────────────────────────
# order_detail_view — ownership isolation (three-hop)
# ─────────────────────────────────────────────

class OrderDetailTests(TestCase):

    def setUp(self):
        self.owner = make_verified_user("detail@example.com")
        self.other_owner = make_verified_user("otherdetail@example.com")

        self.shop = make_shop(self.owner, "My Shop")
        self.second_shop = make_shop(self.owner, "My Other Shop")  # same owner
        self.other_shop = make_shop(self.other_owner, "Not Mine")

        cat = make_category(self.shop)
        self.product = make_product(cat, "Momo", "150.00")
        self.order = make_order(self.shop, self.product, quantity=2)

        self.client.login(username="detail@example.com", password=VALID_PASSWORD)

    def test_own_order_returns_200_with_correct_data(self):
        url = reverse("dashboard:order_detail", args=[self.shop.slug, self.order.id])
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["order"].id, self.order.id)
        item = resp.context["order"].items.first()
        self.assertEqual(item.product_name, "Momo")
        self.assertEqual(item.quantity, 2)

    def test_order_belonging_to_other_owner_returns_404(self):
        url = reverse("dashboard:order_detail", args=[self.other_shop.slug, self.order.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_order_belonging_to_different_own_shop_returns_404(self):
        """
        Critical case: the order exists and IS owned by this user, but
        through a DIFFERENT shop than the one in the URL. The shop_slug
        must match the order's own shop, not just any shop owned by
        this user — otherwise an owner with two shops could open shop
        A's orders through shop B's URL.
        """
        url = reverse("dashboard:order_detail", args=[self.second_shop.slug, self.order.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_nonexistent_order_id_returns_404(self):
        url = reverse("dashboard:order_detail", args=[self.shop.slug, 999999])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)



class UpdateOrderStatusViewTests(TestCase):
 
    def setUp(self):
        self.owner = make_verified_user("statusupdate@example.com")
        self.other_owner = make_verified_user("otherstatusupdate@example.com")
 
        self.shop = make_shop(self.owner, "My Shop")
        self.second_shop = make_shop(self.owner, "My Other Shop")
        self.other_shop = make_shop(self.other_owner, "Not Mine")
 
        self.cat = make_category(self.shop)
        self.product = make_product(self.cat, "Momo", "150.00", stock=10)
        self.order = make_order(self.shop, self.product, quantity=3)
 
        self.url = reverse(
            "dashboard:update_order_status", args=[self.shop.slug, self.order.id]
        )
        self.client.login(username="statusupdate@example.com", password=VALID_PASSWORD)
 
    # ── Auth / ownership ──
 
    def test_anonymous_user_redirected_to_login(self):
        self.client.logout()
        resp = self.client.post(self.url, {"new_status": "preparing"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.url)
 
    def test_get_request_not_allowed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
 
    def test_other_owners_order_returns_404(self):
        url = reverse(
            "dashboard:update_order_status", args=[self.other_shop.slug, self.order.id]
        )
        resp = self.client.post(url, {"new_status": "preparing"})
        self.assertEqual(resp.status_code, 404)
 
    def test_order_via_different_own_shop_returns_404(self):
        """Same trap as order_detail_view: the shop in the URL must be
        the order's actual shop, not just any shop this user owns."""
        url = reverse(
            "dashboard:update_order_status", args=[self.second_shop.slug, self.order.id]
        )
        resp = self.client.post(url, {"new_status": "preparing"})
        self.assertEqual(resp.status_code, 404)
 
    # ── Valid transitions ──
 
    def test_valid_transition_updates_status(self):
        self.client.post(self.url, {"new_status": "preparing"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PREPARING)
 
    def test_valid_transition_decrements_stock(self):
        self.client.post(self.url, {"new_status": "preparing"})
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 7)
 
    def test_valid_transition_redirects_to_order_detail(self):
        resp = self.client.post(self.url, {"new_status": "preparing"})
        expected = reverse("dashboard:order_detail", args=[self.shop.slug, self.order.id])
        self.assertRedirects(resp, expected)
 
    def test_valid_transition_shows_success_message(self):
        resp = self.client.post(self.url, {"new_status": "preparing"}, follow=True)
        messages = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("Preparing" in m for m in messages))
 
    def test_cancel_after_preparing_restores_stock(self):
        self.client.post(self.url, {"new_status": "preparing"})
        self.client.post(self.url, {"new_status": "cancelled"})
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)
 
    # ── Invalid input ──
 
    def test_missing_new_status_shows_error(self):
        resp = self.client.post(self.url, {}, follow=True)
        messages = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("not a valid" in m for m in messages))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)
 
    def test_garbage_new_status_shows_error(self):
        resp = self.client.post(self.url, {"new_status": "on-fire"}, follow=True)
        messages = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("not a valid" in m for m in messages))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)
 
    def test_illegal_transition_shows_error_and_does_not_change_status(self):
        """pending -> ready skips a step and must be rejected."""
        resp = self.client.post(self.url, {"new_status": "ready"}, follow=True)
        messages = [m.message for m in resp.context["messages"]]
        self.assertTrue(len(messages) >= 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)
 
    def test_illegal_transition_does_not_touch_stock(self):
        self.client.post(self.url, {"new_status": "ready"})  # rejected
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)
 
    def test_transition_from_terminal_ready_state_rejected(self):
        self.client.post(self.url, {"new_status": "preparing"})
        self.client.post(self.url, {"new_status": "ready"})
        resp = self.client.post(self.url, {"new_status": "cancelled"}, follow=True)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.READY)  # unchanged


 
class UpdateOrderPaymentViewTests(TestCase):
 
    def setUp(self):
        self.owner = make_verified_user("paymentview@example.com")
        self.other_owner = make_verified_user("otherpaymentview@example.com")
 
        self.shop = make_shop(self.owner, "My Shop")
        self.second_shop = make_shop(self.owner, "My Other Shop")
        self.other_shop = make_shop(self.other_owner, "Not Mine")
 
        self.cat = make_category(self.shop)
        self.product = make_product(self.cat, "Momo", "150.00", stock=10)
        self.order = make_order(self.shop, self.product)
 
        self.url = reverse(
            "dashboard:update_order_payment", args=[self.shop.slug, self.order.id]
        )
        self.client.login(username="paymentview@example.com", password=VALID_PASSWORD)
 
    # ── Auth / ownership ──
 
    def test_anonymous_user_redirected_to_login(self):
        self.client.logout()
        resp = self.client.post(self.url, {"payment_status": "paid"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.url)
 
    def test_get_request_not_allowed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
 
    def test_other_owners_order_returns_404(self):
        url = reverse(
            "dashboard:update_order_payment", args=[self.other_shop.slug, self.order.id]
        )
        resp = self.client.post(url, {"payment_status": "paid"})
        self.assertEqual(resp.status_code, 404)
 
    def test_order_via_different_own_shop_returns_404(self):
        url = reverse(
            "dashboard:update_order_payment", args=[self.second_shop.slug, self.order.id]
        )
        resp = self.client.post(url, {"payment_status": "paid"})
        self.assertEqual(resp.status_code, 404)
 
    # ── Valid updates ──
 
    def test_mark_paid_updates_status(self):
        self.client.post(self.url, {"payment_status": "paid"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)
 
    def test_mark_unpaid_after_paid(self):
        self.client.post(self.url, {"payment_status": "paid"})
        self.client.post(self.url, {"payment_status": "unpaid"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.UNPAID)
 
    def test_update_payment_method(self):
        self.client.post(self.url, {"payment_method": "fonepay"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_method, PaymentMethod.FONEPAY)
 
    def test_valid_update_redirects_to_order_detail(self):
        resp = self.client.post(self.url, {"payment_status": "paid"})
        expected = reverse("dashboard:order_detail", args=[self.shop.slug, self.order.id])
        self.assertRedirects(resp, expected)
 
    def test_valid_update_shows_success_message(self):
        resp = self.client.post(self.url, {"payment_status": "paid"}, follow=True)
        messages = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("Paid" in m for m in messages))
 
    # ── Invalid input ──
 
    def test_no_fields_submitted_shows_error(self):
        resp = self.client.post(self.url, {}, follow=True)
        messages = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("No payment change" in m for m in messages))
 
    def test_invalid_payment_status_shows_error(self):
        resp = self.client.post(self.url, {"payment_status": "crypto"}, follow=True)
        messages = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("not a valid payment status" in m for m in messages))
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.UNPAID)
 
    def test_invalid_payment_method_shows_error(self):
        resp = self.client.post(self.url, {"payment_method": "crypto"}, follow=True)
        messages = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("not a valid payment method" in m for m in messages))
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_method, PaymentMethod.CASH)
 
    # ── Cancelled-order rules ──
 
    def test_payment_status_change_blocked_on_cancelled_order(self):
        cancelled_order = make_order(self.shop, self.product, status=OrderStatus.CANCELLED)
        url = reverse(
            "dashboard:update_order_payment", args=[self.shop.slug, cancelled_order.id]
        )
        resp = self.client.post(url, {"payment_status": "paid"}, follow=True)
        messages = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("cancelled" in m.lower() for m in messages))
        cancelled_order.refresh_from_db()
        self.assertEqual(cancelled_order.payment_status, PaymentStatus.UNPAID)
 
    def test_payment_method_change_still_allowed_on_cancelled_order(self):
        cancelled_order = make_order(self.shop, self.product, status=OrderStatus.CANCELLED)
        url = reverse(
            "dashboard:update_order_payment", args=[self.shop.slug, cancelled_order.id]
        )
        resp = self.client.post(url, {"payment_method": "fonepay"})
        cancelled_order.refresh_from_db()
        self.assertEqual(cancelled_order.payment_method, PaymentMethod.FONEPAY)

        