import sys

from django.conf import settings
from django.apps import AppConfig


class OrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orders"

    def ready(self):
        management_commands = {
            "collectstatic",
            "makemigrations",
            "migrate",
            "shell",
            "test",
        }
        is_management_command = any(
            command in sys.argv[1:] for command in management_commands
        )
        if (
            getattr(settings, "MASTERGO_ORDER_SWEEPER_ENABLED", False)
            and not is_management_command
        ):
            from .sweeper import start_order_sweeper

            start_order_sweeper()

