"""
Automated QA tests for Sajilo Pasal — Categories app (Day 5).

Covers checklist sections:
  3. Categories  — create, edit, delete, verify menu updates correctly
  7. Permissions — User B cannot touch User A's shop's categories
                    (the two-hop ownership check: category -> shop -> owner)
  8. Forms       — empty/invalid/long/duplicate names
  9. URLs        — no 500s, even for cross-owner access attempts

Run with:
    python manage.py test categories -v 2
"""

from django.core.exceptions import ValidationError
from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from shops.models import Shop
from .models import Category


VALID_PASSWORD = "StrongPass123!"


def make_verified_user(email):
    user = User.objects.create_user(email=email, password=VALID_PASSWORD)
    user.is_active = True
    user.is_verified = True
    user.save()
    return user


def make_shop(owner, name="Test Shop"):
    return Shop.objects.create(owner=owner, name=name)


# ─────────────────────────────────────────────
# Model-level uniqueness
# ─────────────────────────────────────────────

class CategoryUniquenessTests(TestCase):

    def setUp(self):
        self.owner_a = make_verified_user("catowner_a@example.com")
        self.owner_b = make_verified_user("catowner_b@example.com")
        self.shop_a = make_shop(self.owner_a, "Shop A")
        self.shop_b = make_shop(self.owner_b, "Shop B")

    def test_same_name_allowed_across_different_shops(self):
        Category.objects.create(shop=self.shop_a, name="Drinks")
        Category.objects.create(shop=self.shop_b, name="Drinks")  # different shop, should succeed
        self.assertEqual(Category.objects.filter(name="Drinks").count(), 2)

    def test_duplicate_name_within_same_shop_rejected_via_clean(self):
        Category.objects.create(shop=self.shop_a, name="Drinks")
        dup = Category(shop=self.shop_a, name="Drinks")
        with self.assertRaises(ValidationError):
            dup.full_clean()

    def test_duplicate_name_case_insensitive_within_same_shop(self):
        """'Drinks' and 'drinks' should be treated as the same category
        name within one shop — clean() uses name__iexact deliberately."""
        Category.objects.create(shop=self.shop_a, name="Drinks")
        dup = Category(shop=self.shop_a, name="drinks")
        with self.assertRaises(ValidationError):
            dup.full_clean()

    def test_editing_a_category_to_its_own_name_does_not_self_collide(self):
        """Saving a category with its OWN existing name (e.g. editing
        the description but not the name) must not trip the duplicate
        check against itself."""
        cat = Category.objects.create(shop=self.shop_a, name="Drinks")
        cat.name = "Drinks"  # unchanged
        cat.full_clean()  # should NOT raise

    def test_db_constraint_is_final_backstop(self):
        """Even bypassing clean() entirely, the DB-level UniqueConstraint
        should still reject an exact-case duplicate at save time."""
        from django.db import IntegrityError, transaction
        Category.objects.create(shop=self.shop_a, name="Drinks")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Category.objects.create(shop=self.shop_a, name="Drinks")


# ─────────────────────────────────────────────
# CRUD via views
# ─────────────────────────────────────────────

class CategoryCRUDTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.owner = make_verified_user("catcrud@example.com")
        self.shop = make_shop(self.owner)
        self.client.login(username=self.owner.email, password=VALID_PASSWORD)

    def test_create_category_via_view(self):
        resp = self.client.post(
            reverse("categories:create", args=[self.shop.slug]),
            {"name": "Momos"},
        )
        self.assertEqual(Category.objects.filter(shop=self.shop, name="Momos").count(), 1)
        self.assertRedirects(resp, reverse("categories:list", args=[self.shop.slug]))

    def test_edit_category_saves_changes(self):
        cat = Category.objects.create(shop=self.shop, name="Drink")
        self.client.post(
            reverse("categories:edit", args=[self.shop.slug, cat.id]),
            {"name": "Drinks"},
        )
        cat.refresh_from_db()
        self.assertEqual(cat.name, "Drinks")

    def test_delete_category_removes_it(self):
        cat = Category.objects.create(shop=self.shop, name="ToDelete")
        self.client.post(reverse("categories:delete", args=[self.shop.slug, cat.id]))
        self.assertFalse(Category.objects.filter(pk=cat.pk).exists())

    def test_delete_via_get_is_rejected(self):
        """Deletion must be POST-only — a GET should not delete anything."""
        cat = Category.objects.create(shop=self.shop, name="ShouldSurvive")
        resp = self.client.get(reverse("categories:delete", args=[self.shop.slug, cat.id]))
        self.assertEqual(resp.status_code, 405)
        self.assertTrue(Category.objects.filter(pk=cat.pk).exists())

    def test_category_list_shows_only_this_shops_categories(self):
        Category.objects.create(shop=self.shop, name="Mine1")
        Category.objects.create(shop=self.shop, name="Mine2")
        other_shop = make_shop(self.owner, "Other Shop")
        Category.objects.create(shop=other_shop, name="NotMine")

        resp = self.client.get(reverse("categories:list", args=[self.shop.slug]))
        names = [c.name for c in resp.context["categories"]]

        self.assertIn("Mine1", names)
        self.assertIn("Mine2", names)
        self.assertNotIn("NotMine", names)

    def test_duplicate_create_via_view_shows_form_error_not_500(self):
        Category.objects.create(shop=self.shop, name="Drinks")
        resp = self.client.post(
            reverse("categories:create", args=[self.shop.slug]),
            {"name": "Drinks"},
        )
        self.assertEqual(resp.status_code, 200)  # re-renders form, not a crash
        self.assertEqual(Category.objects.filter(shop=self.shop, name="Drinks").count(), 1)


