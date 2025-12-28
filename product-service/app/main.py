from typing import Dict, Any
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session
from models import Product
from database import get_db, create_tables

APP_NAME = "product-service"
app = FastAPI(title="Atlas Payment Modernization - Product Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    create_tables()
    print("Database tables created")
    

@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": APP_NAME}


@app.get("/products")
async def get_products(db: Session = Depends(get_db)) -> JSONResponse:
    products = db.query(Product).filter(Product.is_active == True).order_by(Product.id).all()
    products_data = []
    for product in products:
        products_data.append({
            "id": product.id,
            "name": product.name,
            "description": product.description,
            "price": product.price,
            "category": product.category,
            "stock": product.stock,
            "image": product.image
        })
    
    response = JSONResponse({
        "products": products_data,
        "total": len(products_data)
    })
    
    # Blocked caching 
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    return response

@app.get("/products/{product_id}")
async def get_product(product_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        return JSONResponse({"error": "Product not found"}, status_code=404)
    
    product_data = {
        "id": product.id,
        "name": product.name,
        "description": product.description,
        "price": product.price,
        "category": product.category,
        "stock": product.stock,
        "image": product.image
    }
    
    return JSONResponse(product_data)

@app.post("/products/check-stock")
async def check_stock(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    body = await request.json()
    items = body.get("items", [])
    insufficient_stock = []
    
    for item in items:
        product_id = item.get("productId")
        quantity = item.get("quantity", 1)
        
        # Lock the product row to prevent changes during check
        product = db.query(Product).filter(Product.id == product_id).with_for_update().first()
        
        if not product:
            insufficient_stock.append({"productId": product_id, "reason": "Product not found"})
        elif product.stock < quantity:
            insufficient_stock.append({
                "productId": product_id, 
                "requested": quantity, 
                "available": product.stock,
                "reason": "Insufficient stock"
            })
            print(f"Stock check failed: {product_id} has {product.stock}, requested {quantity}")
        else:
            print(f"Stock check passed: {product_id} has {product.stock}, requested {quantity}")
    
    # Not committing, just checking
    db.rollback()  #  Lock free
    
    return JSONResponse({
        "sufficient": len(insufficient_stock) == 0,
        "insufficient_stock": insufficient_stock
    })

@app.post("/products/update-stock")
async def update_stock(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Update product stock after successful payment with concurrency control"""
    body = await request.json()
    items = body.get("items", [])
    order_id = body.get("orderId")
    user_id = body.get("userId")
    
    print(f"Stock update request: {body}")
    print(f"Items to process: {items}")
    
    updated_products = []
    
    try:
        for item in items:
            product_id = item.get("productId")
            quantity = item.get("quantity", 1)
            
            print(f"Processing item: {product_id}, quantity: {quantity}")
            
            # Use SELECT FOR UPDATE NOWAIT to prevent race conditions with timeout
            product = db.query(Product).filter(Product.id == product_id).with_for_update(nowait=True).first()
            
            if product:
                old_stock = product.stock
                
                if product.stock < quantity:
                    print(f"Insufficient stock: {product_id} has {product.stock}, requested {quantity}")
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Insufficient stock for {product_id}: {product.stock} available, {quantity} requested"
                    )
                
                # Update stock atomically
                product.stock -= quantity
                
                print(f"Stock update: {product_id} {old_stock} -> {product.stock}")
                
                updated_products.append({
                    "productId": product_id,
                    "newStock": product.stock,
                    "sold": quantity
                })
            else:
                print(f"Product not found: {product_id}")
                raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
        
        # Committing all changes atomically
        db.commit()
        print(f"Stock update completed successfully for {len(updated_products)} products")
        
        return JSONResponse({
            "success": True,
            "updated_products": updated_products
        })
        
    except Exception as e:
        db.rollback()
        print(f"Stock update failed: {e}")
        raise HTTPException(status_code=500, detail=f"Stock update failed: {str(e)}")

