from django.db import connection
from django.test import SimpleTestCase


class MigrationIntegrityTests(SimpleTestCase):
    databases = {"default"}

    def test_portfolio_post_table_has_updated_at(self):
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor,
                    "masters_masterportfoliopost",
                )
            }

        self.assertIn("updated_at", columns)
