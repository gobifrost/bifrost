# Bifrost Client - Production Container Image
# Multi-stage build for optimized production image

# =============================================================================
# Stage 1: Base with dependencies
# Uses BUILDPLATFORM to run on native architecture (no QEMU emulation)
# =============================================================================
# node:20-slim
FROM --platform=$BUILDPLATFORM node:26-slim@sha256:a1d9d671994fc2d26e297ac56b4b1522a8bc7fa71c43b14cd1b1fe6c5116f7dc AS base

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
FROM nginx:alpine@sha256:4a73073bd557c65b759505da037898b61f1be6cbcc3c2c3aeac22d2a470c1752 AS production

# Copy built assets from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
