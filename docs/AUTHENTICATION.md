# Authentication System

This document explains the JWT + Refresh Token authentication system implemented for the Shuushuu API.

## Overview

The API uses a modern **JWT + Refresh Token** authentication system with the following features:

- **Short-lived access tokens** (JWT, 15 minutes) for API requests
- **Long-lived refresh tokens** (30 days) stored as HTTPOnly cookies
- **Token rotation** for enhanced security
- **Reuse detection** to prevent token theft
- **Backward compatibility** with legacy SHA1+salt passwords from PHP codebase
- **Automatic password migration** from SHA1 to bcrypt on login

## Security Features

### 1. Token Rotation
Every time a refresh token is used to get a new access token, the refresh token is rotated (replaced with a new one). This limits the damage if a refresh token is stolen.

### 2. Reuse Detection
If an already-used (revoked) refresh token is presented, this indicates potential token theft. The system automatically revokes all tokens in the same "family" (all tokens from that login session).

### 3. HTTPOnly Cookies
Refresh tokens are stored in HTTPOnly cookies, making them inaccessible to JavaScript and protecting against XSS attacks.

### 4. Password Migration
The system supports both legacy SHA1+salt passwords (from PHP) and modern bcrypt hashes. When a user with a legacy password logs in, their password is automatically migrated to bcrypt.

## API Endpoints

### POST `/api/v1/auth/login`
Login and receive access token + refresh token.

**Request:**
```json
{
  "username": "alice",
  "password": "secretpassword"
}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 900
}
```

**Cookies Set:**
- `refresh_token`: HTTPOnly, Secure (in production), SameSite=Lax, 30 days

---

### POST `/api/v1/auth/refresh`
Get a new access token using refresh token.

**Request:** No body (refresh token sent via cookie)

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 900
}
```

**Notes:**
- Old refresh token is revoked
- New refresh token is set as cookie
- If revoked token is reused, all session tokens are revoked

---

### POST `/api/v1/auth/logout`
Logout from current device.

**Request:** No body (refresh token sent via cookie)

**Response:**
```json
{
  "message": "Successfully logged out"
}
```

---

### POST `/api/v1/auth/logout-all`
Logout from all devices (requires authentication).

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response:**
```json
{
  "message": "Successfully logged out from all devices"
}
```

---

### GET `/api/v1/auth/me`
Get current user information (requires authentication).

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response:**
```json
{
  "user_id": 123,
  "username": "alice",
  "email": "alice@example.com",
  "active": 1,
  "admin": 0,
  "date_joined": "2024-01-15T10:30:00Z",
  "last_login": "2024-03-20T15:45:00Z"
}
```

---

### POST `/api/v1/auth/change-password`
Change password and logout from all devices (requires authentication).

**Headers:**
```
Authorization: Bearer <access_token>
```

**Request:**
```json
{
  "current_password": "oldpassword",
  "new_password": "newpassword123"
}
```

**Response:**
```json
{
  "message": "Password changed successfully. Please login again with your new password."
}
```

## Frontend Integration

### React/Axios Example

```javascript
import axios from 'axios';

// Create axios instance
const api = axios.create({
  baseURL: 'http://localhost:8000/api/v1',
  withCredentials: true, // Important! Sends cookies
});

let accessToken = null;

