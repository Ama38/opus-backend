import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.masters.models import (
    MasterCategoryPrice,
    MasterProfile,
    MasterServiceStatus,
    MasterStatus,
    ServiceCategory,
)


def _image_file(name: str, color: tuple[int, int, int]) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class MasterPortfolioPostApiTests(TestCase):
    def setUp(self):
        self.master_user = User.objects.create_user(
            phone="+998909001101", full_name="Portfolio Master"
        )
        self.master = MasterProfile.objects.create(
            user=self.master_user,
            status=MasterStatus.APPROVED,
        )
        self.category = ServiceCategory.objects.create(
            slug="painter",
            name_ru="Маляр",
            name_uz="Bo‘yoqchi",
        )
        MasterCategoryPrice.objects.create(
            master=self.master,
            category=self.category,
            min_price_uzs=100_000,
            max_price_uzs=500_000,
            status=MasterServiceStatus.APPROVED,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.master_user)

    def test_master_creates_multi_image_post_visible_in_public_profile(self):
        response = self.api.post(
            "/api/master-portfolio-posts/",
            {
                "title": "Ремонт гостиной",
                "description": "Подготовили стены и выполнили чистовую покраску.",
                "category": self.category.id,
                "images[]": [
                    _image_file("before.jpg", (20, 30, 40)),
                    _image_file("after.jpg", (210, 220, 230)),
                ],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201, response.data)
        payload = response.json()
        self.assertEqual(payload["title"], "Ремонт гостиной")
        self.assertEqual(payload["category_slug"], "painter")
        self.assertEqual(len(payload["images"]), 2)
        self.assertTrue(payload["images"][0]["image_url"].startswith("http"))

        client_user = User.objects.create_user(phone="+998909001102")
        client_api = APIClient()
        client_api.force_authenticate(client_user)
        public_response = client_api.get(
            f"/api/masters/{self.master.id}/public/"
        )

        self.assertEqual(public_response.status_code, 200)
        posts = public_response.json()["master"]["portfolio_posts"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["description"], payload["description"])
        self.assertEqual(len(posts[0]["images"]), 2)

    def test_portfolio_post_requires_images_and_complete_copy(self):
        without_images = self.api.post(
            "/api/master-portfolio-posts/",
            {
                "title": "Работа",
                "description": "Достаточно подробное описание работы.",
            },
            format="multipart",
        )
        self.assertEqual(without_images.status_code, 400)
        self.assertEqual(without_images.json()["code"], "portfolio_images_required")

        short_copy = self.api.post(
            "/api/master-portfolio-posts/",
            {
                "title": "A",
                "description": "Коротко",
                "images[]": [_image_file("work.jpg", (10, 20, 30))],
            },
            format="multipart",
        )
        self.assertEqual(short_copy.status_code, 400)
        self.assertIn("title", short_copy.json())
        self.assertIn("description", short_copy.json())

    def test_portfolio_rejects_category_that_operator_has_not_approved(self):
        pending_category = ServiceCategory.objects.create(
            slug="plumber",
            name_ru="Сантехник",
            name_uz="Santexnik",
        )
        MasterCategoryPrice.objects.create(
            master=self.master,
            category=pending_category,
            min_price_uzs=80_000,
            max_price_uzs=300_000,
            status=MasterServiceStatus.PENDING,
        )

        response = self.api.post(
            "/api/master-portfolio-posts/",
            {
                "title": "Ремонт трубы",
                "description": "Заменили повреждённый участок трубы.",
                "category": pending_category.id,
                "images[]": [_image_file("pipe.jpg", (40, 80, 120))],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "portfolio_category_not_approved")
