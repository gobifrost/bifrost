# Bifrost Client - Production Container Image
# Multi-stage build for optimized production image

# =============================================================================
# Stage 1: Base with dependencies
# Uses BUILDPLATFORM to run on native architecture (no QEMU emulation)
# =============================================================================
# node:26-slim
FROM --platform=$BUILDPLATFORM node:26-slim@sha256:424cafd2a035ed2b2d74acc3142b68b426fb62a47742c80a75e7117db02d6b30 AS base

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci

# =============================================================================
# Stage 2: Build for production
# =============================================================================
FROM base AS builder

# Copy source code
COPY . .

# Build the application
# Increase Node memory limit to prevent OOM during build
ENV NODE_OPTIONS="--max-old-space-size=4096"
ARG VITE_BIFROST_VERSION=unknown
ENV VITE_BIFROST_VERSION=${VITE_BIFROST_VERSION}
RUN npm run build

# =============================================================================
# Stage 3: Production with nginx
# =============================================================================
# nginx:alpine
FROM nginx:alpine@sha256:dc48b7a872a79fb541ba5081d320b11b549231bc63ba465a7495afaa7d2ebcb8 AS production

RUN if command -v apk >/dev/null 2>&1; then \
        apk add --no-cache libcap; \
    else \
        apt-get update && \
        apt-get install -y --no-install-recommends libcap2-bin && \
        rm -rf /var/lib/apt/lists/*; \
    fi && \
    setcap 'cap_net_bind_service=+ep' /usr/sbin/nginx

# Copy built assets from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

RUN touch /var/run/nginx.pid && \
    chown -R nginx:nginx /var/run/nginx.pid /var/cache/nginx /var/log/nginx /etc/nginx/conf.d /usr/share/nginx/html

USER nginx

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
