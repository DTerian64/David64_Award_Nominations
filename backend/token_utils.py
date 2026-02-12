"""
Secure Token Generation for Email Action Links
Generates time-limited, signed tokens for approve/reject actions in emails
"""

import os
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Literal

# Secret key for signing tokens (store in Azure Key Vault for production)
SECRET_KEY = os.getenv("EMAIL_ACTION_SECRET_KEY", "default-secret-key")  # Replace with secure key in production
TOKEN_EXPIRY_HOURS = int(os.getenv("EMAIL_ACTION_TOKEN_EXPIRY_HOURS", "72"))  # 3 days default

def generate_action_token(
    nomination_id: int,
    action: Literal["approve", "reject"],
    approver_id: int,
    expiry_hours: Optional[int] = None
) -> str:
    """
    Generate a secure, time-limited token for email action links
    
    Args:
        nomination_id: The nomination ID
        action: "approve" or "reject"
        approver_id: User ID of the approver (for validation)
        expiry_hours: Token expiration in hours (default: TOKEN_EXPIRY_HOURS)
    
    Returns:
        str: JWT token containing the action details
    
    Example:
        >>> token = generate_action_token(123, "approve", 456)
        >>> url = f"https://api.example.com/api/nominations/email-action?token={token}"
    """
    expiry = expiry_hours or TOKEN_EXPIRY_HOURS
    
    now = datetime.now(timezone.utc)
    payload = {
        "nomination_id": int(nomination_id),
        "action": action,
        "approver_id": int(approver_id),
        "exp": now + timedelta(hours=expiry),
        "iat": now
    }
    
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    return token


def verify_action_token(token: str) -> Optional[Dict]:
    """
    Verify and decode an action token
    
    Args:
        token: JWT token from email link
    
    Returns:
        dict: Decoded token payload with nomination_id, action, approver_id
        None: If token is invalid or expired
    
    Example:
        >>> payload = verify_action_token(token)
        >>> if payload:
        ...     nomination_id = payload["nomination_id"]
        ...     action = payload["action"]
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        print("⚠️ Token has expired")
        return None
    except jwt.InvalidTokenError as e:
        print(f"⚠️ Invalid token: {e}")
        return None


def get_action_url(
    base_url: str,
    nomination_id: int,
    action: Literal["approve", "reject"],
    approver_id: int
) -> str:
    """
    Generate a complete action URL for email buttons
    
    Args:
        base_url: Base API URL (e.g., "https://api.example.com")
        nomination_id: Nomination ID
        action: "approve" or "reject"
        approver_id: Approver user ID
    
    Returns:
        str: Complete URL with token
    
    Example:
        >>> url = get_action_url(
        ...     "https://award-api-eastus.lemonpond.eastus.azurecontainerapps.io",
        ...     123,
        ...     "approve",
        ...     456
        ... )
    """
    token = generate_action_token(nomination_id, action, approver_id)
    return f"{base_url}/api/nominations/email-action?token={token}"


# Test function
if __name__ == "__main__":
    # Test token generation and verification
    token = generate_action_token(123, "approve", 456)
    print(f"Generated token: {token}")
    
    payload = verify_action_token(token)
    print(f"Verified payload: {payload}")
    
    # Test URL generation
    url = get_action_url(
        "https://award-api-eastus.lemonpond.eastus.azurecontainerapps.io",
        123,
        "approve",
        456
    )
    print(f"Action URL: {url}")
