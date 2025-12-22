import json
import os
import httpx
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import redis
import jwt

APP_NAME = "basket-service"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "secret-key-12345")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")

security = HTTPBearer(auto_error=False)

app = FastAPI(title="Atlas Payment Modernization - Basket Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_client = redis.from_url(REDIS_URL, decode_responses=True)


def verify_token(token: str) -> Dict[str, Any]:
    """Verify JWT token and return payload"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user_id(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> str:
    """Get user_id from JWT token"""
    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    token = credentials.credentials
    payload = verify_token(token)
    user_id = payload.get("user_id")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: user_id not found")
    
    return user_id


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": APP_NAME}


@app.get("/basket")
async def get_basket(user_id: str = Depends(get_current_user_id)) -> JSONResponse:
    """Get user's basket - requires authentication"""
    basket_key = f"basket:{user_id}"
    basket_data = redis_client.get(basket_key)
    
    if not basket_data:
        return JSONResponse({
            "userId": user_id,
            "items": [],
            "total": 0.0,
            "currency": "USD"
        })
    
    basket = json.loads(basket_data)
    return JSONResponse(basket)

@app.post("/basket/add")
async def add_to_basket(
    request: Dict[str, Any],
    user_id: str = Depends(get_current_user_id)
) -> JSONResponse:
    """Add item to basket - requires authentication"""
    product_id = request.get("productId")
    quantity = request.get("quantity", 1)
    
    if not product_id:
        raise HTTPException(status_code=400, detail="productId is required")
    
    basket_key = f"basket:{user_id}"
    basket_data = redis_client.get(basket_key)
    
    if basket_data:
        basket = json.loads(basket_data)
    else:
        basket = {
            "userId": user_id,
            "items": [],
            "total": 0.0,
            "currency": "USD"
        }
    
    # Checking if item already exists
    item_exists = False
    for item in basket["items"]:
        if item["productId"] == product_id:
            item["quantity"] += quantity
            item_exists = True
            break
    
    if not item_exists:
        # Fetching product details from product service
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://product-service:8003/products/{product_id}")
                if response.status_code == 200:
                    product_data = response.json()
                    product_details = {
                        "productId": product_id,
                        "name": product_data["name"],
                        "price": product_data["price"],
                        "quantity": quantity
                    }
                    basket["items"].append(product_details)
                else:
                    raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
        except Exception as e:
            print(f"Error fetching product details: {e}")
            raise HTTPException(status_code=503, detail="Product service unavailable")
    
    basket["total"] = sum(item["price"] * item["quantity"] for item in basket["items"])
    
    # Save to Redis
    redis_client.setex(basket_key, 3600, json.dumps(basket))  # 1 hour TTL
    
    return JSONResponse(basket)

@app.delete("/basket/item/{product_id}")
async def remove_from_basket(
    product_id: str,
    user_id: str = Depends(get_current_user_id)
) -> JSONResponse:
    """Remove item from basket - requires authentication"""
    basket_key = f"basket:{user_id}"
    basket_data = redis_client.get(basket_key)
    
    if not basket_data:
        raise HTTPException(status_code=404, detail="Basket not found")
    
    basket = json.loads(basket_data)
    basket["items"] = [item for item in basket["items"] if item["productId"] != product_id]
    
    # Recalculate total
    basket["total"] = sum(item["price"] * item["quantity"] for item in basket["items"])
    
    redis_client.setex(basket_key, 3600, json.dumps(basket))
    
    return JSONResponse(basket)

@app.delete("/basket")
async def clear_basket(user_id: str = Depends(get_current_user_id)) -> JSONResponse:
    """Clear basket - requires authentication"""
    basket_key = f"basket:{user_id}"
    redis_client.delete(basket_key)
    
    return JSONResponse({"message": "Basket cleared", "userId": user_id})
