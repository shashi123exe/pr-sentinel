# ==============================================================================
# PR Sentinel — Production Frontend Dockerfile (Multi-stage)
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Build Application Bundle
# ------------------------------------------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /app

# Copy dependency manifests and install clean dependencies
COPY package.json package-lock.json ./
RUN npm ci

# Copy frontend source code and static assets
COPY . .

# Run production build (TypeScript compile + Vite bundle)
RUN npm run build

# ------------------------------------------------------------------------------
# Stage 2: Serve via Unprivileged Nginx
# ------------------------------------------------------------------------------
FROM nginxinc/nginx-unprivileged:alpine-slim AS runner

# Copy custom Nginx configuration with SPA fallback and /api proxying
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy production bundle from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Expose unprivileged port 8080
EXPOSE 8080

# Health check verifying root dashboard HTML serves successfully
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:8080/ || exit 1

# Start Nginx in foreground
CMD ["nginx", "-g", "daemon off;"]
