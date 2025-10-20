import json
import os
import httpx
from typing import Dict, List, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import redis

APP_NAME = "basket-service"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

app = FastAPI(title="Atlas Payment Modernization - Basket Service")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Redis connection
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Prometheus metrics
requests_total = Counter("basket_requests_total", "Total requests", ["endpoint"])
request_latency = Histogram("basket_request_latency_seconds", "Request latency seconds")

@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": APP_NAME}

@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode("utf-8"), media_type=CONTENT_TYPE_LATEST)

@app.get("/basket/{user_id}")
async def get_basket(user_id: str) -> JSONResponse:
    with request_latency.time():
        requests_total.labels(endpoint="/basket/get").inc()
        
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

@app.post("/basket/{user_id}/add")
async def add_to_basket(user_id: str, request: Dict[str, Any]) -> JSONResponse:
    with request_latency.time():
        requests_total.labels(endpoint="/basket/add").inc()
        
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
        
        # Check if item already exists
        item_exists = False
        for item in basket["items"]:
            if item["productId"] == product_id:
                item["quantity"] += quantity
                item_exists = True
                break
        
        if not item_exists:
            # Get product details from product service
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
                    else:
                        # Fallback to static data if product service is unavailable
                        product_details = {
                            "productId": product_id,
                            "name": f"Product {product_id}",
                            "price": 19.99,
                            "quantity": quantity
                        }
            except Exception as e:
                print(f"⚠️ Error fetching product details: {e}")
                # Fallback to static data
                product_details = {
                    "productId": product_id,
                    "name": f"Product {product_id}",
                    "price": 19.99,
                    "quantity": quantity
                }
            basket["items"].append(product_details)
        
        # Calculate total
        basket["total"] = sum(item["price"] * item["quantity"] for item in basket["items"])
        
        # Save to Redis
        redis_client.setex(basket_key, 3600, json.dumps(basket))  # 1 hour TTL
        
        return JSONResponse(basket)

@app.delete("/basket/{user_id}/item/{product_id}")
async def remove_from_basket(user_id: str, product_id: str) -> JSONResponse:
    with request_latency.time():
        requests_total.labels(endpoint="/basket/remove").inc()
        
        basket_key = f"basket:{user_id}"
        basket_data = redis_client.get(basket_key)
        
        if not basket_data:
            raise HTTPException(status_code=404, detail="Basket not found")
        
        basket = json.loads(basket_data)
        basket["items"] = [item for item in basket["items"] if item["productId"] != product_id]
        
        # Recalculate total
        basket["total"] = sum(item["price"] * item["quantity"] for item in basket["items"])
        
        # Save to Redis
        redis_client.setex(basket_key, 3600, json.dumps(basket))
        
        return JSONResponse(basket)

@app.delete("/basket/{user_id}")
async def clear_basket(user_id: str) -> JSONResponse:
    with request_latency.time():
        requests_total.labels(endpoint="/basket/clear").inc()
        
        basket_key = f"basket:{user_id}"
        redis_client.delete(basket_key)
        
        return JSONResponse({"message": "Basket cleared", "userId": user_id})
