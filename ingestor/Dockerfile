FROM python:3.11-slim
WORKDIR /app
COPY ingestor/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY alembic/ alembic/
COPY ingestor/ ingestor/
COPY .env .env
COPY Kalshi-1.txt Kalshi-1.txt
COPY Kalshi-2-Demo.txt Kalshi-2-Demo.txt
COPY cdp_api_key.json cdp_api_key.json
CMD ["python", "-m", "ingestor.main"]
