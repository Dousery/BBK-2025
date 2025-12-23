# Auth Service

Authentication service for Atlas E-commerce platform. Provides JWT-based authentication and user management.

## Features

- User registration and login
- JWT token generation and validation
- Password hashing with bcrypt
- User profile management
- Token verification endpoint for inter-service communication

## Endpoints

### Public Endpoints

- `POST /register` - Register a new user
- `POST /login` - Login and get JWT token
- `POST /verify` - Verify JWT token
- `GET /health` - Health check

### Protected Endpoints (require JWT token)

- `GET /me` - Get current user information
- `GET /users/{user_id}` - Get user by ID (for inter-service communication)

## Environment Variables

- `DATABASE_URL` - PostgreSQL connection string
- `JWT_SECRET_KEY` - Secret key for JWT token signing
- `JWT_ALGORITHM` - JWT algorithm (default: HS256)
- `JWT_EXPIRATION_HOURS` - Token expiration time in hours (default: 24)

## Default Admin User

On first startup, a default admin user is created:
- Username: `admin`
- Password: `admin123`
- Email: `admin@atlas.com`

**Important**: Change the default password in production!

## Usage Example

### Register
```bash
curl -X POST http://localhost:8005/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "john_doe",
    "email": "john@example.com",
    "password": "secure_password",
    "full_name": "John Doe"
  }'
```

### Login
```bash
curl -X POST http://localhost:8005/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "john_doe",
    "password": "secure_password"
  }'
```

### Use Token
```bash
curl -X GET http://localhost:8005/me \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```


