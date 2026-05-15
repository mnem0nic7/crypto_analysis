"""add sweep_runs and sweep_results tables"""
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "sweep_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("n_settled_predictions", sa.Integer),
        sa.Column("fee_bps", sa.Integer, nullable=False, server_default="50"),
        sa.Column("n_combinations_evaluated", sa.Integer),
        sa.Column("elapsed_seconds", sa.Numeric),
        sa.Column("best_net_pnl_dollars", sa.Numeric),
        sa.Column("best_settings_json", sa.JSON),
    )
    op.create_table(
        "sweep_results",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.BigInteger, sa.ForeignKey("sweep_runs.id"), nullable=False),
        sa.Column("result_type", sa.Text, nullable=False),
        sa.Column("rank", sa.Integer),
        sa.Column("knob_name", sa.Text),
        sa.Column("knob_value", sa.Text),
        sa.Column("min_fee_adjusted_edge_bps", sa.Integer),
        sa.Column("max_spread_bps", sa.Integer),
        sa.Column("min_confidence", sa.Numeric),
        sa.Column("min_contract_price_dollars", sa.Numeric),
        sa.Column("crypto_live_min_market_age_seconds", sa.Integer),
        sa.Column("crypto_autonomy_min_seconds_to_close", sa.Integer),
        sa.Column("crypto_taker_fallback_close_seconds", sa.Integer),
        sa.Column("crypto_market_price_anchor_weight", sa.Numeric),
        sa.Column("crypto_late_sure_thing_min_probability", sa.Numeric),
        sa.Column("crypto_late_sure_thing_min_market_probability", sa.Numeric),
        sa.Column("n_trades", sa.Integer),
        sa.Column("win_rate", sa.Numeric),
        sa.Column("net_pnl_dollars", sa.Numeric),
        sa.Column("ev_per_contract", sa.Numeric),
        sa.Column("starvation_rate", sa.Numeric),
    )
    op.create_index("ix_sweep_results_run_type", "sweep_results", ["run_id", "result_type"])


def downgrade():
    op.drop_index("ix_sweep_results_run_type")
    op.drop_table("sweep_results")
    op.drop_table("sweep_runs")
