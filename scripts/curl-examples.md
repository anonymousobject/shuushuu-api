# Shuushuu API - curl Examples

Quick reference for common API operations using curl.

## Setup

```bash
# Load credentials
source ~/creds

# Set API URL (if different from default)
export API_URL="http://localhost:8000"
```

## Authentication

### 1. Login
```bash
# Login and get token
curl -X POST "${API_URL}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"${SHUU_USER}\",\"password\":\"${SHUU_PASS}\"}" \
  | jq '.'

# Save token to variable
TOKEN=$(curl -s -X POST "${API_URL}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"${SHUU_USER}\",\"password\":\"${SHUU_PASS}\"}" \
  | jq -r '.access_token')

echo "Token: $TOKEN"
```

### 2. Get Current User
```bash
curl "${API_URL}/api/v1/auth/me" \
  -H "Authorization: Bearer ${TOKEN}" \
  | jq '.'
```

### 3. Logout
```bash
# Note: Logout requires refresh token cookie
curl -X POST "${API_URL}/api/v1/auth/logout" \
  -b cookies.txt \
  | jq '.'
```

## Images

### List Images
```bash
# Get first page (15 images by default)
curl "${API_URL}/api/v1/images" | jq '.'

# With pagination
curl "${API_URL}/api/v1/images?page=1&limit=10" | jq '.'

# Sort by date (newest first)
curl "${API_URL}/api/v1/images?sort=date&order=desc" | jq '.'

# Sort by favorites
curl "${API_URL}/api/v1/images?sort=favorites&order=desc&limit=20" | jq '.'
```

### Search Images
```bash
# Search by tag ID
curl "${API_URL}/api/v1/images?tag_id=1" | jq '.'

# Search by multiple tags
curl "${API_URL}/api/v1/images?tag_id=1&tag_id=2" | jq '.'

# Search by user ID
curl "${API_URL}/api/v1/images?user_id=2" | jq '.'

# Complex search: tag + sort + pagination
curl "${API_URL}/api/v1/images?tag_id=1&sort=rating&order=desc&page=1&limit=5" | jq '.'
```

### Get Specific Image
```bash
# Get image by ID
curl "${API_URL}/api/v1/images/1111822" | jq '.'

# Get just the filename and dimensions
curl "${API_URL}/api/v1/images/1111822" | jq '{filename, width, height, filesize}'
```

### Upload Image
```bash
# Simple upload (requires authentication)
curl -X POST "${API_URL}/api/v1/images/upload" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "file=@/path/to/image.png" \
  -F "caption=My awesome image" \
  | jq '.'

# Upload with tags
curl -X POST "${API_URL}/api/v1/images/upload" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "file=@/path/to/image.jpg" \
  -F "caption=Cute anime girl" \
  -F "tag_ids=1,5,10" \
  | jq '.'

# Save uploaded image ID
IMAGE_ID=$(curl -s -X POST "${API_URL}/api/v1/images/upload" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "file=@test.png" \
  -F "caption=Test" \
  | jq -r '.image_id')

echo "Uploaded image ID: $IMAGE_ID"
```

### Get Image Tags
```bash
# Get all tags for an image
curl "${API_URL}/api/v1/images/1111822/tags" | jq '.'

# Get just tag names
curl "${API_URL}/api/v1/images/1111822/tags" | jq '.tags[].tag_name'
```

## Favorites

### Add to Favorites
```bash
curl -X POST "${API_URL}/api/v1/images/1111822/favorite" \
  -H "Authorization: Bearer ${TOKEN}" \
  | jq '.'
```

### Remove from Favorites
```bash
curl -X DELETE "${API_URL}/api/v1/images/1111822/favorite" \
  -H "Authorization: Bearer ${TOKEN}" \
  | jq '.'
```

### List User's Favorites
```bash
# Get current user's favorites
curl "${API_URL}/api/v1/images?favorited=true" \
  -H "Authorization: Bearer ${TOKEN}" \
  | jq '.'
```

