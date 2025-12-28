import asyncio
import os
import sys
import random
import json
from typing import Any, Dict, Optional

import httpx
import redis
import jwt
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from aiokafka import AIOKafkaProducer

# Add shared directory to path for imports
shared_path = os.path.join(os.path.dirname(__file__), '../../shared')
if os.path.exists(shared_path):
    sys.path.insert(0, shared_path)
else:
    # In Docker container, shared is at /app/shared
    sys.path.insert(0, '/app/shared')

from logging_config import setup_logging, get_correlation_id
from correlation_middleware import CorrelationIDMiddleware

APP_NAME = "payment-service"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "payment.completed")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "secret-key-12345")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")

# Setup structured logging
logger = setup_logging(APP_NAME, os.getenv("LOG_LEVEL", "INFO"))

security = HTTPBearer(auto_error=False)

app = FastAPI(title="Atlas Payment Modernization - Payment Service")

# Add correlation ID middleware
app.add_middleware(CorrelationIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

http_client: httpx.AsyncClient | None = None
kafka_producer: AIOKafkaProducer | None = None
redis_client: redis.Redis | None = None


@app.on_event("startup")
async def on_startup() -> None:
    global http_client, kafka_producer, redis_client
    logger.info("Starting payment service")
    http_client = httpx.AsyncClient(timeout=5.0)
    
    # Initialize Redis
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()  # Test connection
        logger.info("Redis connected successfully")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}", exc_info=True)
        redis_client = None
    
    # Initialize Kafka
    try:
        kafka_producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            security_protocol="PLAINTEXT"
        )
        await kafka_producer.start()
        logger.info("Kafka producer started successfully")
    except Exception as e:
        logger.error(f"Kafka connection failed: {e}", exc_info=True)
        kafka_producer = None

@app.on_event("shutdown")
async def on_shutdown() -> None:
    global http_client, kafka_producer
    if http_client: await http_client.aclose()
    if kafka_producer: await kafka_producer.stop()

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
    correlation_id = get_correlation_id()
    logger.debug("Health check requested")
    return {"status": "ok", "service": APP_NAME, "correlation_id": correlation_id}


async def get_cart(user_id: str) -> Dict[str, Any]:
    global redis_client, http_client
    correlation_id = get_correlation_id()
    
    if not redis_client:
        logger.error("Redis service unavailable")
        raise HTTPException(status_code=503, detail="Redis service unavailable")
    
    try:
        basket_key = f"basket:{user_id}"
        basket_data = redis_client.get(basket_key)
        
        if not basket_data:
            logger.warning("Basket not found", extra={
                "extra_fields": {
                    "user_id": user_id
                }
            })
            raise HTTPException(status_code=404, detail="Basket not found")
        
        basket = json.loads(basket_data)
        
        # Check stock availability
        if http_client:
            logger.debug("Checking stock", extra={
                "extra_fields": {
                    "user_id": user_id
                }
            })
            stock_check_response = await http_client.post(
                "http://product-service:8003/products/check-stock",
                json={"items": basket.get("items", [])},
                headers={"X-Correlation-ID": correlation_id or ""}
            )
            if stock_check_response.status_code == 200:
                stock_data = stock_check_response.json()
                if not stock_data.get("sufficient", False):
                    insufficient_items = stock_data.get("insufficient_stock", [])
                    logger.warning("Insufficient stock", extra={
                        "extra_fields": {
                            "user_id": user_id,
                            "insufficient_items": insufficient_items
                        }
                    })
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Insufficient stock: {insufficient_items}"
                    )
        
        return basket
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch basket: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch basket: {str(e)}")

def simulate_payment_amount(cart: Dict[str, Any]) -> float:
    total = sum(float(i["price"]) * int(i["quantity"]) for i in cart.get("items", []))
    return round(total + random.uniform(0.0, 2.5), 2)

@app.post("/pay")
async def pay(
    request: Request,
    user_id: str = Depends(get_current_user_id)
) -> JSONResponse:
    """Process payment - requires authentication"""
    global kafka_producer
    correlation_id = get_correlation_id()
    
    logger.info("Processing payment", extra={
        "extra_fields": {
            "user_id": user_id
        }
    })
    
    try:
        cart = await get_cart(user_id)
        await asyncio.sleep(random.uniform(0.05, 0.3))
        amount = simulate_payment_amount(cart)
        txn_id = f"txn_{user_id}_{random.randint(10000, 99999)}"

        message = {
            "event": "payment.completed",
            "transactionId": txn_id,
            "userId": user_id,
            "amount": amount,
            "currency": cart.get("currency", "USD"),
            "status": "approved",
            "items": cart.get("items", []),
            "correlationId": correlation_id
        }

        if kafka_producer:
            await kafka_producer.send_and_wait(KAFKA_TOPIC, message)
            logger.info("Payment event published to Kafka", extra={
                "extra_fields": {
                    "transaction_id": txn_id,
                    "amount": amount,
                    "user_id": user_id
                }
            })
        else:
            logger.warning("Kafka producer not available, payment processed without event")

        # Clear basket after successful payment
        try:
            if http_client:
                auth_header = request.headers.get("Authorization", "")
                # Use direct service-to-service call (bypassing gateway for internal calls)
                clear_response = await http_client.delete(
                    f"http://basket-service:8004/basket",
                    headers={"Authorization": auth_header, "X-Correlation-ID": correlation_id or ""},
                    timeout=5.0
                )
                if clear_response.status_code == 200:
                    logger.info("Basket cleared", extra={
                        "extra_fields": {
                            "user_id": user_id
                        }
                    })
                else:
                    logger.warning("Failed to clear basket", extra={
                        "extra_fields": {
                            "status_code": clear_response.status_code,
                            "user_id": user_id
                        }
                    })
        except Exception as e:
            logger.error(f"Error clearing basket: {e}", exc_info=True)

        logger.info("Payment completed successfully", extra={
            "extra_fields": {
                "transaction_id": txn_id,
                "amount": amount,
                "user_id": user_id
            }
        })
        
        return JSONResponse(message)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Payment processing failed", exc_info=True, extra={
            "extra_fields": {
                "user_id": user_id,
                "error": str(exc)
            }
        })
        raise HTTPException(status_code=500, detail=str(exc))
