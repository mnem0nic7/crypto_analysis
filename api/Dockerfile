FROM python:3.11-slim
WORKDIR /app
COPY api/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY api/ api/
COPY .env .env
CMD ["python", "-m", "api.main"]
