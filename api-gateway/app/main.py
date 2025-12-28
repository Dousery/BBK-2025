import os
import sys
import jwt
import httpx
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware

shared_path = os.path.join(os.path.dirname(__file__), '../../shared')
if os.path.exists(shared_path):
    sys.path.insert(0, shared_path)
else:
    # In Docker container, shared is at /app/shared
    sys.path.insert(0, '/app/shared')

from logging_config import setup_logging, get_correlation_id
from correlation_middleware import CorrelationIDMiddleware

APP_NAME = "api-gateway"
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "secret-key-12345")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")

logger = setup_logging(APP_NAME, os.getenv("LOG_LEVEL", "INFO"))

# Service URLs
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8005")
PRODUCT_SERVICE_URL = os.getenv("PRODUCT_SERVICE_URL", "http://product-service:8003")
BASKET_SERVICE_URL = os.getenv("BASKET_SERVICE_URL", "http://basket-service:8004")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://payment-service:8001")

# Public endpoints that don't require authentication
PUBLIC_PATHS = [
    "/health",
    "/auth/register",
    "/auth/login",
    "/auth/verify",
    "/products",  
]

app = FastAPI(title="Atlas E-commerce API Gateway")

app.add_middleware(CorrelationIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)


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


def is_public_path(path: str) -> bool:
    """Check if path is public (doesn't require authentication)"""
    for public_path in PUBLIC_PATHS:
        if path.startswith(public_path):
            return True
    return False


async def get_current_user_id(request: Request) -> Optional[str]:
    """Get user_id from JWT token if authentication is required"""
    path = request.url.path
    
    # Public paths don't need authentication
    if is_public_path(path):
        return None
    
    # Protected paths require authentication
    authorization = request.headers.get("Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    token = authorization.replace("Bearer ", "")
    payload = verify_token(token)
    user_id = payload.get("user_id")
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: user_id not found"
        )
    
    return user_id


@app.get("/health")
async def health() -> Dict[str, str]:
    logger.debug("Health check requested")
    return {"status": "ok", "service": APP_NAME}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def gateway(request: Request, path: str):
    """API Gateway - routes requests to appropriate microservices"""
    correlation_id = get_correlation_id()
    
    logger.info("Request received", extra={
        "extra_fields": {
            "method": request.method,
            "path": path,
            "client_ip": request.client.host if request.client else None
        }
    })
    
    # Determine target service based on path
    if path.startswith("auth/"):
        service_path = path.replace("auth/", "")
        target_url = f"{AUTH_SERVICE_URL}/{service_path}"
        target_service = "auth-service"
        
    elif path.startswith("products"):
        target_url = f"{PRODUCT_SERVICE_URL}/{path}"
        target_service = "product-service"

    elif path.startswith("basket"):
        target_url = f"{BASKET_SERVICE_URL}/{path}"
        target_service = "basket-service"

    elif path.startswith("pay"):
        target_url = f"{PAYMENT_SERVICE_URL}/{path}"
        target_service = "payment-service"
        
    else:
        logger.warning("Unknown route", extra={
            "extra_fields": {
                "path": path
            }
        })
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown route: {path}"
        )
    
    # Check authentication for protected routes
    try:
        user_id = await get_current_user_id(request)
    except HTTPException as e:
        logger.warning("Authentication failed", extra={
            "extra_fields": {
                "detail": e.detail,
                "status_code": e.status_code
            }
        })
        raise
    
    # Get request body if exists
    body = None
    if request.method in ["POST", "PUT", "PATCH"]:
        try:
            body = await request.body()
        except:
            pass
    
    # Get headers
    headers = dict(request.headers)
    
    # Add correlation ID to forwarded request
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id
    
    # Remove host header to avoid conflicts
    headers.pop("host", None)
    headers.pop("content-length", None)
    
    # Forward request to target service
    try:
        logger.debug("Forwarding request", extra={
            "extra_fields": {
                "target_service": target_service,
                "target_url": target_url
            }
        })
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
                params=dict(request.query_params)
            )
            
            logger.info("Response received", extra={
                "extra_fields": {
                    "target_service": target_service,
                    "status_code": response.status_code
                }
            })
            
            # Return response
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type", "application/json")
            )
    except httpx.RequestError as e:
        logger.error("Service unavailable", extra={
            "extra_fields": {
                "target_service": target_service,
                "error": str(e)
            }
        }, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Service unavailable: {str(e)}"
        )

