FROM python:3.11-slim
WORKDIR /app
COPY trainer/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY alembic/ alembic/
COPY trainer/ trainer/
CMD ["python", "-m", "trainer.main"]
