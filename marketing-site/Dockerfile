FROM node:24-alpine AS dependencies
WORKDIR /site
COPY package.json package-lock.json* ./
RUN npm install

FROM node:24-alpine AS builder
WORKDIR /site
COPY --from=dependencies /site/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:24-alpine AS runner
WORKDIR /site
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
COPY --from=builder /site/public ./public
COPY --from=builder /site/.next/standalone ./
COPY --from=builder /site/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
