"""Encryption for secrets persisted in EggBot's SQLite database."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


class SecretConfigurationError(RuntimeError):
    pass


class SecretDecryptionError(RuntimeError):
    pass


class SecretBox:
    ENVIRONMENT_KEY = "EGGBOT_SECRET_KEY"

    def __init__(self, key: bytes):
        try:
            self._fernet = Fernet(key)
        except (TypeError, ValueError) as exc:
            raise SecretConfigurationError("EGGBOT_SECRET_KEY is invalid") from exc

    @classmethod
    def from_environment(cls) -> "SecretBox":
        value = os.environ.get(cls.ENVIRONMENT_KEY)
        if not value:
            raise SecretConfigurationError(
                f"{cls.ENVIRONMENT_KEY} must be provided by the service environment"
            )
        return cls(value.encode("ascii"))

    @classmethod
    def from_file(cls, path: Path) -> "SecretBox":
        path = Path(path)
        try:
            if path.stat().st_mode & 0o077:
                raise SecretConfigurationError(
                    "secret key file must not be accessible by group or others"
                )
            return cls(path.read_bytes().strip())
        except OSError as exc:
            raise SecretConfigurationError("secret key file cannot be read") from exc

    def encrypt(self, value: Optional[str]) -> Optional[bytes]:
        if value is None:
            return None
        return self._fernet.encrypt(value.encode("utf-8"))

    def decrypt(self, value: Optional[bytes]) -> Optional[str]:
        if value is None:
            return None
        try:
            return self._fernet.decrypt(value).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise SecretDecryptionError("stored secret cannot be decrypted") from exc
