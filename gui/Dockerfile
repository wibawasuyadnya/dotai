# Local-AI web GUI — build from gui/:
#   docker build -t local-ai-gui .
# NEXT_PUBLIC_AI_SERVER="" bakes relative /api URLs so the app works behind
# the Caddy reverse proxy (single origin) instead of hardcoding localhost.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY . .
ARG NEXT_PUBLIC_AI_SERVER=""
ENV NEXT_PUBLIC_AI_SERVER=$NEXT_PUBLIC_AI_SERVER
RUN npx next build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
