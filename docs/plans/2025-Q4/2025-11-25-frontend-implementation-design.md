# Frontend Implementation Design

**Date:** 2025-11-25
**Status:** Approved
**Author:** Design brainstorming session

## Overview

Design for implementing a modern web frontend for the shuushuu-api FastAPI backend. The goal is to create an MVP that eventually replaces the legacy PHP frontend entirely, starting with core browsing, authentication, and upload features.

## Goals & Constraints

**Long-term Goal:** Complete replacement of legacy PHP frontend

**MVP Approach:** Start small and evolve into full-featured frontend

**User Experience Requirements:**
- Traditional page navigation with real URLs (not full SPA)
- SEO-friendly for image discovery
- Dynamic/interactive components where they improve UX (forms, tag input, etc.)
- Minimal but professional design

**Technical Constraints:**
- Limited frontend development experience
- Some TypeScript familiarity
- Not strong with CSS/design
- Self-hosted deployment preferred

## Technology Stack

### Frontend Framework: SvelteKit

**Rationale:**
- Best learning curve for backend developers - feels like enhanced HTML
- Built-in server-side rendering (SSR) for SEO
- File-based routing matches mental model
- TypeScript support out of the box
- Easy to add dynamic behavior exactly where needed
- Progressive enhancement approach (works without JS, better with JS)

**Why not alternatives:**
- **Not Next.js:** Steeper learning curve (React hooks, state management) when SvelteKit is simpler
- **Not HTMX + Jinja2:** MVP includes complex UX (upload with preview, tag autocomplete) that's easier with a proper framework

### Component Library: shadcn-svelte

**Rationale:**
- Copy-paste components into project (own the code, not a dependency)
- Built on Tailwind CSS but don't need to know it
- Minimal, professional design aesthetic
- Accessible by default
- Components: Button, Card, Input, Dialog, Select, Badge, Skeleton

**Trade-off:**
- Manual updates (re-copy components) vs auto npm updates
- Benefit: No breaking changes from library updates, full customization

### Development Environment

- **Node.js:** Latest LTS
- **Package Manager:** npm (comes with Node)
- **TypeScript:** Yes
- **Linting:** ESLint + Prettier

## Architecture

### System Communication

```
User Browser
    ↓
SvelteKit App (Port 5173 dev / 3000 prod)
    ↓ HTTP/HTTPS requests
FastAPI Backend (Port 8000)
    ↓
MariaDB + Redis
```

### Separation of Concerns

- **Frontend:** Separate project (`shuushuu-frontend/`)
- **Backend:** Existing FastAPI project (`shuushuu-api/`)
- **Communication:** HTTP API calls

**Benefits:**
- Clean separation of concerns
- Can deploy independently
- Backend remains pure API (reusable for mobile apps)
- Frontend can be static-hosted (cheaper, faster)

### Authentication Flow

Backend provides JWT access tokens (15min) + HTTPOnly refresh tokens (30d).

**Frontend Flow:**
1. **Login:** POST to `/api/v1/auth/login` → receives tokens as cookies
2. **API Requests:** Browser auto-sends cookies with each request (`credentials: 'include'`)
3. **Token Refresh:** Backend auto-refreshes when access token expires
4. **Logout:** POST to `/api/v1/auth/logout` → clears cookies

**Key Insight:** Browser handles token management automatically via cookies. No manual token storage in frontend.

