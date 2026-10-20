FROM node:20-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    for attempt in 1 2 3; do \
      npm ci --fetch-retries=5 --fetch-retry-mintimeout=10000 && exit 0; \
      if [ "$attempt" -lt 3 ]; then sleep 2; fi; \
    done; exit 1

COPY . .
# 前端 .env 的地图 Key 属于浏览器公开配置；Docker 演示强制同源 API，
# 不使用开发环境的 VITE_API_BASE_URL 直连后端端口。
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/W205614/langchain-trip-planner"
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
