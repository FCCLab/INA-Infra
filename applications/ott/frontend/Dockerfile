# Multi-stage build for React OTT Video Portal
FROM node:20-bookworm-slim AS build
WORKDIR /ui

COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY . .
RUN npm run build

FROM nginx:1.27-alpine-slim
COPY --from=build /ui/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