// Request interceptor - add token to headers
api.interceptors.request.use(
  (config) => {
    if (accessToken) {
      config.headers.Authorization = `Bearer ${accessToken}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor - handle token refresh
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // If 401 and we haven't retried yet
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;

      try {
        // Try to refresh token
        const { data } = await axios.post(
          'http://localhost:8000/api/v1/auth/refresh',
          {},
          { withCredentials: true }
        );

        // Update access token
        accessToken = data.access_token;

        // Retry original request
        originalRequest.headers.Authorization = `Bearer ${accessToken}`;
        return api(originalRequest);
      } catch (refreshError) {
        // Refresh failed - redirect to login
        accessToken = null;
        window.location.href = '/login';
        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  }
);

// Login function
export async function login(username, password) {
  const { data } = await api.post('/auth/login', { username, password });
  accessToken = data.access_token;
  return data;
}

// Logout function
export async function logout() {
  await api.post('/auth/logout');
  accessToken = null;
}

// Make authenticated requests
export async function getPosts() {
  const { data } = await api.get('/posts');
  return data;
}

export default api;
```

### Vue 3 Example

```javascript
// api.js
import axios from 'axios';
import { ref } from 'vue';

const accessToken = ref(null);

const api = axios.create({
  baseURL: 'http://localhost:8000/api/v1',
  withCredentials: true,
});

api.interceptors.request.use((config) => {
  if (accessToken.value) {
    config.headers.Authorization = `Bearer ${accessToken.value}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401 && !error.config._retry) {
      error.config._retry = true;
      try {
        const { data } = await axios.post(
          'http://localhost:8000/api/v1/auth/refresh',
          {},
          { withCredentials: true }
        );
        accessToken.value = data.access_token;
        error.config.headers.Authorization = `Bearer ${data.access_token}`;
        return api(error.config);
      } catch {
        accessToken.value = null;
        router.push('/login');
      }
    }
    return Promise.reject(error);
  }
);

export { api, accessToken };
```

### Fetch API Example (No Library)

```javascript
// auth.js
let accessToken = null;

async function fetchWithAuth(url, options = {}) {
  // Add authorization header if we have a token
  if (accessToken) {
    options.headers = {
      ...options.headers,
      'Authorization': `Bearer ${accessToken}`,
    };
  }

  // Add credentials to send cookies
  options.credentials = 'include';

  let response = await fetch(url, options);

  // If 401, try to refresh token
  if (response.status === 401 && !options._retry) {
    const refreshResponse = await fetch('/api/v1/auth/refresh', {
      method: 'POST',
      credentials: 'include',
    });

    if (refreshResponse.ok) {
      const data = await refreshResponse.json();
      accessToken = data.access_token;

      // Retry original request
      options._retry = true;
      options.headers = {
        ...options.headers,
        'Authorization': `Bearer ${accessToken}`,
      };
      response = await fetch(url, options);
    } else {
      // Refresh failed - redirect to login
      accessToken = null;
      window.location.href = '/login';
      throw new Error('Authentication failed');
    }
  }

  return response;
}

export async function login(username, password) {
  const response = await fetch('/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
    credentials: 'include',
  });

  if (!response.ok) {
    throw new Error('Login failed');
  }

  const data = await response.json();
  accessToken = data.access_token;
  return data;
}

export async function logout() {
  await fetchWithAuth('/api/v1/auth/logout', { method: 'POST' });
  accessToken = null;
}

export { fetchWithAuth };
```

## Database Setup

### Run Migrations

```bash
# Run the migrations to create refresh_tokens table and update users table
uv run alembic upgrade head
```

This will:
1. Create the `refresh_tokens` table
2. Extend the `users.password` field to 255 characters
3. Add `users.password_type` field ('md5' or 'bcrypt')

### Legacy Password Migration

Users with legacy SHA1+salt passwords will be automatically migrated to bcrypt when they log in. The system:

1. Checks `password_type` field ('md5' or 'bcrypt')
2. If 'md5', uses SHA1+salt verification
3. On successful login, re-hashes password with bcrypt
4. Updates `password_type` to 'bcrypt'
5. Future logins use bcrypt

No action required from users!

## Configuration

Update your `.env` file:

```env
# Secret key for JWT tokens (MUST be changed in production!)
SECRET_KEY=your-very-secret-key-at-least-32-characters-long

# JWT algorithm
ALGORITHM=HS256

# Access token expiration (minutes)
ACCESS_TOKEN_EXPIRE_MINUTES=15

# Refresh token expiration (days)
REFRESH_TOKEN_EXPIRE_DAYS=30

# Environment (affects cookie security)
ENVIRONMENT=development  # or 'production'
```

**Important:** In production, set `ENVIRONMENT=production` to enable:
- Secure flag on cookies (HTTPS only)
- Stricter security settings

## Security Best Practices

### Backend
✅ Short-lived access tokens (15 min)
✅ Refresh tokens stored hashed in database
✅ HTTPOnly cookies for refresh tokens
✅ Token rotation on refresh
✅ Reuse detection
✅ Bcrypt password hashing
✅ Automatic password migration

### Frontend
✅ Store access tokens in memory (not localStorage!)
✅ Use `credentials: 'include'` / `withCredentials: true`
✅ Implement automatic token refresh on 401
✅ Clear tokens on logout

### Don'ts
❌ Don't store access tokens in localStorage (XSS vulnerable)
❌ Don't use long-lived access tokens
❌ Don't skip CORS configuration
❌ Don't disable HTTPOnly on refresh token cookies

## Protecting Routes

### In FastAPI

```python
from app.core.auth import CurrentUser

