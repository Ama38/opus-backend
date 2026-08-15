from django.contrib import admin as django_admin
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.billing.admin import PackagePurchaseAdmin
from apps.billing.models import Package, PackagePurchase, PackagePurchaseStatus
from apps.billing.services import activate_purchase, request_package
from apps.masters.models import MasterStatus
from apps.masters.services import get_or_create_master_profile


class _FakeRequest:
    def __init__(self, user):
        self.user = user


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

    def test_admin_status_dropdown_activation_applies_the_subscription(self):
        # Operator activates by flipping the status dropdown to "Activated" in
        # the change form (not the bulk action). This must still apply the
        # subscription allowance — otherwise the purchase shows activated while
        # the master can't receive orders.
        master = get_or_create_master_profile(self.master_user)
        master.approve()
        purchase = request_package(master, self.package, actor=self.master_user)
        self.assertEqual(purchase.status, PackagePurchaseStatus.PENDING)

        model_admin = PackagePurchaseAdmin(PackagePurchase, django_admin.site)
        request = _FakeRequest(self.operator)
        purchase.status = PackagePurchaseStatus.ACTIVATED
        model_admin.save_model(request, purchase, form=None, change=True)

        purchase.refresh_from_db()
        master.subscription.refresh_from_db()
        self.assertEqual(purchase.status, PackagePurchaseStatus.ACTIVATED)
        self.assertEqual(purchase.activated_by, self.operator)
        self.assertTrue(master.subscription.is_active)
        self.assertEqual(master.subscription.orders_remaining, 10)

        # Re-saving an already-activated purchase must not grant more orders.
        model_admin.save_model(request, purchase, form=None, change=True)
        master.subscription.refresh_from_db()
        self.assertEqual(master.subscription.orders_remaining, 10)
