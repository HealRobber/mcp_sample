FROM python:3.13.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl libpq-dev build-essential rustc \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8080

# 개발용 무한 루프
# CMD ["tail", "-f", "/dev/null"]
CMD ["uvicorn", "mcp_server:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]