**CORS Requirements:**
```python
# FastAPI: app/main.py
origins = [
    "http://localhost:5173",      # Dev
    "https://yourdomain.com",     # Prod
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,  # Required for cookies
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## Project Structure

```
shuushuu-frontend/
├── src/
│   ├── routes/                    # File-based routing
│   │   ├── +page.svelte           # Homepage (/)
│   │   ├── +layout.svelte         # Shared layout (header, nav)
│   │   ├── images/
│   │   │   ├── +page.svelte       # Browse images (/images)
│   │   │   ├── +page.ts           # Data loading
│   │   │   ├── [id]/
│   │   │   │   ├── +page.svelte   # Single image (/images/123)
│   │   │   │   └── +page.ts       # Load image data
│   │   │   └── upload/
│   │   │       └── +page.svelte   # Upload page (/images/upload)
│   │   ├── tags/
│   │   │   ├── +page.svelte       # Browse tags (/tags)
│   │   │   └── [slug]/
│   │   │       ├── +page.svelte   # Tag detail (/tags/anime)
│   │   │       └── +page.ts       # Load tag data
│   │   ├── login/
│   │   │   └── +page.svelte       # Login page (/login)
│   │   └── profile/
│   │       └── +page.svelte       # User profile (/profile)
│   ├── lib/
│   │   ├── components/            # Reusable components
│   │   │   ├── ui/                # shadcn components
│   │   │   ├── ImageCard.svelte
│   │   │   ├── TagInput.svelte
│   │   │   └── Header.svelte
│   │   ├── api/                   # API client functions
│   │   │   ├── images.ts
│   │   │   ├── auth.ts
│   │   │   └── tags.ts
│   │   └── stores/                # Svelte stores
│   │       └── auth.ts            # User state management
│   └── app.html                   # HTML shell
├── static/                        # Static assets
│   └── favicon.png
├── svelte.config.js               # SvelteKit config
├── package.json
└── tsconfig.json
```

### Routing Convention

File path becomes URL:
- `/routes/+page.svelte` → `/`
- `/routes/images/+page.svelte` → `/images`
- `/routes/images/[id]/+page.svelte` → `/images/123` (dynamic parameter)

## Data Loading Pattern

SvelteKit uses `+page.ts` files to fetch data **before** page renders.

### Load Function (Server & Client)

Runs on:
- **Server:** Initial page load (SSR for SEO)
- **Client:** Client-side navigation (no page reload)

**Example: Image Detail Page**

```typescript
// routes/images/[id]/+page.ts
export async function load({ params, fetch }) {
  const response = await fetch(
    `http://localhost:8000/api/v1/images/${params.id}`,
    { credentials: 'include' }
  );

  if (!response.ok) throw error(404, 'Image not found');

  const image = await response.json();
  return { image };
}
```

```svelte
<!-- routes/images/[id]/+page.svelte -->
<script lang="ts">
  export let data;  // { image: {...} }
</script>

<h1>{data.image.title}</h1>
<img src={data.image.url} alt={data.image.title} />
```

### Separation of Concerns

- **+page.ts:** Data fetching, API calls, error handling
- **+page.svelte:** Presentation, HTML rendering, user interactions

**When to use each:**
- **+page.ts:** Data needed before rendering
- **+page.svelte:** Dynamic actions (like button, add tag, submit form)

## Authentication & State Management

### User State (Svelte Store)

```typescript
// src/lib/stores/auth.ts
import { writable } from 'svelte/store';

export const currentUser = writable<User | null>(null);

export async function checkAuth() {
  const response = await fetch('http://localhost:8000/api/v1/auth/me', {
    credentials: 'include'
  });

  if (response.ok) {
    const user = await response.json();
    currentUser.set(user);
  } else {
    currentUser.set(null);
  }
}
```

### Using Auth State in Components

```svelte
<script>
  import { currentUser } from '$lib/stores/auth';
</script>

