import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction, IntegrityError
from django.utils.text import slugify
from django.urls import reverse  


# ── Validators ───────────────────────────────────────────

nepal_phone_validator = RegexValidator(
    regex=r"^(98|97)\d{8}$",
    message="Enter a valid Nepali mobile number (e.g. 98XXXXXXXX or 97XXXXXXXX).",
)

MAX_LOGO_SIZE_BYTES = 5 * 1024 * 1024  # 5MB
ALLOWED_LOGO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def validate_logo_image(file):
    """
    Two-layer validation for shop logo uploads:
    1. Extension allowlist — fast, cheap, rejects obviously wrong files.
    2. Pillow content-sniff — confirms the file is ACTUALLY a readable
       image of an allowed format, not just renamed to look like one.
       An extension check alone is spoofable (rename a .tiff to .jpg);
       opening it with Pillow and checking the real detected format
       closes that gap.

    Also enforces a 5MB size ceiling so a vendor's heavy phone-camera
    photo can't tie up the upload in one shot — this is a hard backstop,
    not a substitute for the client-side compression (Compressor.js)
    the SDD calls for separately.
    """
    # ── Size check ──
    if file.size > MAX_LOGO_SIZE_BYTES:
        raise ValidationError(
            f"Image file too large ({file.size // (1024*1024)}MB). "
            f"Maximum allowed is {MAX_LOGO_SIZE_BYTES // (1024*1024)}MB."
        )

    # ── Extension allowlist ──
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        raise ValidationError(
            f"Unsupported file type '{ext}'. Allowed: "
            f"{', '.join(sorted(ALLOWED_LOGO_EXTENSIONS))}."
        )

    # ── Real content sniff via Pillow ──
    try:
        from PIL import Image
        file.seek(0)
        img = Image.open(file)
        img.verify()  # raises if the file isn't a genuinely valid image
        detected_format = (img.format or "").upper()
    except Exception:
        raise ValidationError("This file is not a valid, readable image.")
    finally:
        file.seek(0)  # reset pointer so Django can still save the file afterward

    allowed_formats = {"JPEG", "PNG", "WEBP"}
    if detected_format not in allowed_formats:
        raise ValidationError(
            f"Detected image format '{detected_format}' is not allowed. "
            f"Allowed formats: JPEG, PNG, WEBP."
        )


class ShopType(models.TextChoices):
    """
    TextChoices over a raw list: IDE autocomplete (ShopType.KIRANA) and
    refactor-safety. Stored DB values are unchanged ("kirana",
    "restaurant", etc.), so this is a safe drop-in with no data
    migration implications.
    """
    KIRANA     = "kirana",     "Kirana Store"
    RESTAURANT = "restaurant", "Restaurant"
    FRUIT_VEG  = "fruit_veg",  "Fruit / Vegetable Shop"
    TEA_CAFE   = "tea_cafe",   "Tea Shop / Cafe"
    OTHER      = "other",      "Other"


class Shop(models.Model):
    """
    A single shop/restaurant/stall on Sajilo Pasal.

    slug locks at creation and never changes on rename — it may already
    be printed on a QR code in a customer's hands.

    NOTE: get_menu_url currently returns a hardcoded path string. This
    will be switched to use reverse() once the actual public menu URL
    (menu:detail) is built on Day 9 — doing that now would point at a
    URL name that doesn't exist yet.
    """

    owner       = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shops",
    )

    name        = models.CharField(max_length=150)
    # Reserve headroom for the largest realistic collision suffix so
    # truncating the base slug never collides with max_length=170.
    slug        = models.SlugField(max_length=170, unique=True, blank=True, db_index=True)
    shop_type   = models.CharField(max_length=20, choices=ShopType.choices, default=ShopType.KIRANA)

    logo        = models.ImageField(
        upload_to="shop_logos/",
        blank=True,
        null=True,
        validators=[validate_logo_image],
    )
    phone       = models.CharField(
        max_length=20,
        blank=True,
        validators=[nepal_phone_validator],
    )
    address     = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)

    is_active   = models.BooleanField(default=True)

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Shop"
        verbose_name_plural = "Shops"
        ordering             = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "is_active"]),
        ]

    def __str__(self):
        return self.name

    # ── Slug generation ──────────────────────────────────

    # Reserve 10 characters of headroom for the "-N" suffix so the
    # truncated base never bumps against SlugField's max_length=170
    # once a counter suffix is appended.
    _SLUG_BASE_MAX_LENGTH = 160

    def save(self, *args, **kwargs):
        if not self.slug:
            self._save_with_unique_slug(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def _save_with_unique_slug(self, *args, **kwargs):
        """
        Generates a slug and saves, retrying on IntegrityError.

        Why a retry loop instead of just transaction.atomic():
        atomic() only protects against a partial write within ONE
        transaction — it does nothing to stop two separate, concurrent
        requests from each independently checking "is this slug taken?",
        both getting "no", and both then trying to INSERT the same
        slug. That's exactly the race two simultaneous "ABC Shop"
        creations would hit. The only reliable fix is to let the
        database's unique constraint be the final word: attempt the
        save, and if it raises IntegrityError on the slug collision,
        generate a new candidate and try again.
        """
        base_slug = slugify(self.name)[: self._SLUG_BASE_MAX_LENGTH] or "shop"
        candidate = base_slug
        counter = 2
        max_retries = 10

        for attempt in range(max_retries):
            self.slug = candidate
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                return  # success
            except IntegrityError:
                # Someone else's concurrent save won the race for this
                # exact slug between our check and our insert. Generate
                # the next candidate and retry.
                candidate = f"{base_slug}-{counter}"
                counter += 1
                if self.pk and not self._state.adding:
                    self.pk = None
                continue

        raise IntegrityError(
            f"Could not generate a unique slug for '{self.name}' after {max_retries} attempts."
        )


    def get_shop_type_display_nepali(self):
        """
        Nepali label for shop_type, used in bilingual UI per the SDD's
        localization strategy.
        """
        nepali_labels = {
            ShopType.KIRANA:     "किराना पसल",
            ShopType.RESTAURANT: "रेस्टुरेन्ट",
            ShopType.FRUIT_VEG:  "फलफूल / तरकारी पसल",
            ShopType.TEA_CAFE:   "चिया पसल",
            ShopType.OTHER:      "अन्य",
        }
        return nepali_labels.get(self.shop_type, "")
    
    def get_menu_url(self):
        return reverse("menu:detail", kwargs={"slug": self.slug})
