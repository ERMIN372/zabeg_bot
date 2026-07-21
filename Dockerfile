FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# api.telegram.org резолвится и в IPv6, который в docker-сети недоступен
# ("Network is unreachable" / "Cannot assign requested address"). Повышаем
# приоритет IPv4 в таблице precedence RFC 3484 — getaddrinfo сначала отдаёт
# IPv4, при этом IPv6 остаётся включённым.
RUN echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "bot.main"]
