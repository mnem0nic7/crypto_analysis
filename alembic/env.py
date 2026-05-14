# alembic/env.py
import os
from alembic import context
from sqlalchemy import engine_from_config, pool
from shared.orm import Base

target_metadata = Base.metadata


def run_migrations_online():
    password = os.environ.get("POSTGRES_PASSWORD", "postgres")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    db = os.environ.get("POSTGRES_DB", "crypto_analysis")
    url = f"postgresql://postgres:{password}@{host}:5432/{db}"
    connectable = engine_from_config(
        {"sqlalchemy.url": url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
