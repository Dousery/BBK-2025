import os
import jwt
from typing import Optional, Dict, Any
from fastapi import HTTPException, status
import httpx

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "secret-key-12345")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8005")


def verify_token(token: str) -> Dict[str, Any]:
    """Verify JWT token and return payload"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )


async def verify_token_with_auth_service(token: str) -> Dict[str, Any]:
    """Verify token by calling auth service"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{AUTH_SERVICE_URL}/verify",
                json={"token": token},
                timeout=5.0
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("valid"):
                    return data.get("payload", {})
                else:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=data.get("error", "Invalid token")
                    )
            else:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Auth service unavailable"
                )
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service unavailable"
        )


def get_user_id_from_token(token: str) -> Optional[str]:
    """Extract user_id from token"""
    try:
        payload = verify_token(token)
        return payload.get("user_id")
    except:
        return None


def get_username_from_token(token: str) -> Optional[str]:
    """Extract username from token"""
    try:
        payload = verify_token(token)
        return payload.get("sub")
    except:
        return None

