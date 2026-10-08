FROM node:22.11.0-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# Optional extra CA (build secret "extra_ca") for TLS-intercepting proxies; empty and unused by default.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund

FROM node:22.11.0-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22.11.0-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S ccp && adduser -S ccp -G ccp
COPY --from=build --chown=ccp:ccp /app/.next/standalone ./
COPY --from=build --chown=ccp:ccp /app/.next/static ./.next/static
COPY --from=build --chown=ccp:ccp /app/public ./public
USER ccp
EXPOSE 3000
CMD ["node", "server.js"]
