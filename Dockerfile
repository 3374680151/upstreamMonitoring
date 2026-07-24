FROM node:22-alpine AS web-build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    TZ=Asia/Shanghai \
    APP_TIMEZONE=Asia/Shanghai

WORKDIR /app

COPY app.py /app/app.py
COPY --from=web-build /web/dist /app/apps/web/dist

RUN mkdir -p /app/data /app/static

EXPOSE 8000

VOLUME ["/app/data"]

CMD ["python", "app.py"]
