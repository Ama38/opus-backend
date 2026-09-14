from django.conf import settings
from django.db import models
from django.utils import timezone


class MasterStatus(models.TextChoices):
    PENDING = "pending", "На проверке"
    APPROVED = "approved", "Подтверждён"
    REJECTED = "rejected", "Отклонён"
    BLOCKED = "blocked", "Заблокирован"


class ServiceCategory(models.Model):
    slug = models.SlugField(unique=True, verbose_name="Slug")
    name_ru = models.CharField(max_length=120, verbose_name="Название (рус)")
    name_uz = models.CharField(max_length=120, verbose_name="Название (узб)")
    icon = models.CharField(max_length=16, blank=True, verbose_name="Иконка")
    color_hex = models.CharField(max_length=16, blank=True, verbose_name="Цвет")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    sort_order = models.PositiveSmallIntegerField(default=100, verbose_name="Порядок")

    class Meta:
        ordering = ["sort_order", "name_ru"]
        verbose_name = "Категория услуг"
        verbose_name_plural = "Категории услуг"

    def __str__(self) -> str:
        return self.name_ru


class MasterProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="master_profile",
        verbose_name="Пользователь",
    )
    status = models.CharField(
        max_length=32, choices=MasterStatus.choices, default=MasterStatus.PENDING, verbose_name="Статус"
    )
    bio = models.TextField(blank=True, verbose_name="О себе")
    face_photo_url = models.URLField(blank=True, verbose_name="Фото лица")

    activity_points = models.IntegerField(default=400, verbose_name="Баллы активности")
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0, verbose_name="Рейтинг")
    completed_orders_count = models.PositiveIntegerField(default=0, verbose_name="Выполнено заказов")

    is_online = models.BooleanField(default=False, verbose_name="На линии")
    online_since = models.DateTimeField(null=True, blank=True, verbose_name="На линии с")
    current_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Широта")
    current_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Долгота")
    last_seen_at = models.DateTimeField(null=True, blank=True, verbose_name="Последняя активность")

    # Weekly leaderboard snapshot (TZ §7.2): rank recomputed by a periodic job;
    # the previous value lets the UI show the ↑/↓ position change.
    leaderboard_rank = models.PositiveIntegerField(null=True, blank=True, verbose_name="Место в рейтинге")
    leaderboard_rank_prev = models.PositiveIntegerField(null=True, blank=True, verbose_name="Прошлое место")

    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="Подтверждён")
    blocked_at = models.DateTimeField(null=True, blank=True, verbose_name="Заблокирован")
    block_reason = models.TextField(blank=True, verbose_name="Причина блокировки")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлён")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Мастер"
        verbose_name_plural = "Мастера"

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
        # Approving the master also approves the services he registered with, so
        # he can actually be matched (otherwise he is online but invisible to
        # matching, which requires an approved service). Services added *after*
        # approval still go through their own moderation.
        self.category_prices.filter(status=MasterServiceStatus.PENDING).update(
            status=MasterServiceStatus.APPROVED, reject_reason=""
        )

    def reject(self) -> None:
        self.status = MasterStatus.REJECTED
        self.is_online = False
        self.user.is_master_enabled = False
        self.user.save(update_fields=["is_master_enabled", "updated_at"])
        self.save(update_fields=["status", "is_online", "updated_at"])

    def block(self, reason: str = "") -> None:
        self.status = MasterStatus.BLOCKED
        self.is_online = False
        self.blocked_at = timezone.now()
        if reason:
            self.block_reason = reason
        self.user.is_master_enabled = False
        self.user.save(update_fields=["is_master_enabled", "updated_at"])
        self.save(update_fields=["status", "is_online", "blocked_at", "block_reason", "updated_at"])


class MasterServiceStatus(models.TextChoices):
    PENDING = "pending", "На проверке"
    APPROVED = "approved", "Подтверждена"
    REJECTED = "rejected", "Отклонена"


class MasterCategoryPrice(models.Model):
    """A direction (service) the master offers. Up to 3 per master, each
    moderated (status) and independently toggleable (is_active)."""

    master = models.ForeignKey(
        MasterProfile, on_delete=models.CASCADE, related_name="category_prices", verbose_name="Мастер"
    )
    category = models.ForeignKey(
        ServiceCategory, on_delete=models.PROTECT, related_name="master_prices", verbose_name="Категория"
    )
    min_price_uzs = models.PositiveIntegerField(verbose_name="Цена от")
    max_price_uzs = models.PositiveIntegerField(verbose_name="Цена до")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    status = models.CharField(
        max_length=16,
        choices=MasterServiceStatus.choices,
        default=MasterServiceStatus.APPROVED,
        verbose_name="Статус",
    )
    reject_reason = models.TextField(blank=True, verbose_name="Причина отказа")
    experience_years = models.CharField(max_length=16, blank=True, verbose_name="Опыт (лет)")
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True, verbose_name="Создана")

    class Meta:
        unique_together = ["master", "category"]
        ordering = ["category__sort_order"]
        verbose_name = "Услуга мастера"
        verbose_name_plural = "Услуги мастеров"

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