@router.get("/protected")
async def protected_route(current_user: CurrentUser):
    return {"message": f"Hello {current_user.username}!"}
```

The `CurrentUser` dependency automatically:
1. Extracts JWT from Authorization header
2. Verifies token signature and expiration
3. Loads user from database
4. Checks if user is active
5. Returns user object or raises 401

### Admin-Only Routes

```python
from app.core.auth import AdminUser

@router.delete("/users/{user_id}")
async def delete_user(user_id: int, admin: AdminUser):
    # Only admins can access this
    ...
```

### Optional Authentication

```python
from app.core.auth import OptionalCurrentUser

@router.get("/posts")
async def list_posts(current_user: OptionalCurrentUser):
    # Different behavior for authenticated vs anonymous
    if current_user:
        # Show personalized content
        ...
    else:
        # Show public content
        ...
```

## Troubleshooting

### "Invalid refresh token" Error
- Refresh token expired (>30 days old)
- Refresh token was revoked (logout/password change)
- User logged out

**Solution:** Redirect user to login page

### "Refresh token reuse detected"
- A revoked refresh token was used
- Indicates potential token theft
- All tokens in family are revoked

**Solution:** Force user to re-login

### CORS Issues
- Frontend on different domain than API
- Missing `credentials: 'include'` / `withCredentials: true`
- CORS not configured correctly

**Solution:** Check CORS settings in `app/config.py`:
```python
CORS_ORIGINS=["http://localhost:3000"]  # Add your frontend URL
```

### Cookies Not Set
- Check if `credentials: 'include'` is set in fetch/axios
- Check if CORS allows credentials
- In production, ensure HTTPS is used (Secure flag)

## Token Flow Diagram

```
┌─────────────┐                          ┌─────────────┐
│   Frontend  │                          │     API     │
└──────┬──────┘                          └──────┬──────┘
       │                                        │
       │  POST /auth/login                     │
       │  {username, password}                 │
       ├──────────────────────────────────────>│
       │                                        │
       │  access_token + Set-Cookie            │
       │  (refresh_token, HTTPOnly)            │
       │<──────────────────────────────────────┤
       │                                        │
       │  GET /posts                           │
       │  Authorization: Bearer <token>        │
       ├──────────────────────────────────────>│
       │                                        │
       │  200 OK {posts}                       │
       │<──────────────────────────────────────┤
       │                                        │
       │  (15 minutes later...)                │
       │                                        │
       │  GET /posts                           │
       │  Authorization: Bearer <expired>      │
       ├──────────────────────────────────────>│
       │                                        │
       │  401 Unauthorized                     │
       │<──────────────────────────────────────┤
       │                                        │
       │  POST /auth/refresh                   │
       │  Cookie: refresh_token                │
       ├──────────────────────────────────────>│
       │                                        │
       │  new access_token + new refresh       │
       │<──────────────────────────────────────┤
       │                                        │
       │  GET /posts (retry)                   │
       │  Authorization: Bearer <new>          │
       ├──────────────────────────────────────>│
       │                                        │
       │  200 OK {posts}                       │
       │<──────────────────────────────────────┤
       │                                        │
```

## Testing

### Test Login
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "testuser", "password": "testpass"}' \
  -c cookies.txt

# Save access token from response
```

### Test Protected Route
```bash
curl http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer <access_token>"
```

### Test Refresh
```bash
curl -X POST http://localhost:8000/api/v1/auth/refresh \
  -b cookies.txt \
  -c cookies.txt
```

### Test Logout
```bash
curl -X POST http://localhost:8000/api/v1/auth/logout \
  -b cookies.txt
```

## Additional Resources

- [JWT.io](https://jwt.io/) - JWT debugger
- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [FastAPI Security Documentation](https://fastapi.tiangolo.com/tutorial/security/)
