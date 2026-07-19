"""
Day 8 — QR code generation tests.
Append these classes to qr_manager/tests.py (create the file if it
doesn't exist yet).

Run with:
    python manage.py test qr_manager -v 2
"""

import io

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from shops.models import Shop


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
# Short redirect view
# ─────────────────────────────────────────────

class ShortRedirectTests(TestCase):
    """
    /s/<id>/  →  redirects to the public menu.
    No login required — customers scanning QR codes aren't authenticated.
    """

    def setUp(self):
        self.owner = make_verified_user("qrredirect@example.com")
        self.shop = make_shop(self.owner)

    def test_active_shop_redirects_to_menu(self):
        resp = self.client.get(reverse("qr_manager:redirect", args=[self.shop.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(self.shop.slug, resp.url)

    def test_inactive_shop_returns_404(self):
        self.shop.is_active = False
        self.shop.save()
        resp = self.client.get(reverse("qr_manager:redirect", args=[self.shop.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_nonexistent_shop_id_returns_404(self):
        resp = self.client.get(reverse("qr_manager:redirect", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_user_can_follow_redirect(self):
        """Customers scanning QR codes are not logged in — the
        redirect must work without authentication."""
        anon = Client()
        resp = anon.get(reverse("qr_manager:redirect", args=[self.shop.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_redirect_encodes_numeric_id_not_slug(self):
        """
        Core SDD requirement: the short URL uses the numeric PK,
        not the slug. Confirm the redirect URL pattern is /s/<int>/
        and actually contains the shop's integer PK.
        """
        url = reverse("qr_manager:redirect", args=[self.shop.pk])
        self.assertIn(str(self.shop.pk), url)
        self.assertNotIn(self.shop.slug, url)


# ─────────────────────────────────────────────
# QR PNG generation
# ─────────────────────────────────────────────

class QRPNGTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.owner = make_verified_user("qrpng@example.com")
        self.shop = make_shop(self.owner)
        self.client.login(username=self.owner.email, password=VALID_PASSWORD)

    def test_png_response_content_type(self):
        resp = self.client.get(reverse("qr_manager:png", args=[self.shop.slug]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")

    def test_png_response_is_valid_image(self):
        """Confirm the response body is actually a readable PNG."""
        resp = self.client.get(reverse("qr_manager:png", args=[self.shop.slug]))
        from PIL import Image
        img = Image.open(io.BytesIO(resp.content))
        self.assertEqual(img.format, "PNG")

    def test_png_has_cache_header(self):
        resp = self.client.get(reverse("qr_manager:png", args=[self.shop.slug]))
        self.assertIn("max-age", resp.get("Cache-Control", ""))

    def test_png_requires_login(self):
        anon = Client()
        resp = anon.get(reverse("qr_manager:png", args=[self.shop.slug]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp.url)

    def test_other_user_cannot_generate_png_for_shop_they_dont_own(self):
        other = make_verified_user("qrpng_other@example.com")
        client_b = Client()
        client_b.login(username=other.email, password=VALID_PASSWORD)
        resp = client_b.get(reverse("qr_manager:png", args=[self.shop.slug]))
        self.assertEqual(resp.status_code, 404)


# ─────────────────────────────────────────────
# QR SVG generation
# ─────────────────────────────────────────────

class QRSVGTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.owner = make_verified_user("qrsvg@example.com")
        self.shop = make_shop(self.owner)
        self.client.login(username=self.owner.email, password=VALID_PASSWORD)

    def test_svg_response_content_type(self):
        resp = self.client.get(reverse("qr_manager:svg", args=[self.shop.slug]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/svg+xml")

    def test_svg_response_contains_svg_markup(self):
        resp = self.client.get(reverse("qr_manager:svg", args=[self.shop.slug]))
        content = resp.content.decode("utf-8")
        self.assertIn("<svg", content)

    def test_svg_is_forced_download(self):
        """SVG is for printing — should always download, not display inline."""
        resp = self.client.get(reverse("qr_manager:svg", args=[self.shop.slug]))
        self.assertIn("attachment", resp.get("Content-Disposition", ""))

    def test_svg_requires_login(self):
        anon = Client()
        resp = anon.get(reverse("qr_manager:svg", args=[self.shop.slug]))
        self.assertEqual(resp.status_code, 302)

    def test_other_user_cannot_generate_svg_for_shop_they_dont_own(self):
        other = make_verified_user("qrsvg_other@example.com")
        client_b = Client()
        client_b.login(username=other.email, password=VALID_PASSWORD)
        resp = client_b.get(reverse("qr_manager:svg", args=[self.shop.slug]))
        self.assertEqual(resp.status_code, 404)


# ─────────────────────────────────────────────
# QR detail page
# ─────────────────────────────────────────────

class QRDetailPageTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.owner = make_verified_user("qrdetail@example.com")
        self.shop = make_shop(self.owner)
        self.client.login(username=self.owner.email, password=VALID_PASSWORD)

    def test_detail_page_renders(self):
        resp = self.client.get(reverse("qr_manager:detail", args=[self.shop.slug]))
        self.assertEqual(resp.status_code, 200)

    def test_detail_page_shows_short_url(self):
        resp = self.client.get(reverse("qr_manager:detail", args=[self.shop.slug]))
        self.assertContains(resp, f"/s/{self.shop.pk}/")

    def test_detail_page_includes_download_buttons(self):
        resp = self.client.get(reverse("qr_manager:detail", args=[self.shop.slug]))
        self.assertContains(resp, "Download PNG")
        self.assertContains(resp, "Download SVG")

    def test_detail_page_requires_login(self):
        anon = Client()
        resp = anon.get(reverse("qr_manager:detail", args=[self.shop.slug]))
        self.assertEqual(resp.status_code, 302)

    def test_other_user_cannot_view_qr_detail(self):
        other = make_verified_user("qrdetail_other@example.com")
        client_b = Client()
        client_b.login(username=other.email, password=VALID_PASSWORD)
        resp = client_b.get(reverse("qr_manager:detail", args=[self.shop.slug]))
        self.assertEqual(resp.status_code, 404)


# ─────────────────────────────────────────────
# QR content correctness
# ─────────────────────────────────────────────

class QRContentTests(TestCase):
    """
    Decodes the generated QR code and verifies it encodes the correct
    short URL — not the slug URL, and not a hardcoded domain.
    """

    def setUp(self):
        self.client = Client()
        self.owner = make_verified_user("qrcontent@example.com")
        self.shop = make_shop(self.owner)
        self.client.login(username=self.owner.email, password=VALID_PASSWORD)

    def test_qr_encodes_short_url_not_slug_url(self):
        """
        Decode the actual QR PNG and read its data to confirm it
        contains /s/<id>/ rather than /shop/<slug>/. This is the most
        important correctness test — everything else (density, camera
        scannability) flows from the URL being short.
        """
        try:
            from pyzbar.pyzbar import decode as pyzbar_decode
        except ImportError:
            self.skipTest(
                "pyzbar not installed — install it to run QR decode tests. "
                "pip install pyzbar  (also needs libzbar0 on Linux: "
                "sudo apt-get install libzbar0)"
            )

        resp = self.client.get(reverse("qr_manager:png", args=[self.shop.slug]))
        from PIL import Image
        img = Image.open(io.BytesIO(resp.content))
        decoded = pyzbar_decode(img)

        self.assertTrue(len(decoded) > 0, "QR code could not be decoded")
        qr_data = decoded[0].data.decode("utf-8")

        self.assertIn(f"/s/{self.shop.pk}/", qr_data)
        self.assertNotIn(f"/shop/{self.shop.slug}/", qr_data)

    def test_short_url_uses_numeric_pk_not_slug(self):
        """
        Even without pyzbar, verify the URL building logic itself
        produces a short URL with the numeric PK.
        """
        from qr_manager.views import _get_short_url
        from django.test import RequestFactory

        rf = RequestFactory()
        request = rf.get("/")
        short_url = _get_short_url(request, self.shop)

        self.assertIn(f"/s/{self.shop.pk}/", short_url)
        self.assertNotIn(self.shop.slug, short_url)

        