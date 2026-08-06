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

# FastAPI/Uvicorn + PyMySQL；不引入 ORM 或任务队列。
COPY requirements.txt /app/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY app.py /app/app.py
COPY backend /app/backend
COPY scripts /app/scripts
COPY --from=web-build /web/dist /app/apps/web/dist

RUN mkdir -p /app/data

EXPOSE 8000

VOLUME ["/app/data"]

CMD ["python", "app.py"]
