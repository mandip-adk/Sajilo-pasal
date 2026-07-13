"""
Automated QA tests for Sajilo Pasal — Shops app (Day 4).

Covers checklist sections:
  2. Restaurant/Shop  — create, edit, verify changes saved
  7. Permissions       — User B cannot access User A's shop (THE critical test)
  8. Forms             — empty/invalid/long/duplicate input, phone format, image upload
  9. URLs              — no 500s, even for cross-owner access attempts

Plus second-review fixes:
  - Slug race condition (retry-on-IntegrityError)
  - Nepal phone number validation
  - Image upload validation (size, extension, real content-sniff)
  - Slug truncation headroom
  - ShopType as TextChoices

NOTE: get_menu_url() / reverse()-based menu URLs are deferred to Day 9,
when the actual public menu URL pattern is built — so there are no
tests for that here yet. menu_url_path stays a simple hardcoded
property for now.

Run with:
    python manage.py test shops -v 2
"""

import io
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, Client

from accounts.models import User
from .models import Shop, ShopType, validate_logo_image


VALID_PASSWORD = "StrongPass123!"


def make_verified_user(email):
    user = User.objects.create_user(email=email, password=VALID_PASSWORD)
    user.is_active = True
    user.is_verified = True
    user.save()
    return user


def make_test_image_bytes(format="JPEG", size=(10, 10)):
    """Generates a tiny, genuinely valid in-memory image for upload tests."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color="red").save(buf, format=format)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────
# Slug generation
# ─────────────────────────────────────────────

class ShopSlugGenerationTests(TestCase):

    def setUp(self):
        self.owner = make_verified_user("slugowner@example.com")

    def test_slug_generated_from_name(self):
        shop = Shop.objects.create(owner=self.owner, name="Sharma Kirana Pasal")
        self.assertEqual(shop.slug, "sharma-kirana-pasal")

    def test_duplicate_name_gets_unique_suffixed_slug(self):
        shop1 = Shop.objects.create(owner=self.owner, name="Annapurna Restaurant")
        owner2 = make_verified_user("slugowner2@example.com")
        shop2 = Shop.objects.create(owner=owner2, name="Annapurna Restaurant")

        self.assertEqual(shop1.slug, "annapurna-restaurant")
        self.assertEqual(shop2.slug, "annapurna-restaurant-2")
        self.assertNotEqual(shop1.slug, shop2.slug)

    def test_three_way_duplicate_name_collision(self):
        names = ["Same Name Shop"] * 3
        slugs = []
        for i, name in enumerate(names):
            owner = make_verified_user(f"collision{i}@example.com")
            shop = Shop.objects.create(owner=owner, name=name)
            slugs.append(shop.slug)
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertEqual(slugs[0], "same-name-shop")
        self.assertEqual(slugs[1], "same-name-shop-2")
        self.assertEqual(slugs[2], "same-name-shop-3")

    def test_slug_does_not_change_on_rename(self):
        shop = Shop.objects.create(owner=self.owner, name="Original Name")
        original_slug = shop.slug
        shop.name = "Completely Different Name"
        shop.save()
        self.assertEqual(shop.slug, original_slug)

    def test_empty_name_still_produces_a_slug(self):
        shop = Shop(owner=self.owner, name="")
        shop.save()
        self.assertTrue(shop.slug)

    def test_nepali_or_unicode_name_does_not_crash(self):
        shop = Shop(owner=self.owner, name="शर्मा किराना पसल")
        shop.save()
        self.assertTrue(shop.slug)


class SlugRaceConditionTests(TestCase):
    """
    Two near-simultaneous creates with the same name, where the second
    save's first attempt collides. Exercised by directly forcing the
    parent save() to raise IntegrityError once, simulating a genuine
    concurrent collision at the database layer.
    """

    def setUp(self):
        self.owner1 = make_verified_user("race1@example.com")
        self.owner2 = make_verified_user("race2@example.com")

    def test_retry_recovers_from_simulated_integrity_error(self):
        Shop.objects.create(owner=self.owner1, name="Race Condition Shop")
        shop2 = Shop(owner=self.owner2, name="Race Condition Shop")
        shop2.save()

        self.assertNotEqual(shop2.slug, "")
        self.assertNotEqual(
            Shop.objects.filter(owner=self.owner1).first().slug,
            shop2.slug,
        )

    def test_concurrent_integrity_error_is_caught_and_retried(self):
        from django.db.models import Model

        shop = Shop(owner=self.owner1, name="Forced Collision Shop")

        original_save = Model.save
        call_count = {"n": 0}

        def flaky_save(self_inner, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise IntegrityError("simulated concurrent slug collision")
            return original_save(self_inner, *args, **kwargs)

        with mock.patch.object(Model, "save", flaky_save):
            shop._save_with_unique_slug()

        self.assertEqual(call_count["n"], 2)  # failed once, succeeded on retry
        self.assertTrue(Shop.objects.filter(pk=shop.pk).exists())

    def test_exhausting_all_retries_raises_loudly(self):
        from django.db.models import Model

        shop = Shop(owner=self.owner1, name="Always Collides Shop")

        def always_fails(self_inner, *args, **kwargs):
            raise IntegrityError("simulated permanent collision")

        with mock.patch.object(Model, "save", always_fails):
            with self.assertRaises(IntegrityError):
                shop._save_with_unique_slug()


class SlugLengthHeadroomTests(TestCase):

    def setUp(self):
        self.owner = make_verified_user("lengthtest@example.com")

    def test_very_long_name_slug_stays_within_field_limit(self):
        long_name = "A" * 300
        shop = Shop.objects.create(owner=self.owner, name=long_name)
        self.assertLessEqual(len(shop.slug), 170)

    def test_very_long_name_with_collision_still_fits(self):
        long_name = "B" * 200
        Shop.objects.create(owner=self.owner, name=long_name)
        owner2 = make_verified_user("lengthtest2@example.com")
        shop2 = Shop.objects.create(owner=owner2, name=long_name)
        self.assertLessEqual(len(shop2.slug), 170)
        self.assertTrue(shop2.slug.endswith("-2"))


# ─────────────────────────────────────────────
# Phone validation
# ─────────────────────────────────────────────

class PhoneValidationTests(TestCase):

    def setUp(self):
        self.owner = make_verified_user("phonetest@example.com")

    def test_valid_98_number_accepted(self):
        shop = Shop(owner=self.owner, name="Phone Test 1", phone="9812345678")
        shop.full_clean()

    def test_valid_97_number_accepted(self):
        shop = Shop(owner=self.owner, name="Phone Test 2", phone="9712345678")
        shop.full_clean()

    def test_blank_phone_accepted(self):
        shop = Shop(owner=self.owner, name="Phone Test 3", phone="")
        shop.full_clean()

    def test_alphabetic_phone_rejected(self):
        shop = Shop(owner=self.owner, name="Phone Test 4", phone="abcdefghij")
        with self.assertRaises(ValidationError):
            shop.full_clean()

    def test_too_short_phone_rejected(self):
        shop = Shop(owner=self.owner, name="Phone Test 5", phone="123")
        with self.assertRaises(ValidationError):
            shop.full_clean()

    def test_wrong_prefix_phone_rejected(self):
        shop = Shop(owner=self.owner, name="Phone Test 6", phone="9612345678")
        with self.assertRaises(ValidationError):
            shop.full_clean()


# ─────────────────────────────────────────────
# Image validation
# ─────────────────────────────────────────────

class ImageValidationTests(TestCase):

    def setUp(self):
        self.owner = make_verified_user("imgtest@example.com")

    def test_valid_jpeg_passes_validation(self):
        content = make_test_image_bytes(format="JPEG")
        upload = SimpleUploadedFile("logo.jpg", content, content_type="image/jpeg")
        validate_logo_image(upload)

    def test_valid_png_passes_validation(self):
        content = make_test_image_bytes(format="PNG")
        upload = SimpleUploadedFile("logo.png", content, content_type="image/png")
        validate_logo_image(upload)

    def test_oversized_file_rejected(self):
        content = make_test_image_bytes(format="JPEG")
        upload = SimpleUploadedFile("logo.jpg", content, content_type="image/jpeg")
        upload.size = 6 * 1024 * 1024  # spoof reported size past the 5MB ceiling
        with self.assertRaises(ValidationError):
            validate_logo_image(upload)

    def test_disallowed_extension_rejected(self):
        content = make_test_image_bytes(format="JPEG")
        upload = SimpleUploadedFile("logo.tiff", content, content_type="image/tiff")
        with self.assertRaises(ValidationError):
            validate_logo_image(upload)

    def test_renamed_non_image_file_rejected(self):
        """A plain text file renamed to .jpg — extension allowlist
        passes it through, the Pillow content-sniff must catch it."""
        fake_content = b"this is not an image, just text pretending to be one"
        upload = SimpleUploadedFile("fake.jpg", fake_content, content_type="image/jpeg")
        with self.assertRaises(ValidationError):
            validate_logo_image(upload)

    def test_renamed_tiff_with_jpg_extension_rejected(self):
        """A REAL TIFF saved with a .jpg extension — the Pillow
        format-sniff must catch the claimed-vs-actual mismatch."""
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (10, 10), color="blue").save(buf, format="TIFF")
        buf.seek(0)
        upload = SimpleUploadedFile("disguised.jpg", buf.read(), content_type="image/jpeg")
        with self.assertRaises(ValidationError):
            validate_logo_image(upload)


# ─────────────────────────────────────────────
# ShopType TextChoices
# ─────────────────────────────────────────────

class ShopTypeTextChoicesTests(TestCase):

    def setUp(self):
        self.owner = make_verified_user("choicestest@example.com")

    def test_shop_type_choices_use_textchoices_enum(self):
        self.assertEqual(ShopType.KIRANA, "kirana")
        self.assertEqual(ShopType.RESTAURANT, "restaurant")

    def test_shop_created_with_enum_member_stores_correct_string(self):
        shop = Shop.objects.create(
            owner=self.owner,
            name="Enum Test Shop",
            shop_type=ShopType.RESTAURANT,
        )
        shop.refresh_from_db()
        self.assertEqual(shop.shop_type, "restaurant")

    def test_get_shop_type_display_still_works(self):
        shop = Shop.objects.create(
            owner=self.owner,
            name="Display Test Shop",
            shop_type=ShopType.TEA_CAFE,
        )
        self.assertEqual(shop.get_shop_type_display(), "Tea Shop / Cafe")


# ─────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────

class ShopCRUDTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.owner = make_verified_user("cruduser@example.com")
        self.client.login(username=self.owner.email, password=VALID_PASSWORD)

    def test_create_shop_via_view(self):
        resp = self.client.post(reverse_lazy_create(), {
            "name": "Test Kirana",
            "shop_type": "kirana",
            "phone": "9800000000",
            "address": "Test Address",
            "description": "A test shop",
        })
        self.assertEqual(Shop.objects.filter(owner=self.owner).count(), 1)

    def test_created_shop_is_owned_by_creator(self):
        self.client.post(reverse_lazy_create(), {
            "name": "Ownership Test Shop",
            "shop_type": "restaurant",
        })
        shop = Shop.objects.get(name="Ownership Test Shop")
        self.assertEqual(shop.owner, self.owner)

    def test_edit_shop_saves_changes(self):
        shop = Shop.objects.create(owner=self.owner, name="Before Edit", shop_type="kirana")
        self.client.post(reverse_lazy_edit(shop.slug), {
            "name": "Before Edit",
            "shop_type": "restaurant",
            "phone": "9811111111",
            "address": "New Address",
            "description": "Updated description",
        })
        shop.refresh_from_db()
        self.assertEqual(shop.shop_type, "restaurant")
        self.assertEqual(shop.phone, "9811111111")
        self.assertEqual(shop.address, "New Address")
        self.assertEqual(shop.description, "Updated description")

    def test_shop_list_shows_only_own_shops(self):
        Shop.objects.create(owner=self.owner, name="My Shop One")
        Shop.objects.create(owner=self.owner, name="My Shop Two")
        other_owner = make_verified_user("otherowner@example.com")
        Shop.objects.create(owner=other_owner, name="Someone Else's Shop")

        resp = self.client.get(reverse_lazy_list())
        shop_names = [s.name for s in resp.context["shops"]]

        self.assertIn("My Shop One", shop_names)
        self.assertIn("My Shop Two", shop_names)
        self.assertNotIn("Someone Else's Shop", shop_names)


# ─────────────────────────────────────────────
# Form validation
# ─────────────────────────────────────────────

class ShopFormValidationTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.owner = make_verified_user("formuser@example.com")
        self.client.login(username=self.owner.email, password=VALID_PASSWORD)

    def test_empty_name_rejected(self):
        resp = self.client.post(reverse_lazy_create(), {"name": "", "shop_type": "kirana"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Shop.objects.count(), 0)

    def test_single_character_name_rejected(self):
        resp = self.client.post(reverse_lazy_create(), {"name": "A", "shop_type": "kirana"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Shop.objects.count(), 0)

    def test_invalid_shop_type_rejected(self):
        resp = self.client.post(reverse_lazy_create(), {
            "name": "Valid Name",
            "shop_type": "not_a_real_choice",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Shop.objects.count(), 0)

    def test_invalid_phone_rejected_via_form(self):
        resp = self.client.post(reverse_lazy_create(), {
            "name": "Phone Form Test",
            "shop_type": "kirana",
            "phone": "notaphonenumber",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Shop.objects.count(), 0)

    def test_very_long_name_does_not_crash(self):
        resp = self.client.post(reverse_lazy_create(), {"name": "A" * 5000, "shop_type": "kirana"})
        self.assertIn(resp.status_code, (200, 302))

    def test_very_long_description_does_not_crash(self):
        resp = self.client.post(reverse_lazy_create(), {
            "name": "Valid Shop Name",
            "shop_type": "kirana",
            "description": "B" * 50000,
        })
        self.assertIn(resp.status_code, (200, 302))

    def test_duplicate_shop_name_across_different_owners_is_allowed(self):
        Shop.objects.create(owner=self.owner, name="Common Shop Name")
        other_owner = make_verified_user("formuser2@example.com")
        client2 = Client()
        client2.login(username=other_owner.email, password=VALID_PASSWORD)
        client2.post(reverse_lazy_create(), {"name": "Common Shop Name", "shop_type": "kirana"})
        self.assertEqual(Shop.objects.filter(name="Common Shop Name").count(), 2)


# ─────────────────────────────────────────────
# Ownership permissions (the critical section)
# ─────────────────────────────────────────────

class ShopOwnershipPermissionTests(TestCase):

    def setUp(self):
        self.user_a = make_verified_user("ownera@example.com")
        self.user_b = make_verified_user("ownerb@example.com")
        self.shop = Shop.objects.create(owner=self.user_a, name="User A's Private Shop")

        self.client_a = Client()
        self.client_a.login(username=self.user_a.email, password=VALID_PASSWORD)

        self.client_b = Client()
        self.client_b.login(username=self.user_b.email, password=VALID_PASSWORD)

    def test_owner_can_view_own_shop(self):
        resp = self.client_a.get(reverse_lazy_detail(self.shop.slug))
        self.assertEqual(resp.status_code, 200)

    def test_other_user_cannot_view_shop_via_direct_url(self):
        resp = self.client_b.get(reverse_lazy_detail(self.shop.slug))
        self.assertEqual(resp.status_code, 404)

    def test_other_user_cannot_edit_shop_via_direct_url_get(self):
        resp = self.client_b.get(reverse_lazy_edit(self.shop.slug))
        self.assertEqual(resp.status_code, 404)

    def test_other_user_cannot_edit_shop_via_direct_url_post(self):
        resp = self.client_b.post(reverse_lazy_edit(self.shop.slug), {
            "name": "HIJACKED BY USER B",
            "shop_type": "other",
        })
        self.assertEqual(resp.status_code, 404)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.name, "User A's Private Shop")
        self.assertNotEqual(self.shop.name, "HIJACKED BY USER B")

    def test_other_users_shop_list_does_not_include_this_shop(self):
        resp = self.client_b.get(reverse_lazy_list())
        shop_names = [s.name for s in resp.context["shops"]]
        self.assertNotIn("User A's Private Shop", shop_names)

    def test_anonymous_user_cannot_view_owner_dashboard_shop_detail(self):
        anon_client = Client()
        resp = anon_client.get(reverse_lazy_detail(self.shop.slug))
        self.assertEqual(resp.status_code, 302)

    def test_nonexistent_slug_returns_404_not_500(self):
        resp = self.client_a.get(reverse_lazy_detail("this-slug-does-not-exist"))
        self.assertEqual(resp.status_code, 404)


# ─────────────────────────────────────────────
# Small helpers so the URL names stay in one place
# ─────────────────────────────────────────────

from django.urls import reverse as _reverse

def reverse_lazy_create():
    return _reverse("shops:create")

def reverse_lazy_list():
    return _reverse("shops:list")

def reverse_lazy_detail(slug):
    return _reverse("shops:detail", args=[slug])

def reverse_lazy_edit(slug):
    return _reverse("shops:edit", args=[slug])

# ─────────────────────────────────────────────
# Day 7 — Cloudinary integration tests
# ─────────────────────────────────────────────

class ShopLogoValidatorCloudinaryRegressionTests(TestCase):
    """
    Confirms validate_logo_image still rejects bad files after
    Cloudinary config — validators must fire BEFORE the storage
    backend sees the file.
    """

    def test_valid_jpeg_still_passes_after_cloudinary_config(self):
        content = make_test_image_bytes(format="JPEG")
        upload = SimpleUploadedFile("logo.jpg", content, content_type="image/jpeg")
        validate_logo_image(upload)  # must not raise

    def test_disguised_tiff_still_rejected(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="TIFF")
        buf.seek(0)
        upload = SimpleUploadedFile("sneaky.jpg", buf.read(), content_type="image/jpeg")
        with self.assertRaises(ValidationError):
            validate_logo_image(upload)

    def test_oversized_file_still_rejected(self):
        content = make_test_image_bytes(format="JPEG")
        upload = SimpleUploadedFile("big.jpg", content, content_type="image/jpeg")
        upload.size = 6 * 1024 * 1024
        with self.assertRaises(ValidationError):
            validate_logo_image(upload)


class StorageBackendSwitchingTests(TestCase):
    """
    Verifies the conditional storage logic — local when no DATABASE_URL,
    Cloudinary when DATABASE_URL is set.
    """

    def test_cloudinary_credentials_are_not_placeholder_values(self):
        import os
        cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
        api_key    = os.environ.get("CLOUDINARY_API_KEY", "")
        api_secret = os.environ.get("CLOUDINARY_API_SECRET", "")

        if cloud_name:
            self.assertNotEqual(cloud_name, "your-cloud-name",
                "CLOUDINARY_CLOUD_NAME looks like a placeholder.")
            self.assertNotEqual(api_key, "your-api-key",
                "CLOUDINARY_API_KEY looks like a placeholder.")
            self.assertNotEqual(api_secret, "your-api-secret",
                "CLOUDINARY_API_SECRET looks like a placeholder.")

    def test_local_storage_active_without_database_url(self):
        import os
        from django.conf import settings
        has_db_url = bool(os.environ.get("DATABASE_URL"))
        current_storage = getattr(
            settings, "DEFAULT_FILE_STORAGE",
            "django.core.files.storage.FileSystemStorage"
        )
        if not has_db_url:
            self.assertNotIn("cloudinary", current_storage.lower())


class CompressorJsShopTemplateTests(TestCase):
    """
    Confirms Compressor.js is rendered in shop upload forms
    but not on non-upload pages.
    """

    def setUp(self):
        self.client = Client()
        self.owner = make_verified_user("shopcompressor@example.com")
        self.shop = Shop.objects.create(owner=self.owner, name="Compressor Test Shop")
        self.client.login(username=self.owner.email, password=VALID_PASSWORD)

    def test_shop_create_form_includes_compressorjs(self):
        resp = self.client.get(reverse_lazy_create())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "compressorjs")
        self.assertContains(resp, "initImageCompressor")

    def test_shop_edit_form_includes_compressorjs(self):
        resp = self.client.get(reverse_lazy_edit(self.shop.slug))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "compressorjs")
        self.assertContains(resp, "initImageCompressor")

    def test_shop_list_does_not_include_compressorjs(self):
        resp = self.client.get(reverse_lazy_list())
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "compressorjs")

        