FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
COPY packages/shared/package.json ./packages/shared/package.json
COPY services/aqua-gateway/package.json ./services/aqua-gateway/package.json
COPY services/arc-gateway/package.json ./services/arc-gateway/package.json
COPY services/hedera-gateway/package.json ./services/hedera-gateway/package.json
RUN npm ci

COPY packages/shared ./packages/shared
COPY services/aqua-gateway ./services/aqua-gateway
COPY services/arc-gateway ./services/arc-gateway

WORKDIR /app/services/arc-gateway

CMD ["node", "--import", "tsx", "src/server.ts"]
