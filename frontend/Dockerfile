# Smart Spatial System frontend image - static build served by nginx.
#
# Vite bakes VITE_* env vars in at BUILD time, not container-start time, so
# they are passed as build args here, not `docker run -e`:
#
#   docker build \
#     --build-arg VITE_API_BASE_URL=https://api.example.com \
#     --build-arg VITE_API_KEY=<matches backend SMART_SPATIAL_API_KEY> \
#     -t smart-spatial-system-frontend .
#   docker run -p 8080:80 smart-spatial-system-frontend

FROM node:20-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG VITE_API_BASE_URL
ARG VITE_API_KEY
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_API_KEY=$VITE_API_KEY

RUN npm run build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html

# Client-side routing isn't used here (single page, no router), but this
# keeps deep links to the app itself working behind any reverse proxy.
RUN printf 'server {\n\
    listen 80;\n\
    root /usr/share/nginx/html;\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 80
