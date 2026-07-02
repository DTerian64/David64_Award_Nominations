"""
crypto.py — Token encryption for payroll-broker
================================================
AES-256-GCM symmetric encryption for Gusto OAuth tokens stored in
dbo.payroll_tokens.

Key management
--------------
The 32-byte AES key is base64-encoded and stored as an Azure Key Vault
secret named PAYROLL-TOKEN-ENCRYPTION-KEY.  Azure Container Apps injects
it as the PAYROLL_TOKEN_ENCRYPTION_KEY environment variable at startup —
no SDK call needed at runtime.

Generate the key once:
    python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"

Store it:
    az keyvault secret set \\
        --vault-name <your-vault> \\
        --name PAYROLL-TOKEN-ENCRYPTION-KEY \\
        --value <output-from-above>

Wire format
-----------
encrypt() returns:  nonce (12 bytes) || ciphertext || GCM tag (16 bytes)
The nonce is randomly generated per call; the tag is appended by AESGCM.
All of this is stored as VARBINARY(MAX) in the DB.
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Module-level cache — loaded once at first use
_key: bytes | None = None


def _get_key() -> bytes:
    global _key
    if _key is None:
        raw = os.environ.get("PAYROLL_TOKEN_ENCRYPTION_KEY", "")
        if not raw:
            raise RuntimeError(
                "PAYROLL_TOKEN_ENCRYPTION_KEY is not set. "
                "Generate a 32-byte key, base64-encode it, store it in Key Vault "
                "as PAYROLL-TOKEN-ENCRYPTION-KEY, and wire it via kv_secret_references."
            )
        decoded = base64.b64decode(raw)
        if len(decoded) != 32:
            raise RuntimeError(
                f"PAYROLL_TOKEN_ENCRYPTION_KEY must decode to exactly 32 bytes "
                f"(AES-256). Got {len(decoded)} bytes after base64 decode."
            )
        _key = decoded
    return _key


def encrypt(plaintext: str) -> bytes:
    """
    Encrypt a plaintext string with AES-256-GCM.

    Returns nonce || ciphertext || tag as a bytes object suitable for
    storage in a VARBINARY(MAX) column.

    A fresh random 12-byte nonce is generated for every call, so two
    encryptions of the same value produce different ciphertexts.
    """
    nonce = os.urandom(12)
    ciphertext = AESGCM(_get_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ciphertext  # nonce(12) || ciphertext || tag(16)


def decrypt(ciphertext: bytes) -> str:
    """
    Decrypt bytes produced by encrypt().

    Raises cryptography.exceptions.InvalidTag if the ciphertext or nonce
    has been tampered with (integrity protection from GCM mode).
    """
    if len(ciphertext) < 28:  # 12 (nonce) + at least 0 bytes + 16 (tag)
        raise ValueError(f"Ciphertext too short to be valid ({len(ciphertext)} bytes)")
    nonce      = ciphertext[:12]
    ciphertext = ciphertext[12:]
    return AESGCM(_get_key()).decrypt(nonce, ciphertext, None).decode("utf-8")
