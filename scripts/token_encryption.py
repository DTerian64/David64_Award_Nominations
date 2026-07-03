"""
token_encryption.py
-------------------
Encrypt or decrypt a Gusto OAuth token using the same AES-256-GCM scheme
as the payroll-broker (utils/crypto.py).

The encryption key is fetched from Azure Key Vault (secret name:
PAYROLL-TOKEN-ENCRYPTION-KEY) using DefaultAzureCredential — the same
identity chain the payroll-broker uses.  Run `az login` first if you are
authenticating as yourself.

The Key Vault URL is read from KEY_VAULT_URL in .env (no command-line flag needed).

Usage
-----
  Encrypt (plaintext → SQL-ready hex):
      python token_encryption.py --encrypt --token <plaintext_token>

  Decrypt (hex or base64 → plaintext):
      python token_encryption.py --decrypt --token <hex_or_base64>

Output of --encrypt is a SQL hex literal (0x...) you can paste directly into
SSMS / Azure Data Studio:

    UPDATE dbo.payroll_tokens
    SET    access_token  = 0x<encrypt output for access token>,
           refresh_token = 0x<encrypt output for refresh token>,
           updated_at    = GETUTCDATE()
    WHERE  provider_id   = 1;

Requirements:
    pip install cryptography azure-identity azure-keyvault-secrets python-dotenv
"""

import argparse
import base64
import os
import sys

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    sys.exit("ERROR: 'cryptography' package not installed.\n       Run: pip install cryptography")

try:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
except ImportError:
    sys.exit(
        "ERROR: Azure SDK not installed.\n"
        "       Run: pip install azure-identity azure-keyvault-secrets"
    )

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional

_KV_SECRET_NAME = "PAYROLL-TOKEN-ENCRYPTION-KEY"


# ── Key loading ────────────────────────────────────────────────────────────────

def _load_key(key_vault_url: str) -> bytes:
    print(f"Fetching encryption key from Key Vault: {key_vault_url}")
    try:
        credential = DefaultAzureCredential()
        client     = SecretClient(vault_url=key_vault_url, credential=credential)
        secret     = client.get_secret(_KV_SECRET_NAME)
    except Exception as exc:
        sys.exit(
            f"ERROR: Could not fetch secret '{_KV_SECRET_NAME}' from Key Vault.\n"
            f"       Make sure you are logged in (`az login`) and have Get permission.\n"
            f"       Detail: {exc}"
        )

    raw = (secret.value or "").strip()
    if not raw:
        sys.exit(f"ERROR: Key Vault secret '{_KV_SECRET_NAME}' is empty.")

    try:
        key = base64.b64decode(raw)
    except Exception:
        sys.exit(f"ERROR: Key Vault secret '{_KV_SECRET_NAME}' is not valid base64.")

    if len(key) != 32:
        sys.exit(
            f"ERROR: Key must be 32 bytes after base64 decode. Got {len(key)} bytes."
        )

    print("  ✓ Key loaded.\n")
    return key


# ── Crypto operations ──────────────────────────────────────────────────────────

def encrypt(plaintext: str, key: bytes) -> bytes:
    """AES-256-GCM encrypt. Returns nonce(12) || ciphertext || tag(16)."""
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)


def decrypt(ciphertext: bytes, key: bytes) -> str:
    """AES-256-GCM decrypt. Raises on tampered data."""
    if len(ciphertext) < 28:
        sys.exit(
            f"ERROR: Ciphertext too short ({len(ciphertext)} bytes). "
            "Not valid AES-256-GCM output."
        )
    return AESGCM(key).decrypt(ciphertext[:12], ciphertext[12:], None).decode("utf-8")


# ── Input parsing ──────────────────────────────────────────────────────────────

def parse_bytes(value: str) -> bytes:
    """Accept either 0x<hex> (SQL style) or base64."""
    stripped = value.strip()
    if stripped.lower().startswith("0x"):
        return bytes.fromhex(stripped[2:])
    try:
        return base64.b64decode(stripped)
    except Exception:
        sys.exit("ERROR: --token value is neither valid 0x<hex> nor valid base64.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Encrypt or decrypt a payroll OAuth token using AES-256-GCM."
    )
    parser.add_argument(
        "--token",
        required=True,
        help="Plaintext token (--encrypt) or hex/base64 ciphertext (--decrypt)",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--encrypt", action="store_true", help="Encrypt plaintext → SQL hex")
    group.add_argument("--decrypt", action="store_true", help="Decrypt SQL hex or base64 → plaintext")

    args = parser.parse_args()

    key_vault_url = os.environ.get("KEY_VAULT_URL", "").strip()
    if not key_vault_url:
        sys.exit(
            "ERROR: KEY_VAULT_URL is not set.\n"
            "       Add KEY_VAULT_URL=https://<vault>.vault.azure.net/ to your .env file."
        )

    key = _load_key(key_vault_url)

    if args.encrypt:
        ciphertext = encrypt(args.token, key)
        hex_value  = ciphertext.hex().upper()
        b64_value  = base64.b64encode(ciphertext).decode()
        print(f"Encrypted token")
        print(f"  SQL hex  (paste into SSMS):  0x{hex_value}")
        print(f"  Base64   (for reference):    {b64_value}")
        print(f"\nExample UPDATE:")
        print(f"  UPDATE dbo.payroll_tokens")
        print(f"  SET    access_token = 0x{hex_value},  -- or refresh_token")
        print(f"         updated_at   = GETUTCDATE()")
        print(f"  WHERE  provider_id  = 1;")

    else:  # --decrypt
        ciphertext = parse_bytes(args.token)
        try:
            plaintext = decrypt(ciphertext, key)
        except Exception as exc:
            sys.exit(
                f"ERROR: Decryption failed — {exc}\n"
                "(Wrong key, or data is not AES-256-GCM ciphertext.)"
            )
        print(f"Decrypted token: {plaintext}")


if __name__ == "__main__":
    main()
