from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.billing.models import Package, PackagePurchaseStatus
from apps.billing.services import activate_purchase, request_package
from apps.masters.models import MasterStatus
from apps.masters.services import get_or_create_master_profile


@override_settings(
    MASTERGO_AUTO_APPROVE_MASTERS=False,
    MASTERGO_FREE_PACKAGES=False,
)
class PackageApprovalTests(TestCase):
    def setUp(self):
        self.master_user = User.objects.create_user(
            phone="+998909002201",
            full_name="Pending Master",
        )
        self.operator = User.objects.create_user(
            phone="+998909002202",
            full_name="Operator",
            is_staff=True,
        )
        self.package = Package.objects.create(
            slug="start-10",
            name_ru="Старт",
            name_uz="Start",
            orders_count=10,
            price_uzs=100_000,
        )

    def test_new_master_and_package_wait_for_operator_confirmation(self):
        master = get_or_create_master_profile(self.master_user)
        self.assertEqual(master.status, MasterStatus.PENDING)

        purchase = request_package(master, self.package, actor=self.master_user)
        purchase.refresh_from_db()
        master.subscription.refresh_from_db()

        self.assertEqual(purchase.status, PackagePurchaseStatus.PENDING)
        self.assertFalse(master.subscription.is_active)
        self.assertEqual(master.subscription.orders_remaining, 0)

        master.approve()
        activate_purchase(purchase, activated_by=self.operator)
        purchase.refresh_from_db()
        master.subscription.refresh_from_db()

        self.assertEqual(master.status, MasterStatus.APPROVED)
        self.assertEqual(purchase.status, PackagePurchaseStatus.ACTIVATED)
        self.assertEqual(purchase.activated_by, self.operator)
        self.assertTrue(master.subscription.is_active)
        self.assertEqual(master.subscription.orders_remaining, 10)
