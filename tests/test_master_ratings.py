from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.masters.models import MasterProfile, MasterStatus, ServiceCategory
from apps.orders.models import Order, OrderStatus
from apps.reviews.models import Review


class MasterRatingTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.client = User.objects.create_user(
            phone="+998909004401", full_name="Client"
        )
        self.second_client = User.objects.create_user(
            phone="+998909004402", full_name="Second client"
        )
        self.master_user = User.objects.create_user(
            phone="+998909004403", full_name="Master"
        )
        self.master = MasterProfile.objects.create(
            user=self.master_user,
            status=MasterStatus.APPROVED,
        )
        self.category = ServiceCategory.objects.create(
            slug="rating-test",
            name_ru="Тест рейтинга",
            name_uz="Reyting testi",
        )

    def _order(self, client):
        return Order.objects.create(
            client=client,
            master=self.master,
            category=self.category,
            status=OrderStatus.COMPLETED,
            address_text="Tashkent",
        )

    def test_rating_is_real_average_and_directory_has_review_count(self):
        Review.objects.create(
            order=self._order(self.client),
            author=self.client,
            target=self.master_user,
            rating=5,
            is_public=True,
        )
        Review.objects.create(
            order=self._order(self.second_client),
            author=self.second_client,
            target=self.master_user,
            rating=3,
            is_public=True,
        )

        self.master.refresh_from_db()
        self.assertEqual(self.master.rating, Decimal("4.00"))

        self.api.force_authenticate(self.client)
        payload = self.api.get("/api/masters/directory/").json()["masters"][0]
        self.assertEqual(payload["rating"], "4.00")
        self.assertEqual(payload["effective_rating"], 4.0)
        self.assertEqual(payload["review_count"], 2)

    def test_master_without_reviews_has_zero_rating_not_starter_rating(self):
        self.api.force_authenticate(self.client)
        payload = self.api.get("/api/masters/directory/").json()["masters"][0]

        self.assertEqual(payload["rating"], "0.00")
        self.assertEqual(payload["effective_rating"], 0.0)
        self.assertEqual(payload["review_count"], 0)

    def test_deleting_review_recalculates_rating(self):
        first = Review.objects.create(
            order=self._order(self.client),
            author=self.client,
            target=self.master_user,
            rating=5,
            is_public=True,
        )
        Review.objects.create(
            order=self._order(self.second_client),
            author=self.second_client,
            target=self.master_user,
            rating=1,
            is_public=True,
        )

        first.delete()

        self.master.refresh_from_db()
        self.assertEqual(self.master.rating, Decimal("1.00"))
