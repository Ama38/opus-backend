from django.urls import path

from .views import (
    DeleteAccountView,
    LogoutView,
    MeView,
    MockOTPStartView,
    MockOTPVerifyView,
    MyIdSessionView,
    MyIdVerifyView,
    PasswordLoginView,
)


urlpatterns = [
    path("otp/start/", MockOTPStartView.as_view(), name="mock_otp_start"),
    path("otp/verify/", MockOTPVerifyView.as_view(), name="mock_otp_verify"),
    path("login/", PasswordLoginView.as_view(), name="password_login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("delete-account/", DeleteAccountView.as_view(), name="delete_account"),
    path("me/", MeView.as_view(), name="me"),
    path("myid/session/", MyIdSessionView.as_view(), name="myid_session"),
    path("myid/verify/", MyIdVerifyView.as_view(), name="myid_verify"),
]

