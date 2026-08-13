from django.conf import settings
from django.db import models
from django.utils import timezone


class MasterStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    BLOCKED = "blocked", "Blocked"


class ServiceCategory(models.Model):
    slug = models.SlugField(unique=True)
    name_ru = models.CharField(max_length=120)
    name_uz = models.CharField(max_length=120)
    icon = models.CharField(max_length=16, blank=True)
    color_hex = models.CharField(max_length=16, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "name_ru"]
        verbose_name_plural = "service categories"

    def __str__(self) -> str:
        return self.name_ru


class MasterProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="master_profile")
    status = models.CharField(max_length=32, choices=MasterStatus.choices, default=MasterStatus.PENDING)
    bio = models.TextField(blank=True)
    face_photo_url = models.URLField(blank=True)

    activity_points = models.IntegerField(default=400)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    completed_orders_count = models.PositiveIntegerField(default=0)

    is_online = models.BooleanField(default=False)
    current_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    current_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    # Weekly leaderboard snapshot (TZ §7.2): rank recomputed by a periodic job;
    # the previous value lets the UI show the ↑/↓ position change.
    leaderboard_rank = models.PositiveIntegerField(null=True, blank=True)
    leaderboard_rank_prev = models.PositiveIntegerField(null=True, blank=True)

    approved_at = models.DateTimeField(null=True, blank=True)
    blocked_at = models.DateTimeField(null=True, blank=True)
    block_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return str(self.user)

    @property
    def is_approved(self) -> bool:
        return self.status == MasterStatus.APPROVED

    def approve(self) -> None:
        self.status = MasterStatus.APPROVED
        self.approved_at = timezone.now()
        self.user.is_master_enabled = True
        self.user.save(update_fields=["is_master_enabled", "updated_at"])
        self.save(update_fields=["status", "approved_at", "updated_at"])


class MasterServiceStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class MasterCategoryPrice(models.Model):
    """A direction (service) the master offers. Up to 3 per master, each
    moderated (status) and independently toggleable (is_active)."""

    master = models.ForeignKey(MasterProfile, on_delete=models.CASCADE, related_name="category_prices")
    category = models.ForeignKey(ServiceCategory, on_delete=models.PROTECT, related_name="master_prices")
    min_price_uzs = models.PositiveIntegerField()
    max_price_uzs = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    status = models.CharField(
        max_length=16, choices=MasterServiceStatus.choices, default=MasterServiceStatus.APPROVED
    )
    reject_reason = models.TextField(blank=True)
    experience_years = models.CharField(max_length=16, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        unique_together = ["master", "category"]
        ordering = ["category__sort_order"]

    @property
    def is_available(self) -> bool:
        return self.is_active and self.status == MasterServiceStatus.APPROVED

    def __str__(self) -> str:
        return f"{self.master} / {self.category}: {self.min_price_uzs}-{self.max_price_uzs}"


class MasterPortfolioItem(models.Model):
    """A work sample the master chose to publish on their profile (TZ §6.3/§7.5).
    Completion photos never land here automatically — the master adds them."""

    master = models.ForeignKey(
        MasterProfile, on_delete=models.CASCADE, related_name="portfolio_items"
    )
    category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portfolio_items",
    )
    image = models.ImageField(upload_to="portfolio/")
    caption = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.master} portfolio #{self.pk}"


class MasterPortfolioPost(models.Model):
    """An Instagram-style portfolio post: a titled, described gallery of work
    photos the master publishes so clients can judge their quality."""

    master = models.ForeignKey(
        MasterProfile, on_delete=models.CASCADE, related_name="portfolio_posts"
    )
    category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portfolio_posts",
    )
    title = models.CharField(max_length=120)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.master} / {self.title}"


class MasterPortfolioPostImage(models.Model):
    post = models.ForeignKey(
        MasterPortfolioPost, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to="portfolio/posts/")
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.post} image #{self.pk}"

