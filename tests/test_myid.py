from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts import myid
from apps.accounts.models import User


@override_settings(MYID_CLIENT_ID="cid", MYID_CLIENT_SECRET="secret", MYID_HOST="https://myid.test")
class MyIdClientTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("apps.accounts.myid.requests.post")
    def test_access_token_is_cached_across_calls(self, post_mock):
        post_mock.return_value = Mock(
            status_code=200, json=lambda: {"access_token": "tok1", "expires_in": 3600}
        )
        first = myid._access_token()
        second = myid._access_token()
        self.assertEqual(first, "tok1")
        self.assertEqual(second, "tok1")
        post_mock.assert_called_once()

    @patch("apps.accounts.myid.requests.post")
    def test_access_token_raises_when_not_configured(self, post_mock):
        with override_settings(MYID_CLIENT_ID="", MYID_CLIENT_SECRET=""):
            with self.assertRaises(myid.MyIdError):
                myid._access_token()
        post_mock.assert_not_called()

    @patch("apps.accounts.myid.requests.request")
    @patch("apps.accounts.myid.requests.post")
    def test_create_session_returns_session_id(self, post_mock, request_mock):
        post_mock.return_value = Mock(
            status_code=200, json=lambda: {"access_token": "tok1", "expires_in": 3600}
        )
        request_mock.return_value = Mock(status_code=200, json=lambda: {"session_id": "abc-123"})
        session_id = myid.create_session(phone_number="998901234567")
        self.assertEqual(session_id, "abc-123")

    @patch("apps.accounts.myid.requests.request")
    @patch("apps.accounts.myid.requests.post")
    def test_create_session_retries_once_on_401(self, post_mock, request_mock):
        post_mock.return_value = Mock(
            status_code=200, json=lambda: {"access_token": "tok1", "expires_in": 3600}
        )
        cache.set(myid._TOKEN_CACHE_KEY, "stale-token", timeout=60)
        request_mock.side_effect = [
            Mock(status_code=401, text="expired"),
            Mock(status_code=200, json=lambda: {"session_id": "abc-123"}),
        ]
        session_id = myid.create_session()
        self.assertEqual(session_id, "abc-123")
        self.assertEqual(request_mock.call_count, 2)

    @patch("apps.accounts.myid.requests.request")
    @patch("apps.accounts.myid.requests.post")
    def test_get_identification_data_raises_on_error_status(self, post_mock, request_mock):
        post_mock.return_value = Mock(
            status_code=200, json=lambda: {"access_token": "tok1", "expires_in": 3600}
        )
        request_mock.return_value = Mock(status_code=404, text='{"detail":"Code not found"}')
        with self.assertRaises(myid.MyIdError):
            myid.get_identification_data("bogus")


class MyIdViewTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.user = User.objects.create_user(phone="+998901112233", password="x")
        self.api.force_authenticate(user=self.user)

    @override_settings(
        MYID_CLIENT_HASH="hash", MYID_CLIENT_HASH_ID="hash-id", MYID_ENVIRONMENT="DEBUG"
    )
    @patch("apps.accounts.views.myid.create_session", return_value="session-1")
    def test_session_endpoint_returns_sdk_config(self, create_session_mock):
        response = self.api.post("/api/auth/myid/session/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "session_id": "session-1",
                "client_hash": "hash",
                "client_hash_id": "hash-id",
                "environment": "DEBUG",
            },
        )
        create_session_mock.assert_called_once_with(phone_number=self.user.phone)

    @patch("apps.accounts.views.myid.create_session", side_effect=myid.MyIdError("boom"))
    def test_session_endpoint_surfaces_myid_errors(self, _mock):
        response = self.api.post("/api/auth/myid/session/")
        self.assertEqual(response.status_code, 502)

    @patch("apps.accounts.views.myid.get_identification_data")
    def test_verify_endpoint_saves_verified_name(self, get_data_mock):
        get_data_mock.return_value = {
            "data": {
                "profile": {
                    "common_data": {
                        "first_name": "Aziz",
                        "last_name": "Karimov",
                        "birth_date": "1995-05-01",
                        "pinfl": "12345678901234",
                    }
                }
            }
        }
        response = self.api.post("/api/auth/myid/verify/", {"code": "abc"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Aziz")
        self.assertEqual(self.user.last_name, "Karimov")
        self.assertEqual(self.user.full_name, "Aziz Karimov")
        self.assertEqual(self.user.pinfl, "12345678901234")
        self.assertIsNotNone(self.user.myid_verified_at)

    @patch("apps.accounts.views.myid.get_identification_data")
    def test_verify_endpoint_rejects_incomplete_profile(self, get_data_mock):
        get_data_mock.return_value = {"data": {"profile": {"common_data": {}}}}
        response = self.api.post("/api/auth/myid/verify/", {"code": "abc"}, format="json")
        self.assertEqual(response.status_code, 502)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "")
