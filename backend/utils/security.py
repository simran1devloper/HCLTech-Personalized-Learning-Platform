"""
utils/security.py — Password hashing and verification
"""

from passlib.context import CryptContext

# bcrypt context — cost factor auto-selected
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _bcrypt_input(pw: str) -> bytes:
    """bcrypt only uses the first 72 BYTES (not characters); truncate explicitly so multibyte passwords hash consistently."""
    return pw.encode("utf-8")[:72]


def hash_password(plain_password: str) -> str:
    """
    Hash a plain-text password using bcrypt.

    Args:
        plain_password: Raw password string.

    Returns:
        Bcrypt hash string.
    """
    return pwd_context.hash(_bcrypt_input(plain_password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain-text password against a bcrypt hash.

    Args:
        plain_password: Raw password string.
        hashed_password: Stored bcrypt hash.

    Returns:
        True if match, False otherwise.
    """
    return pwd_context.verify(_bcrypt_input(plain_password), hashed_password)
