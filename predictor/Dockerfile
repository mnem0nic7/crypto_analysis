FROM python:3.11-slim
WORKDIR /app
COPY predictor/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY predictor/ predictor/
COPY .env .env
VOLUME ["/app/models"]
CMD ["python", "-m", "predictor.main"]
