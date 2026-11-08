from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)


class WeakPasswordError(ValueError):
    pass


def check_policy(password: str, min_length: int = 12) -> None:
    if len(password) < min_length:
        raise WeakPasswordError(f"password must be at least {min_length} characters")
    if password.isdigit() or password.isalpha():
        raise WeakPasswordError("password must mix letters and other characters")
    if len(set(password)) < 5:
        raise WeakPasswordError("password is too repetitive")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
