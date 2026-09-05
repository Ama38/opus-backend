from django.contrib import admin
from datetime import timedelta
from unittest.mock import patch
from django.db.models.query import QuerySet

from django.test import RequestFactory, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.support.admin import SupportCaseAdmin, SupportCaseAdminForm
from apps.notifications.models import NotificationEvent
from apps.support.models import SupportCase, SupportCaseStatus
from apps.support.services import add_support_message, close_inactive_support_cases


class SupportChatTests(TestCase):
    def test_operator_final_reply_and_resolution_are_saved_together(self):
        form = SupportCaseAdminForm(data={
            "user": self.user.pk, "status": SupportCaseStatus.RESOLVED,
            "priority": "normal", "subject": self.case.subject,
            "body": self.case.body, "assigned_to": "",
            "operator_reply": "Проблема решена",
        }, instance=self.case)
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        request = RequestFactory().post("/")
        request.user = self.operator
        handler = SupportCaseAdmin(SupportCase, admin.site)
        handler.save_model(request, obj, form, True)
        handler.save_related(request, form, [], True)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, SupportCaseStatus.RESOLVED)
        self.assertIsNotNone(self.case.closed_at)
        self.assertEqual(self.case.messages.get().text, "Проблема решена")

    def test_reply_arriving_during_sweep_prevents_closure(self):
        add_support_message(self.case, sender=self.operator, text="Уточните вопрос")
        self.case.last_operator_message_at = timezone.now() - timedelta(hours=4)
        self.case.save(update_fields=["last_operator_message_at"])
        original_update = QuerySet.update
        replied = False

        def reply_before_update(queryset, **kwargs):
            nonlocal replied
            if queryset.model is SupportCase and kwargs.get("status") == SupportCaseStatus.CLOSED and not replied:
                replied = True
                add_support_message(self.case, sender=self.user, text="Мой ответ")
            return original_update(queryset, **kwargs)

        with patch.object(QuerySet, "update", new=reply_before_update):
            self.assertEqual(close_inactive_support_cases(), 0)
        self.assertTrue(replied)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, SupportCaseStatus.IN_PROGRESS)

    def test_stale_case_cannot_receive_message_after_closure(self):
        SupportCase.objects.filter(pk=self.case.pk).update(status=SupportCaseStatus.CLOSED)
        with self.assertRaisesMessage(ValueError, "support_case_closed"):
            add_support_message(self.case, sender=self.user, text="Поздний ответ")
        self.assertFalse(self.case.messages.exists())

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

    def test_user_cannot_reopen_resolved_case_with_a_message(self):
        self.case.status = SupportCaseStatus.RESOLVED
        self.case.save(update_fields=["status", "updated_at"])
        api = APIClient()
        api.force_authenticate(self.user)

        response = api.post(
            f"/api/support/cases/{self.case.id}/message/",
            {"text": "  Проблема осталась  "},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, SupportCaseStatus.RESOLVED)
        self.assertFalse(self.case.messages.exists())
        self.assertEqual(response.json()["code"], "support_case_closed")

    def test_each_new_problem_creates_a_separate_chat_with_initial_message(self):
        api = APIClient()
        api.force_authenticate(self.user)

        first = api.post(
            "/api/support/cases/",
            {"subject": "Заказ", "body": "Первый вопрос", "priority": "high"},
            format="json",
        )
        second = api.post(
            "/api/support/cases/",
            {"subject": "Профиль", "body": "Второй вопрос"},
            format="json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(first.json()["messages"][0]["text"], "Первый вопрос")
        self.assertEqual(second.json()["messages"][0]["text"], "Второй вопрос")

    def test_user_can_close_case_and_cannot_send_more_messages(self):
        api = APIClient()
        api.force_authenticate(self.user)

        closed = api.post(f"/api/support/cases/{self.case.id}/close/")
        message = api.post(
            f"/api/support/cases/{self.case.id}/message/",
            {"text": "Новое сообщение"},
            format="json",
        )

        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json()["status"], SupportCaseStatus.CLOSED)
        self.assertEqual(closed.json()["close_reason"], "resolved_by_user")
        self.assertEqual(message.status_code, 409)

    def test_case_closes_after_operator_waits_three_hours(self):
        operator_message = add_support_message(
            self.case,
            sender=self.operator,
            text="Уточните номер заказа",
        )
        stale_at = timezone.now() - timedelta(hours=3, minutes=1)
        self.case.last_operator_message_at = stale_at
        self.case.save(update_fields=["last_operator_message_at", "updated_at"])

        closed_count = close_inactive_support_cases(now=timezone.now())

        self.case.refresh_from_db()
        self.assertIsNotNone(operator_message.id)
        self.assertEqual(closed_count, 1)
        self.assertEqual(self.case.status, SupportCaseStatus.CLOSED)
        self.assertEqual(self.case.close_reason, "user_inactive")
        self.assertTrue(
            NotificationEvent.objects.filter(
                user=self.user, event_type="support.case_closed"
            ).exists()
        )

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

        api = APIClient()
        api.force_authenticate(self.user)
        payload = api.get("/api/support/cases/").json()[0]
        self.assertTrue(payload["messages"][0]["sender_is_operator"])
