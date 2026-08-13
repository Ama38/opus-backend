from django.contrib import admin
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.support.admin import SupportCaseAdmin, SupportCaseAdminForm
from apps.support.models import SupportCase, SupportCaseStatus


class SupportChatTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+998909002201", full_name="Client"
        )
        self.operator = User.objects.create_superuser(
            phone="+998909002202", password="secret"
        )
        self.case = SupportCase.objects.create(
            user=self.user,
            subject="Проблема с заказом",
            body="Не могу связаться с мастером",
        )

    def test_user_message_is_trimmed_and_reopens_resolved_case(self):
        self.case.status = SupportCaseStatus.RESOLVED
        self.case.save(update_fields=["status", "updated_at"])
        api = APIClient()
        api.force_authenticate(self.user)

        response = api.post(
            f"/api/support/cases/{self.case.id}/message/",
            {"text": "  Проблема осталась  "},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, SupportCaseStatus.OPEN)
        self.assertEqual(self.case.messages.get().text, "Проблема осталась")

    def test_operator_can_reply_from_case_admin(self):
        form = SupportCaseAdminForm(
            data={
                "user": self.user.id,
                "status": SupportCaseStatus.OPEN,
                "priority": "normal",
                "subject": self.case.subject,
                "body": self.case.body,
                "assigned_to": "",
                "operator_reply": "Мы связались с мастером. Проверьте чат.",
            },
            instance=self.case,
        )
        self.assertTrue(form.is_valid(), form.errors)
        request = RequestFactory().post("/admin/support/supportcase/")
        request.user = self.operator
        case_admin = SupportCaseAdmin(SupportCase, admin.site)

        case_admin.save_model(request, self.case, form, change=True)

        self.case.refresh_from_db()
        message = self.case.messages.get()
        self.assertEqual(message.sender, self.operator)
        self.assertEqual(
            message.text, "Мы связались с мастером. Проверьте чат."
        )
        self.assertEqual(self.case.assigned_to, self.operator)
        self.assertEqual(self.case.status, SupportCaseStatus.IN_PROGRESS)
