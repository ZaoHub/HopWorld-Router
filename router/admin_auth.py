"""Store a password verifier for installations initialized with the default login."""

import hashlib
import hmac
import secrets


DEFAULT_PASSWORD = 'admin'
MIN_PASSWORD_LENGTH = 6
ADMIN_PASSWORD_KEY = 'admin_password_verifier'
ADMIN_ACCOUNT_KEY = 'admin_account'
ROUNDS = 600_000


def password_record(password):
    if not isinstance(password, str) or not MIN_PASSWORD_LENGTH <= len(password) <= 256:
        raise ValueError(f'新密码须为 {MIN_PASSWORD_LENGTH} 至 256 个字符')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ROUNDS)
    return f'{salt.hex()}:{digest.hex()}'


def matches_record(record, password):
    try:
        salt, expected = (bytes.fromhex(value) for value in record.split(':'))
        if len(salt) != 16 or len(expected) != 32 or len(password) > 256:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ROUNDS)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def matches_password(configured, verifier, supplied):
    if not isinstance(supplied, str):
        return False
    return matches_record(verifier, supplied) if verifier else hmac.compare_digest(
        supplied.encode(), configured.encode(),
    )
