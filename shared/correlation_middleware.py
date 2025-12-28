import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from shared.logging_config import set_correlation_id, get_correlation_id


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """Middleware to handle correlation IDs across requests"""
    
    CORRELATION_ID_HEADER = "X-Correlation-ID"
    
    async def dispatch(self, request: Request, call_next):
        # Extract correlation ID from header or generate new one
        correlation_id = request.headers.get(self.CORRELATION_ID_HEADER)
        if not correlation_id:
            correlation_id = str(uuid.uuid4())
        
        # Set in context for logging
        set_correlation_id(correlation_id)
        
        response = await call_next(request)
        
        # Add correlation ID to response headers
        response.headers[self.CORRELATION_ID_HEADER] = correlation_id
        
        return response