{#if $currentUser}
  <p>Welcome, {$currentUser.username}!</p>
  <a href="/images/upload">Upload</a>
{:else}
  <a href="/login">Login</a>
{/if}
```

The `$` prefix auto-subscribes to store updates.

### Protected Routes

```typescript
// routes/images/upload/+page.ts
export async function load({ fetch }) {
  const response = await fetch('http://localhost:8000/api/v1/auth/me', {
    credentials: 'include'
  });

  if (!response.ok) {
    throw redirect(302, '/login');
  }

  return { user: await response.json() };
}
```

### Login/Logout Flow

**Login:**
1. Form submits credentials to `/api/v1/auth/login`
2. Receives cookies from backend
3. Call `checkAuth()` to update store
4. Redirect to homepage

**Logout:**
1. POST to `/api/v1/auth/logout`
2. Cookies cleared by backend
3. Call `checkAuth()` to clear store
4. Redirect to login

**App Startup:**
Call `checkAuth()` in root `+layout.svelte` to restore session if cookies exist.

## Dynamic Components & Interactivity

### Svelte Reactivity

Variables are automatically reactive - when they change, UI updates automatically.

**Example: Upload Form with Preview**

```svelte
<script lang="ts">
  import { goto } from '$app/navigation';
  import { Button } from '$lib/components/ui/button';
  import TagInput from '$lib/components/TagInput.svelte';

  let selectedFile: File | null = null;
  let previewUrl = '';
  let tags: string[] = [];
  let uploading = false;

  function handleFileSelect(event: Event) {
    const target = event.target as HTMLInputElement;
    selectedFile = target.files?.[0] || null;
    if (selectedFile) {
      previewUrl = URL.createObjectURL(selectedFile);
    }
  }

  async function handleSubmit() {
    if (!selectedFile) return;

    uploading = true;

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('tags', JSON.stringify(tags));

    const response = await fetch('http://localhost:8000/api/v1/images', {
      method: 'POST',
      body: formData,
      credentials: 'include'
    });

    if (response.ok) {
      const result = await response.json();
      goto(`/images/${result.image_id}`);
    } else {
      // Handle error
      uploading = false;
    }
  }
</script>

<form on:submit|preventDefault={handleSubmit}>
  <input
    type="file"
    on:change={handleFileSelect}
    accept="image/*"
  />

  {#if previewUrl}
    <img src={previewUrl} alt="Preview" />
  {/if}

  <TagInput bind:tags />

  <Button type="submit" disabled={!selectedFile || uploading}>
    {uploading ? 'Uploading...' : 'Upload Image'}
  </Button>
</form>
```

### Key Dynamic Features for MVP

- **Upload preview:** Show selected image before upload
- **Tag input:** Autocomplete tags from API as user types
- **Form validation:** Disable submit until required fields filled
- **Loading states:** Show spinners during API calls
- **Error handling:** Display error messages from API responses

### Reusable Component Strategy

Build components once, reuse across pages:
- `ImageCard.svelte` - Display image thumbnail with metadata
- `TagInput.svelte` - Tag selection with autocomplete
- `LoadingSpinner.svelte` - Loading indicator
- `ErrorMessage.svelte` - Error display

## Component Library Setup

### shadcn-svelte Installation

```bash
# Initial setup
npx shadcn-svelte@latest init
# Choose: Default style, Slate color, TypeScript: Yes

# Add components as needed
npx shadcn-svelte@latest add button
npx shadcn-svelte@latest add card
npx shadcn-svelte@latest add input
npx shadcn-svelte@latest add dialog
npx shadcn-svelte@latest add select
npx shadcn-svelte@latest add badge
npx shadcn-svelte@latest add skeleton
```

### Usage Pattern

```svelte
<script>
  import { Button } from '$lib/components/ui/button';
  import { Card } from '$lib/components/ui/card';
</script>

<Card.Root>
  <Card.Header>
    <Card.Title>Upload Image</Card.Title>
  </Card.Header>
  <Card.Content>
    <!-- form fields -->
  </Card.Content>
  <Card.Footer>
    <Button>Submit</Button>
  </Card.Footer>
</Card.Root>
```

### Update Strategy

Components are copied into project (not npm dependencies).

**To update:**
```bash
npx shadcn-svelte@latest add button  # Re-copies latest version
```

**If customized:** Manually merge changes using git diff.

**Trade-off:** Manual updates, but no breaking changes from automatic dependency updates.

## Development Workflow

### First Time Setup

```bash
# Create project
npx sv create shuushuu-frontend
# Choose: SvelteKit minimal, TypeScript: Yes, ESLint + Prettier

# Install shadcn
cd shuushuu-frontend
npx shadcn-svelte@latest init

# Add components
npx shadcn-svelte@latest add button card input badge
```

### Local Development

```bash
# Terminal 1: Backend
cd shuushuu-api
docker compose up -d

# Terminal 2: Frontend
cd shuushuu-frontend
npm run dev -- --open
# Opens http://localhost:5173
```

**Auto-reload:** Both services auto-reload on file changes.

### Building for Production

```bash
npm run build    # Creates build/ directory
npm run preview  # Test production build locally
```

## Deployment Strategy

### Self-Hosted with Docker + nginx

**Architecture:**
```
nginx (Port 80/443)
  ├─ / → Serves SvelteKit static files
  └─ /api/* → Proxies to FastAPI (Port 8000)
```

**Benefits:**
- Single origin (no CORS needed)
- SSL termination at nginx
- Static file caching
- Production-ready

### Docker Compose Configuration

**Add to shuushuu-api/docker-compose.yml:**

```yaml
services:
  # ... existing api, db, redis services ...

  frontend:
    image: nginx:alpine
    volumes:
      - ../shuushuu-frontend/build:/usr/share/nginx/html:ro
      - ./docker/nginx/frontend.conf:/etc/nginx/conf.d/default.conf:ro
    ports:
      - "3000:80"
    depends_on:
      - api
```

### nginx Configuration

**Create: shuushuu-api/docker/nginx/frontend.conf**

```nginx
# Upstream for FastAPI backend
upstream api_backend {
    server api:8000;
}

server {
    listen 80;
    server_name _;

    # Serve SvelteKit static files
    root /usr/share/nginx/html;
    index index.html;

    # API proxy
    location /api/ {
        proxy_pass http://api_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # SvelteKit client-side routing
    # Try to serve file, fall back to index.html
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Cache static assets
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

### Deployment Process

```bash
# 1. Build frontend
cd shuushuu-frontend
npm run build

# 2. Restart containers
cd ../shuushuu-api
docker compose restart frontend
```

### Production Considerations

**SSL/HTTPS:**
- Add Let's Encrypt SSL certificates to nginx
- Update nginx to listen on port 443
- Redirect HTTP to HTTPS

**Environment Variables:**
- Create `.env` for API base URL
- Different values for dev/prod

**File Upload Path:**
- Backend serves images from `/shuushuu/images/`
- nginx may need to proxy or serve directly

## MVP Implementation Plan

### Phase 1: Foundation
**Goal:** Basic project setup and deployment pipeline

**Tasks:**
1. ✓ Create SvelteKit project
2. Install shadcn-svelte components
3. Configure nginx + Docker setup
4. Create basic layout (header, navigation, footer)
5. Build simple homepage
6. Verify deployment works

**Success:** Can view static homepage served via nginx

### Phase 2: Core Browsing
**Goal:** Users can browse and view images

**Pages:**
1. **Browse Images** (`/images`)
   - Paginated image grid
   - Load from `/api/v1/images`
   - Previous/Next navigation

2. **Image Detail** (`/images/[id]`)
   - Display image, title, tags, metadata
   - Load from `/api/v1/images/{id}`

3. **Tag Browse** (`/tags`)
   - List all tags
   - Click tag → filter images

4. **Search**
   - Search bar in header
   - Filters images by title/tags

**Components:**
- `ImageCard.svelte` - Thumbnail display
- `Pagination.svelte` - Page navigation
- `TagBadge.svelte` - Tag display

**Success:** Can browse and view images, filter by tags

### Phase 3: Authentication
**Goal:** Users can log in and access protected features

**Pages:**
1. **Login** (`/login`)
   - Username/password form
   - POST to `/api/v1/auth/login`
   - Redirect to homepage on success

2. **Profile** (`/profile`)
   - View own uploads
   - Basic user info
   - Logout button

**State Management:**
- Implement `auth.ts` store
- Add `checkAuth()` function
- Update layout to show login state

**Protected Routes:**
- Redirect to `/login` if not authenticated

**Success:** Can log in, see user-specific content, log out

### Phase 4: Upload Feature
**Goal:** Logged-in users can upload images

**Page:**
1. **Upload** (`/images/upload`)
   - File picker
   - Image preview
   - Tag input (autocomplete)
   - Title/source fields
   - Submit to `/api/v1/images`

**Components:**
- `TagInput.svelte` - Autocomplete from `/api/v1/tags`
- `ImagePreview.svelte` - Show selected file
- `UploadForm.svelte` - Form logic

**Success:** Can upload images with tags, see them in browse page

## MVP Success Criteria

- ✅ Users can browse images with pagination
- ✅ Users can view individual images and metadata
- ✅ Users can filter by tags
- ✅ Users can search images
- ✅ Users can log in/out
- ✅ Logged-in users can upload images with tags
- ✅ All pages are SEO-friendly (SSR working)
- ✅ Self-hosted deployment via Docker + nginx

## Future Enhancements (Post-MVP)

**User Features:**
- Comments on images
- Image ratings
- User profiles (avatars, bio)
- Favorites/collections
- Advanced search filters

**Admin Features:**
- User management
- Image moderation
- Tag management
- Analytics

**Technical:**
- Image optimization (WebP conversion)
- Infinite scroll option
- Real-time updates (WebSocket)
- Progressive Web App (PWA)
- Mobile responsive design improvements

## Open Questions & Decisions Needed

1. **Image serving:** Should nginx serve images directly from filesystem, or proxy through FastAPI?
2. **Error handling:** Standard error page design/messaging?
3. **Analytics:** Track page views, popular images?
4. **Accessibility:** ARIA labels, keyboard navigation requirements?
5. **Mobile design:** Responsive breakpoints, mobile-specific features?

## References

- [SvelteKit Documentation](https://kit.svelte.dev/docs)
- [shadcn-svelte](https://www.shadcn-svelte.com/)
- [Svelte Tutorial](https://learn.svelte.dev/)
