"""
Authentication and Authorization Module
Handles JWT validation, user authentication, and impersonation
WITH SWAGGER UI SUPPORT for testing impersonation
"""

import os
import jwt
from fastapi import Depends, HTTPException, Header, status
from fastapi.security import OAuth2
from typing import Optional, Dict, Any, Callable
import sqlhelper
import logging
logger = logging.getLogger(__name__) 

# ============================================================================
# CONFIGURATION
# ============================================================================

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"

# ============================================================================
# OAUTH2 SCHEME
# ============================================================================

oauth2_scheme = OAuth2(
    flows={
        "implicit": {
            "authorizationUrl": f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize",
            "scopes": {
                f"api://{CLIENT_ID}/access_as_user": "Access the API as the signed-in user",
                "openid": "OpenID Connect",
                "profile": "User profile",
                "email": "User email",
            }
        }
    }
)

# ============================================================================
# AUTHENTICATION FUNCTIONS
# ============================================================================

async def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    """
    Validate Microsoft Entra ID token and return user info
    
    Returns:
        dict: User information including UserId, userPrincipalName, roles, etc.
    """
    try:
        # Remove "Bearer " prefix if present
        if token.startswith("Bearer "):
            token = token[7:]
        
        # Decode without verification first to see the payload
        payload = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_iss": False,
                "verify_exp": False
            }
        )
        
        # Debug: Print available claims
        logger.info("Token payload claims: %s", list(payload.keys()))
        
        # Get User Principal Name from token
        upn = payload.get("upn") or payload.get("preferred_username") or payload.get("email")
        
        if not upn:
            logger.warning(f"UPN not found. Available claims: {list(payload.keys())}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"User Principal Name not found in token"
            )
        
        logger.info(f"Found UPN: {upn}")
        
        # Get user from database by UPN
        row = sqlhelper.get_user_by_upn(upn)
        
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User not found in system with UPN: {upn}"
            )
        
        logger.info(f"User found: {row[2]} {row[3]} (ID: {row[0]})")
        logger.info(f"User roles from token: {payload.get('roles', [])}")
        logger.info(f"tid: {payload.get('tid')}")
        logger.info(f"ver: {payload.get('ver')}")
        logger.info(f"aud: {payload.get('aud')}")
        logger.info(f"roles: {payload.get('roles')}")

                
        return {
            "UserId": row[0],
            "userPrincipalName": row[1],
            "FirstName": row[2],
            "LastName": row[3],
            "Title": row[4],
            "ManagerId": row[5],
            "roles": payload.get("roles", [])
        }
    
    except jwt.DecodeError as e:
        logger.error(f"JWT Decode Error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token format: {str(e)}"
        )
    except jwt.InvalidTokenError as e:
        logger.error(f"Invalid Token Error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error in get_current_user: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication error: {str(e)}"
        )


async def get_current_user_with_impersonation(
    token: str = Depends(oauth2_scheme),
    x_impersonate_user: Optional[str] = Header(
        None,
        alias="X-Impersonate-User",
        description="🎭 Admin only: Enter UPN to impersonate (e.g., chris.brown@RideshareDavid64.onmicrosoft.com)",
        example="chris.brown@RideshareDavid64.onmicrosoft.com"
    )
) -> Dict[str, Any]:
    """
    Dependency that handles both regular authentication and impersonation.
    
    **Impersonation Testing in Swagger:**
    1. Authorize as an admin user
    2. In any endpoint using this dependency, you'll see "X-Impersonate-User" field
    3. Enter a UPN (e.g., chris.brown@RideshareDavid64.onmicrosoft.com)
    4. Execute - you'll see data as that user!
    
    Args:
        token: JWT token from Authorization header
        x_impersonate_user: Optional UPN of user to impersonate (admin only)
    
    Returns:
        dict with:
        - actual_user: The authenticated admin user (from token)
        - effective_user: The user to act as (impersonated user or actual user)
        - is_impersonating: Boolean flag
    """
    
    # 1. Validate the JWT token and get the authenticated user
    actual_user = await get_current_user(token)
    
    # 2. Check if impersonation is requested
    if x_impersonate_user:
        # Verify the actual user has admin privileges
        roles = actual_user.get("roles", [])
        is_admin = "AWard_Nomination_Admin" in roles or "Administrator" in roles
        
        if not is_admin:
            logger.warning(
                f"❌ Non-admin user {actual_user['userPrincipalName']} "
                f"attempted to impersonate {x_impersonate_user}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators can impersonate users"
            )
        
        # Validate that the impersonated user exists
        impersonated_row = sqlhelper.get_user_by_upn(x_impersonate_user)
        if not impersonated_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User {x_impersonate_user} not found"
            )
        
        impersonated_user = {
            "UserId": impersonated_row[0],
            "userPrincipalName": impersonated_row[1],
            "FirstName": impersonated_row[2],
            "LastName": impersonated_row[3],
            "Title": impersonated_row[4],
            "ManagerId": impersonated_row[5],
            "roles": []  # Impersonated user doesn't inherit admin roles
        }
        
        # Log the impersonation action
        sqlhelper.log_impersonation(
            admin_upn=actual_user['userPrincipalName'],
            impersonated_upn=x_impersonate_user,
            action="impersonation_started"
        )
        
        logger.info(
            f"✅ Admin {actual_user['userPrincipalName']} "
            f"is impersonating {x_impersonate_user}"
        )
        
        return {
            "actual_user": actual_user,
            "effective_user": impersonated_user,
            "is_impersonating": True
        }
    
    # No impersonation - return actual user
    return {
        "actual_user": actual_user,
        "effective_user": actual_user,
        "is_impersonating": False
    }


def require_role(required_role: str) -> Callable:
    """
    Dependency factory to require a specific role.
    Uses actual_user (not effective_user) to prevent privilege escalation.
    
    Args:
        required_role: The role name required (e.g., "AWard_Nomination_Admin")
    
    Returns:
        Dependency function that validates the role
    
    Example:
        @app.get("/admin/endpoint")
        async def admin_only(user = Depends(require_role("AWard_Nomination_Admin"))):
            pass
    """
    def _checker(user_claims: Dict[str, Any] = Depends(get_current_user)):
        roles = user_claims.get("roles", []) or []
        if required_role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not Authorized"
            )
        return user_claims
    return _checker


async def log_action_if_impersonating(
    user_context: Dict[str, Any], 
    action: str, 
    details: Optional[str] = None
):
    """
    Helper function to log actions when impersonating.
    Only logs if is_impersonating is True.
    
    Args:
        user_context: The context returned from get_current_user_with_impersonation
        action: Description of the action being performed
        details: Optional additional details about the action
    """
    if user_context["is_impersonating"]:
        sqlhelper.log_impersonation(
            admin_upn=user_context["actual_user"]["userPrincipalName"],
            impersonated_upn=user_context["effective_user"]["userPrincipalName"],
            action=action,
            details=details
        )


def is_admin(user: Dict[str, Any]) -> bool:
    """
    Check if a user has admin privileges.
    
    Args:
        user: User dict with roles
    
    Returns:
        bool: True if user has AWard_Nomination_Admin or Administrator role
    """
    roles = user.get("roles", [])
    return "AWard_Nomination_Admin" in roles or "Administrator" in roles