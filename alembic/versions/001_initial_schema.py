# alembic/versions/001_initial_schema.py
"""initial schema"""
revision = "001"
down_revision = None
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "markets",
        sa.Column("market_id", sa.Text, primary_key=True),
        sa.Column("ticker", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("close_time", sa.TIMESTAMP(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("discovered_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_table(
        "raw_features",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("market_id", sa.Text, sa.ForeignKey("markets.market_id"), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("price_open", sa.Numeric),
        sa.Column("price_high", sa.Numeric),
        sa.Column("price_low", sa.Numeric),
        sa.Column("price_close", sa.Numeric),
        sa.Column("volume", sa.Numeric),
        sa.Column("bid_depth_1pct", sa.Numeric),
        sa.Column("ask_depth_1pct", sa.Numeric),
        sa.Column("book_imbalance", sa.Numeric),
        sa.Column("kalshi_yes_price", sa.Numeric),
        sa.Column("kalshi_no_price", sa.Numeric),
        sa.Column("kalshi_volume", sa.Numeric),
        sa.Column("price_momentum_1m", sa.Numeric),
        sa.Column("price_momentum_5m", sa.Numeric),
        sa.Column("price_momentum_15m", sa.Numeric),
        sa.Column("volatility_5m", sa.Numeric),
    )
    op.create_index("ix_raw_features_market_ts", "raw_features", ["market_id", "ts"])
    op.create_table(
        "predictions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("market_id", sa.Text, sa.ForeignKey("markets.market_id"), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("confidence", sa.Numeric, nullable=False),
        sa.Column("low_confidence", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("feature_snapshot_id", sa.BigInteger, sa.ForeignKey("raw_features.id")),
        sa.Column("settled_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("actual_outcome", sa.SmallInteger),
    )
    op.create_table(
        "model_registry",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("market_id", sa.Text, sa.ForeignKey("markets.market_id"), nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("trained_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("training_rows", sa.Integer, nullable=False),
        sa.Column("brier_score", sa.Numeric, nullable=False),
        sa.Column("artifact_path", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
        sa.UniqueConstraint("market_id", "version", name="uq_model_market_version"),
    )


def downgrade():
    op.drop_table("model_registry")
    op.drop_table("predictions")
    op.drop_index("ix_raw_features_market_ts")
    op.drop_table("raw_features")
    op.drop_table("markets")
