#!/bin/bash
# Shuushuu API Testing Script
# Simple workflow for testing API endpoints with curl

set -e  # Exit on error

# Colors for output (disable with NO_COLOR=1)
if [ -n "$NO_COLOR" ] || [ ! -t 1 ]; then
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    NC=''
else
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    NC='\033[0m'
fi

# Configuration
API_URL="${API_URL:-http://localhost:8000}"
CREDS_FILE="${HOME}/creds"
TOKEN_FILE="/tmp/shuu_token.txt"

# Load credentials
# if [ -f "$CREDS_FILE" ]; then
#     source "$CREDS_FILE"
# else
#     echo -e "${RED}Error: Credentials file not found at ${CREDS_FILE}${NC}"
#     exit 1
# fi

# Helper functions
print_header() {
    echo -e "\n${BLUE}===================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}===================================================${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}ℹ $1${NC}"
}

# 1. Test Health Check
test_health() {
    print_header "1. Testing Health Check"

    response=$(curl -s "${API_URL}/health")
    echo "$response" | jq '.' 2>/dev/null || echo "$response"

    if echo "$response" | grep -q "healthy"; then
        print_success "API is healthy"
    else
        print_error "API health check failed"
        return 1
    fi
}

# 2. Login and Get Token
login() {
    print_header "2. Login and Get Access Token"

    print_info "Logging in as: ${SHUU_USER}"

    response=$(curl -s -X POST "${API_URL}/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"${SHUU_USER}\",\"password\":\"${SHUU_PASS}\"}" \
        -c /tmp/shuu_cookies.txt)

    # Extract access token
    ACCESS_TOKEN=$(echo "$response" | jq -r '.access_token' 2>/dev/null)

    if [ "$ACCESS_TOKEN" != "null" ] && [ -n "$ACCESS_TOKEN" ]; then
        echo "$ACCESS_TOKEN" > "$TOKEN_FILE"
        print_success "Login successful"
        print_info "Token saved to: ${TOKEN_FILE}"
        echo "$response" | jq '.'
    else
        print_error "Login failed"
        echo "$response" | jq '.' 2>/dev/null || echo "$response"
        return 1
    fi
}

# 3. Get Current User Info
get_user_info() {
    print_header "3. Get Current User Info"

    if [ ! -f "$TOKEN_FILE" ]; then
        print_error "Not logged in. Run login first."
        return 1
    fi

    ACCESS_TOKEN=$(cat "$TOKEN_FILE")

    response=$(curl -s "${API_URL}/api/v1/auth/me" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}")

    echo "$response" | jq '.' 2>/dev/null || echo "$response"

    if echo "$response" | grep -q "user_id"; then
        print_success "User info retrieved"
    else
        print_error "Failed to get user info"
        return 1
    fi
}

# 4. List Images (with pagination)
list_images() {
    print_header "4. List Images (First Page)"

    local page=${1:-1}
    local limit=${2:-5}

    response=$(curl -s "${API_URL}/api/v1/images?page=${page}&limit=${limit}")

    echo "$response" | jq '.' 2>/dev/null || echo "$response"

    total=$(echo "$response" | jq -r '.total' 2>/dev/null)
    if [ "$total" != "null" ]; then
        print_success "Found ${total} total images"
    fi
}

# 5. Search Images by Tag
search_images() {
    print_header "5. Search Images by Tag"

    local tag_id=${1:-1}
    local limit=${2:-5}

    print_info "Searching for tag_id: ${tag_id}"

    response=$(curl -s "${API_URL}/api/v1/images?tag_id=${tag_id}&limit=${limit}")

    echo "$response" | jq '.' 2>/dev/null || echo "$response"

    total=$(echo "$response" | jq -r '.total' 2>/dev/null)
    if [ "$total" != "null" ]; then
        print_success "Found ${total} images with tag ${tag_id}"
    fi
}

# 6. Get Specific Image
get_image() {
    print_header "6. Get Specific Image"

    local image_id=${1:-1}

    print_info "Fetching image_id: ${image_id}"

    response=$(curl -s "${API_URL}/api/v1/images/${image_id}")

    echo "$response" | jq '.' 2>/dev/null || echo "$response"

    if echo "$response" | grep -q "image_id"; then
        print_success "Image retrieved"
    else
        print_error "Image not found"
        return 1
    fi
}

