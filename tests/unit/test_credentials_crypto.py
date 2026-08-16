from __future__ import annotations

import base64
import json

import pytest

from commerce.config import Settings
from commerce.credentials import (
    CredentialCipher,
    CredentialConfigurationError,
    CredentialDecryptionError,
    EncryptedCredential,
)
from commerce.models import ShopCredential


def _cipher(*, active: str = "v1", include_v2: bool = False) -> CredentialCipher:
    keys = {"v1": b"1" * 32}
    if include_v2:
        keys["v2"] = b"2" * 32
    return CredentialCipher(keys, active)


def test_credentials_use_authenticated_encryption_and_aad() -> None:
    secret = "seller-access-token-do-not-leak"
    encrypted = _cipher().encrypt(
        {"access_token": secret, "refresh_token": "refresh-value"},
        shop_id=7,
        credential_type="OAUTH",
    )
    assert secret.encode() not in encrypted.ciphertext
    assert (
        _cipher().decrypt(encrypted, shop_id=7, credential_type="OAUTH")["access_token"] == secret
    )
    with pytest.raises(CredentialDecryptionError):
        _cipher().decrypt(encrypted, shop_id=8, credential_type="OAUTH")


def test_tamper_and_wrong_key_fail_without_secret_leakage() -> None:
    secret = "platform-secret-value"
    encrypted = _cipher().encrypt({"secret": secret}, shop_id=7, credential_type="APP_KEY")
    tampered = EncryptedCredential(
        key_id=encrypted.key_id,
        nonce=encrypted.nonce,
        ciphertext=encrypted.ciphertext[:-1] + bytes([encrypted.ciphertext[-1] ^ 1]),
    )
    for candidate in (
        tampered,
        EncryptedCredential("missing", encrypted.nonce, encrypted.ciphertext),
    ):
        with pytest.raises(CredentialDecryptionError) as error:
            _cipher().decrypt(candidate, shop_id=7, credential_type="APP_KEY")
        assert secret not in str(error.value)


def test_keyring_settings_are_validated_and_not_represented() -> None:
    encoded = base64.b64encode(b"k" * 32).decode()
    settings = Settings(
        _env_file=None,
        credential_encryption_keys=json.dumps({"primary": encoded}),
        credential_active_key_id="primary",
    )
    assert CredentialCipher.from_settings(settings).active_key_id == "primary"
    assert encoded not in repr(settings)
    with pytest.raises(CredentialConfigurationError):
        CredentialCipher.from_settings(
            Settings(
                _env_file=None,
                credential_encryption_keys='{"primary":"invalid"}',
                credential_active_key_id="primary",
            )
        )


def test_shop_credential_schema_has_no_plaintext_secret_columns() -> None:
    column_names = set(ShopCredential.__table__.columns.keys())
    assert {"key_id", "nonce", "encrypted_payload"}.issubset(column_names)
    assert not column_names.intersection(
        {"access_token", "refresh_token", "app_secret", "client_secret", "plaintext"}
    )


def test_credential_payload_is_bounded_for_mysql_blob_portability() -> None:
    with pytest.raises(ValueError):
        _cipher().encrypt(
            {"access_token": "x" * (33 * 1024)},
            shop_id=7,
            credential_type="OAUTH",
        )
