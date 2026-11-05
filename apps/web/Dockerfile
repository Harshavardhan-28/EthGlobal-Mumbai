FROM node:22-alpine AS dependencies

WORKDIR /app

COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
COPY packages/shared/package.json ./packages/shared/package.json
COPY services/aqua-gateway/package.json ./services/aqua-gateway/package.json
COPY services/arc-gateway/package.json ./services/arc-gateway/package.json
COPY services/hedera-gateway/package.json ./services/hedera-gateway/package.json
RUN npm ci

FROM dependencies AS development

COPY . .

EXPOSE 3000

CMD ["sh", "-c", "cd apps/web && ../../node_modules/.bin/next dev --hostname 0.0.0.0 --port 3000"]

FROM dependencies AS builder

COPY . .

ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

RUN npm run build --workspace apps/web

FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

COPY --from=builder /app/apps/web/.next/standalone ./
COPY --from=builder /app/apps/web/.next/static ./apps/web/.next/static

EXPOSE 3000

CMD ["node", "apps/web/server.js"]