## Ratings

### Rate Image
```bash
# Rate image (1-5 stars)
curl -X POST "${API_URL}/api/v1/images/1111822/rate" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"rating": 5}' \
  | jq '.'
```

### Get User's Rating
```bash
curl "${API_URL}/api/v1/images/1111822/rating" \
  -H "Authorization: Bearer ${TOKEN}" \
  | jq '.'
```

## Tags

### List Tags
```bash
# Get all tags
curl "${API_URL}/api/v1/tags" | jq '.'

# Get tags with pagination
curl "${API_URL}/api/v1/tags?page=1&limit=20" | jq '.'

# Search tags by name
curl "${API_URL}/api/v1/tags?search=anime" | jq '.'

# Filter by tag type (1=theme, 2=source, 3=artist, 4=character)
curl "${API_URL}/api/v1/tags?tag_type=4" | jq '.'
```

### Get Specific Tag
```bash
curl "${API_URL}/api/v1/tags/1" | jq '.'
```

## Users

### Get User Info
```bash
# Get specific user
curl "${API_URL}/api/v1/users/2" | jq '.'
```

### List Users
```bash
# List all users (admin only)
curl "${API_URL}/api/v1/users" \
  -H "Authorization: Bearer ${TOKEN}" \
  | jq '.'
```

## Health & Info

### Health Check
```bash
curl "${API_URL}/health" | jq '.'
```

### API Info
```bash
curl "${API_URL}/" | jq '.'
```

## Advanced Examples

### Download Full Image
```bash
# Get image URL and download
IMAGE_ID=1111822
URL=$(curl -s "${API_URL}/api/v1/images/${IMAGE_ID}" | jq -r '.url')
FILENAME=$(curl -s "${API_URL}/api/v1/images/${IMAGE_ID}" | jq -r '.filename + "." + .ext')

# Download the image (replace with your actual image server URL)
curl "http://your-image-server.com${URL}.ext" -o "${FILENAME}"
```

### Batch Upload
```bash
# Upload all images in a directory
for img in ~/images/*.{jpg,png,jpeg}; do
  [ -f "$img" ] || continue
  echo "Uploading: $img"
  curl -s -X POST "${API_URL}/api/v1/images/upload" \
    -H "Authorization: Bearer ${TOKEN}" \
    -F "file=@${img}" \
    -F "caption=Batch upload" \
    | jq '.image_id, .message'
  sleep 1  # Rate limiting
done
```

### Search and Download Favorites
```bash
# Get your favorite images and download them
curl -s "${API_URL}/api/v1/images?favorited=true&limit=100" \
  -H "Authorization: Bearer ${TOKEN}" \
  | jq -r '.data[] | .image_id' \
  | while read id; do
      echo "Processing image $id"
      # Download logic here
    done
```

### Get Statistics
```bash
# Get total images count
curl -s "${API_URL}/api/v1/images?limit=1" | jq '.total'

# Get top rated images
curl -s "${API_URL}/api/v1/images?sort=rating&order=desc&limit=10" \
  | jq '.data[] | {id: .image_id, rating: .bayesian_rating}'
```

## Tips

1. **Save token to file for convenience:**
   ```bash
   echo $TOKEN > /tmp/shuu_token.txt
   TOKEN=$(cat /tmp/shuu_token.txt)
   ```

2. **Use jq for better formatting:**
   ```bash
   # Colorized output
   curl "${API_URL}/api/v1/images/1" | jq -C '.' | less -R
   ```

3. **Check response headers:**
   ```bash
   curl -i "${API_URL}/api/v1/images/1"
   ```

4. **Save response to file:**
   ```bash
   curl "${API_URL}/api/v1/images" -o images.json
   ```

5. **Follow redirects:**
   ```bash
   curl -L "${API_URL}/some/endpoint"
   ```

6. **Debug mode (show request/response):**
   ```bash
   curl -v "${API_URL}/api/v1/images/1"
   ```
