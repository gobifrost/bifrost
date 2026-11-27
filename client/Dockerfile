# Bifrost Client - Production Container Image
# Multi-stage build for optimized production image

# =============================================================================
# Stage 1: Base with dependencies
# Uses BUILDPLATFORM to run on native architecture (no QEMU emulation)
# =============================================================================
# node:20-slim
FROM --platform=$BUILDPLATFORM node:26-slim@sha256:14bf3eac4bf209d906d3c41256597d3ab1f926b2e93a79e9bdfe1efd32454239 AS base

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
RUN npm run build \
    && find dist -type d -exec chmod 755 {} + \
    && find dist -type f -exec chmod 644 {} +

# =============================================================================
# Stage 3: Production with nginx
# =============================================================================
# nginx:1.31.9-alpine
FROM nginx:1.31.9-alpine@sha256:fb4658584bd9a916ffb6f6e365f97c25cfdb2923801f551ea08356e695f14ff7 AS production

# Copy built assets from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
