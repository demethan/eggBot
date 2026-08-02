import os
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from eggbot_db.secrets import (
    SecretBox,
    SecretConfigurationError,
    SecretDecryptionError,
)


class SecretBoxTests(unittest.TestCase):
    def test_round_trip_and_none(self):
        box = SecretBox(Fernet.generate_key())
        encrypted = box.encrypt("sensitive value")
        self.assertNotIn(b"sensitive value", encrypted)
        self.assertEqual(box.decrypt(encrypted), "sensitive value")
        self.assertIsNone(box.encrypt(None))
        self.assertIsNone(box.decrypt(None))

    def test_wrong_key_has_safe_error_without_secret(self):
        ciphertext = SecretBox(Fernet.generate_key()).encrypt("do-not-log-me")
        with self.assertRaisesRegex(SecretDecryptionError, "cannot be decrypted") as raised:
            SecretBox(Fernet.generate_key()).decrypt(ciphertext)
        self.assertNotIn("do-not-log-me", str(raised.exception))

    def test_environment_key_is_required(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SecretConfigurationError):
                SecretBox.from_environment()

    def test_loads_environment_key(self):
        key = Fernet.generate_key().decode("ascii")
        with patch.dict(os.environ, {SecretBox.ENVIRONMENT_KEY: key}, clear=True):
            box = SecretBox.from_environment()
        self.assertEqual(box.decrypt(box.encrypt("value")), "value")


if __name__ == "__main__":
    unittest.main()
