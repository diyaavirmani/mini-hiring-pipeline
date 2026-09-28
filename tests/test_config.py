"""Secure and predictable environment configuration tests."""

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from hiring_pipeline.config import ConfigurationError, get_settings


class ConfigurationTestCase(unittest.TestCase):
    def test_required_secret_and_supported_database_are_validated(self):
        environment = {
            "APP_SECRET_KEY": "test-configuration-secret-value-123456",
            "APP_ENV": "production",
            "APP_TIMEZONE": "Asia/Kolkata",
            "DATABASE_URL": "sqlite:////tmp/hiring-pipeline-config-test.sqlite3",
            "GEMINI_API_KEY": "optional-test-key",
            "GEMINI_MODEL": "gemini-test-model",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = get_settings()
        self.assertEqual(
            settings.database_path,
            Path("/tmp/hiring-pipeline-config-test.sqlite3").resolve(),
        )
        self.assertEqual(settings.gemini_api_key, "optional-test-key")
        self.assertEqual(settings.gemini_model, "gemini-test-model")

    def test_missing_or_example_secret_is_rejected(self):
        for secret in (None, "short", "replace-with-at-least-32-random-characters"):
            with self.subTest(secret=secret):
                environment = {"APP_SECRET_KEY": secret} if secret is not None else {}
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(ConfigurationError):
                        get_settings()

    def test_bad_timezone_database_url_and_model_are_rejected(self):
        valid = {
            "APP_SECRET_KEY": "test-configuration-secret-value-123456",
            "APP_ENV": "test",
            "APP_TIMEZONE": "UTC",
            "DATABASE_URL": "sqlite:////tmp/config-test.sqlite3",
            "GEMINI_MODEL": "gemini-test",
        }
        invalid_values = (
            ("APP_TIMEZONE", "Not/A_Timezone"),
            ("DATABASE_URL", "postgresql://database"),
            ("GEMINI_MODEL", "https://provider.test/model"),
        )
        for key, value in invalid_values:
            with self.subTest(key=key):
                environment = {**valid, key: value}
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(ConfigurationError):
                        get_settings()


if __name__ == "__main__":
    unittest.main()
