# syntax=docker/dockerfile:1.7
# Production web image: builds the SPA, then ships only the static files on Caddy.
# Caddy's configuration (TLS, /api proxy) is mounted from deploy/Caddyfile at run time.

FROM node:24-alpine AS build
WORKDIR /app
RUN npm install -g pnpm@12
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM caddy:2-alpine AS web
COPY --from=build /app/dist /srv