# 7. Upload Image
upload_image() {
    print_header "7. Upload Image"

    if [ ! -f "$TOKEN_FILE" ]; then
        print_error "Not logged in. Run login first."
        return 1
    fi

    local image_file=${1}
    local caption=${2:-"Test upload via API"}
    local tag_ids=${3:-""}

    if [ -z "$image_file" ]; then
        print_error "Usage: upload_image <image_file> [caption] [tag_ids]"
        return 1
    fi

    if [ ! -f "$image_file" ]; then
        print_error "Image file not found: ${image_file}"
        return 1
    fi

    ACCESS_TOKEN=$(cat "$TOKEN_FILE")

    print_info "Uploading: ${image_file}"
    print_info "Caption: ${caption}"
    [ -n "$tag_ids" ] && print_info "Tags: ${tag_ids}"

    response=$(curl -s -X POST "${API_URL}/api/v1/images/upload" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" \
        -F "file=@${image_file}" \
        -F "caption=${caption}" \
        -F "tag_ids=${tag_ids}")

    echo "$response" | jq '.' 2>/dev/null || echo "$response"

    image_id=$(echo "$response" | jq -r '.image_id' 2>/dev/null)
    if [ "$image_id" != "null" ] && [ -n "$image_id" ]; then
        print_success "Image uploaded successfully! Image ID: ${image_id}"
    else
        print_error "Upload failed"
        return 1
    fi
}

# 8. Get Image Tags
get_image_tags() {
    print_header "8. Get Image Tags"

    local image_id=${1}

    if [ -z "$image_id" ]; then
        print_error "Usage: get_image_tags <image_id>"
        return 1
    fi

    response=$(curl -s "${API_URL}/api/v1/images/${image_id}/tags")

    echo "$response" | jq '.' 2>/dev/null || echo "$response"

    if echo "$response" | grep -q "tags"; then
        print_success "Tags retrieved"
    fi
}

# 9. Rate Image
rate_image() {
    print_header "9. Rate Image"

    if [ ! -f "$TOKEN_FILE" ]; then
        print_error "Not logged in. Run login first."
        return 1
    fi

    local image_id=${1}
    local rating=${2:-3}

    if [ -z "$image_id" ]; then
        print_error "Usage: rate_image <image_id> [rating]"
        return 1
    fi

    ACCESS_TOKEN=$(cat "$TOKEN_FILE")

    print_info "Rating image ${image_id} with ${rating}/5 stars"

    response=$(curl -s -X POST "${API_URL}/api/v1/images/${image_id}/rate" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"rating\":${rating}}")

    echo "$response" | jq '.' 2>/dev/null || echo "$response"

    if echo "$response" | grep -q "message"; then
        print_success "Image rated"
    fi
}

# 10. Add to Favorites
add_favorite() {
    print_header "10. Add Image to Favorites"

    if [ ! -f "$TOKEN_FILE" ]; then
        print_error "Not logged in. Run login first."
        return 1
    fi

    local image_id=${1}

    if [ -z "$image_id" ]; then
        print_error "Usage: add_favorite <image_id>"
        return 1
    fi

    ACCESS_TOKEN=$(cat "$TOKEN_FILE")

    response=$(curl -s -X POST "${API_URL}/api/v1/images/${image_id}/favorite" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}")

    echo "$response" | jq '.' 2>/dev/null || echo "$response"

    if echo "$response" | grep -q "message"; then
        print_success "Added to favorites"
    fi
}

# Logout
logout() {
    print_header "Logout"

    if [ -f "$TOKEN_FILE" ]; then
        rm -f "$TOKEN_FILE"
        rm -f /tmp/shuu_cookies.txt
        print_success "Logged out (token removed)"
    else
        print_info "Not logged in"
    fi
}

# Full test workflow
run_full_test() {
    print_header "Running Full API Test Workflow"

    test_health || return 1
    login || return 1
    get_user_info || return 1
    list_images 1 5 || return 1
    search_images 1 3 || return 1
    get_image 1 || return 1

    print_success "Full test workflow completed!"
}

# Show usage
show_usage() {
    echo -e "${GREEN}Shuushuu API Testing Script${NC}"
    echo -e ""
    echo -e "${YELLOW}Usage:${NC}"
    echo -e "    $0 <command> [args]"
    echo -e ""
    echo -e "${YELLOW}Available Commands:${NC}"
    echo -e "    health              - Test API health"
    echo -e "    login               - Login and save access token"
    echo -e "    me                  - Get current user info"
    echo -e "    list [page] [limit] - List images (default: page=1, limit=5)"
    echo -e "    search <tag_id> [limit] - Search images by tag"
    echo -e "    get <image_id>      - Get specific image details"
    echo -e "    upload <file> [caption] [tag_ids] - Upload an image"
    echo -e "    tags <image_id>     - Get tags for an image"
    echo -e "    rate <image_id> [rating] - Rate an image (1-5)"
    echo -e "    favorite <image_id> - Add image to favorites"
    echo -e "    logout              - Logout and remove token"
    echo -e "    test                - Run full test workflow"
    echo -e ""
    echo -e "${YELLOW}Environment Variables:${NC}"
    echo -e "    API_URL             - API base URL (default: http://localhost:8000)"
    echo -e "    CREDS_FILE          - Path to credentials file (default: ~/creds)"
    echo -e ""
    echo -e "${YELLOW}Examples:${NC}"
    echo -e "    $0 test                                    # Run full test"
    echo -e "    $0 login                                   # Login"
    echo -e "    $0 list                                    # List first 5 images"
    echo -e "    $0 list 2 10                               # List page 2 with 10 items"
    echo -e "    $0 get 1111822                             # Get specific image"
    echo -e "    $0 upload ~/test.png \"My test image\" \"1,2,3\""
    echo -e "    $0 rate 1111822 5                          # Rate image 5 stars"
    echo -e "    $0 favorite 1111822                        # Add to favorites"
    echo -e ""
    echo -e "${YELLOW}Tips:${NC}"
    echo -e "    - Token is saved to: ${TOKEN_FILE}"
    echo -e "    - Requires jq for JSON formatting (optional but recommended)"
    echo -e "    - Use 'source ~/creds' to load credentials in your shell"
    echo -e ""
}

# Main command dispatcher
main() {
    local command=${1:-help}
    shift || true

    case "$command" in
        health)
            test_health
            ;;
        login)
            login
            ;;
        me)
            get_user_info
            ;;
        list)
            list_images "$@"
            ;;
        search)
            search_images "$@"
            ;;
        get)
            get_image "$@"
            ;;
        upload)
            upload_image "$@"
            ;;
        tags)
            get_image_tags "$@"
            ;;
        rate)
            rate_image "$@"
            ;;
        favorite|fav)
            add_favorite "$@"
            ;;
        logout)
            logout
            ;;
        test)
            run_full_test
            ;;
        help|-h|--help)
            show_usage
            ;;
        *)
            print_error "Unknown command: ${command}"
            echo ""
            show_usage
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
