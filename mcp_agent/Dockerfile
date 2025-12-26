FROM python:3.13.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8090
CMD ["uvicorn", "agent_server:app", "--host", "0.0.0.0", "--port", "8090"]