# ─────────────────────────────────────────────
# Form validation
# ─────────────────────────────────────────────

class CategoryFormValidationTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.owner = make_verified_user("catform@example.com")
        self.shop = make_shop(self.owner)
        self.client.login(username=self.owner.email, password=VALID_PASSWORD)

    def test_empty_name_rejected(self):
        resp = self.client.post(reverse("categories:create", args=[self.shop.slug]), {"name": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Category.objects.count(), 0)

    def test_single_character_name_rejected(self):
        resp = self.client.post(reverse("categories:create", args=[self.shop.slug]), {"name": "A"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Category.objects.count(), 0)

    def test_very_long_name_does_not_crash(self):
        resp = self.client.post(
            reverse("categories:create", args=[self.shop.slug]),
            {"name": "A" * 5000},
        )
        self.assertIn(resp.status_code, (200, 302))

    def test_whitespace_only_name_rejected(self):
        resp = self.client.post(reverse("categories:create", args=[self.shop.slug]), {"name": "   "})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Category.objects.count(), 0)


# ─────────────────────────────────────────────
# Two-hop ownership permissions (the critical section)
# ─────────────────────────────────────────────

class CategoryOwnershipPermissionTests(TestCase):
    """
    The category's owner isn't stored directly — it's reached through
    shop.owner. These tests confirm that two-hop relationship is
    actually enforced everywhere, not just assumed.
    """

    def setUp(self):
        self.user_a = make_verified_user("catownera@example.com")
        self.user_b = make_verified_user("catownerb@example.com")
        self.shop_a = make_shop(self.user_a, "User A's Shop")
        self.category = Category.objects.create(shop=self.shop_a, name="A's Category")

        self.client_a = Client()
        self.client_a.login(username=self.user_a.email, password=VALID_PASSWORD)

        self.client_b = Client()
        self.client_b.login(username=self.user_b.email, password=VALID_PASSWORD)

    def test_owner_can_view_own_categories(self):
        resp = self.client_a.get(reverse("categories:list", args=[self.shop_a.slug]))
        self.assertEqual(resp.status_code, 200)

    def test_other_user_cannot_view_categories_list_for_shop_they_dont_own(self):
        resp = self.client_b.get(reverse("categories:list", args=[self.shop_a.slug]))
        self.assertEqual(resp.status_code, 404)

    def test_other_user_cannot_create_category_under_shop_they_dont_own(self):
        resp = self.client_b.post(
            reverse("categories:create", args=[self.shop_a.slug]),
            {"name": "Injected Category"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(Category.objects.filter(name="Injected Category").exists())

    def test_other_user_cannot_edit_category_via_direct_url(self):
        resp = self.client_b.post(
            reverse("categories:edit", args=[self.shop_a.slug, self.category.id]),
            {"name": "HIJACKED"},
        )
        self.assertEqual(resp.status_code, 404)
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, "A's Category")

    def test_other_user_cannot_delete_category_via_direct_url(self):
        resp = self.client_b.post(
            reverse("categories:delete", args=[self.shop_a.slug, self.category.id])
        )
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())

    def test_mismatched_shop_and_category_returns_404(self):
        """
        The real two-hop test: a category that genuinely exists, but
        under a DIFFERENT shop than the one named in the URL. This
        catches a bug where ownership is checked on shop OR category
        independently rather than as a single joined query — e.g. if
        the view only checked "is this category_id real and does its
        shop belong to me" without also confirming shop_slug in the
        URL matches that category's actual shop.
        """
        other_shop_same_owner = make_shop(self.user_a, "User A's Other Shop")
        resp = self.client_a.get(
            reverse("categories:edit", args=[other_shop_same_owner.slug, self.category.id])
        )
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_user_redirected_to_login(self):
        anon = Client()
        resp = anon.get(reverse("categories:list", args=[self.shop_a.slug]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp.url)

    def test_nonexistent_category_id_returns_404(self):
        resp = self.client_a.get(
            reverse("categories:edit", args=[self.shop_a.slug, 999999])
        )
        self.assertEqual(resp.status_code, 404)

        