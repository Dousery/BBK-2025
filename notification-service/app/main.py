import asyncio
import json
import os
from typing import Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer


APP_NAME = "notification-service"
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "payment.completed")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "notification-service")
NOTIFICATION_TOPIC = os.getenv("NOTIFICATION_TOPIC", "notification.successful")

app = FastAPI(title="Atlas Payment Modernization - Notification Service")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

kafka_consumer: AIOKafkaConsumer | None = None
kafka_producer: AIOKafkaProducer | None = None


@app.on_event("startup")
async def startup() -> None:
    global kafka_consumer, kafka_producer
    try:
        kafka_consumer = AIOKafkaConsumer(
            KAFKA_TOPIC,
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            group_id=KAFKA_GROUP_ID,
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            auto_offset_reset='latest',
            enable_auto_commit=True,
            auto_commit_interval_ms=1000,
            session_timeout_ms=30000,
            heartbeat_interval_ms=10000,
            max_poll_records=10
        )
        await kafka_consumer.start()
        print("✅ Kafka consumer started successfully")
        
        # Initialize producer for publishing notification events
        kafka_producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        await kafka_producer.start()
        print("✅ Kafka producer started successfully")
    except Exception as e:
        print(f"⚠️ Kafka connection failed: {e}")
        kafka_consumer = None
        kafka_producer = None
    
    async def consume_messages():
        if not kafka_consumer:
            print("⚠️ Kafka consumer not available, skipping message consumption")
            return
        
        print("🔄 Starting message consumption...")
        try:
            async for message in kafka_consumer:
                try:
                    payload = message.value
                    print(f"📨 Received message: {payload}")
                    
                    user_id = payload.get("userId")
                    transaction_id = payload.get("transactionId")
                    amount = payload.get("amount")
                    currency = payload.get("currency")
                    items = payload.get("items", [])
                    
                    print(f"📩 Processing payment notification for user {user_id}")
                    print(f"🛒 Transaction {transaction_id}: {amount} {currency}")
                    print(f"👤 User ID: {user_id}")
                    print(f"💰 Payment Details: {amount} {currency} for user {user_id}")
                    
                    # Update stock in product service
                    try:
                        import httpx
                        print(f"🛒 Items to update stock: {items}")
                        
                        if items:
                            async with httpx.AsyncClient() as client:
                                stock_update_response = await client.post(
                                    "http://product-service:8003/products/update-stock",
                                    json={
                                        "items": items,
                                        "orderId": transaction_id,
                                        "userId": user_id
                                    }
                                )
                                if stock_update_response.status_code == 200:
                                    print("✅ Stock updated successfully")
                                else:
                                    print(f"⚠️ Stock update failed: {stock_update_response.status_code}")
                        else:
                            print("⚠️ No items found in payload for stock update")
                    except Exception as e:
                        print(f"⚠️ Stock update error: {e}")
                    
                    # Publish notification successful event
                    if kafka_producer:
                        notification_event = {
                            "event": "notification.successful",
                            "userId": user_id,
                            "orderId": transaction_id,
                            "amount": amount,
                            "currency": currency,
                            "timestamp": message.timestamp,
                            "status": "notified"
                        }
                        await kafka_producer.send_and_wait(NOTIFICATION_TOPIC, notification_event)
                        print(f"✅ Notification successful event published for transaction {transaction_id}")
                        
                except Exception as e:
                    print(f"❌ Error processing notification: {e}")
                    continue
        except Exception as e:
            print(f"❌ Consumer error: {e}")

    # Start consumer in background
    asyncio.create_task(consume_messages())


@app.on_event("shutdown")
async def shutdown() -> None:
    global kafka_consumer, kafka_producer
    if kafka_consumer is not None:
        await kafka_consumer.stop()
    if kafka_producer is not None:
        await kafka_producer.stop()


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": APP_NAME}


