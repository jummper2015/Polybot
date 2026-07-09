 # alembic/versions/005_redeem_operations.py
  """add redeem_operations table
     
  Revision ID: 005
  Revises: 004
  Create Date: 2026-07-09 16:00:00
  
  R2.0-redeem-impl F1 Paso 2: persistencia de operaciones redeem on-chain.
  Idempotencia via redeem_op_id (UUID unico por operacion).
  Reconciliacion on startup via status + tx_hash.
  """
  from alembic import op
  import sqlalchemy as sa
  
  revision = "005"
  down_revision = "004"
  branch_labels = None
  depends_on = None
  

  def upgrade() -> None:
      op.create_table(
          "redeem_operations",
          sa.Column("redeem_op_id",    sa.String(36),  primary_key=True),
          sa.Column("condition_id",    sa.String(66),  nullable=False),
          sa.Column("position_id",     sa.String(36),  nullable=True),
          sa.Column("tx_hash",         sa.String(66),  nullable=True),
          sa.Column("index_sets",      sa.JSON,        nullable=False),
          sa.Column("shares_redeemed", sa.Integer,     nullable=False),
          sa.Column("pusd_received",   sa.Float,       nullable=False, server_default="0.0"),
          sa.Column("gas_used",        sa.Integer,     nullable=True),
          sa.Column("gas_fee_matic",   sa.Float,       nullable=True),
          sa.Column("submitted_at",    sa.DateTime,    nullable=True),
          sa.Column("mined_at",        sa.DateTime,    nullable=True),
          sa.Column("confirmed_at",    sa.DateTime,    nullable=True),
          sa.Column("status",          sa.String(20),  nullable=False),
          sa.Column("error_reason",    sa.Text,        nullable=True),
          sa.Column("proxy_address",   sa.String(42),  nullable=False),
          sa.Column("adapter_address", sa.String(42),  nullable=False),
          sa.Column("created_at",      sa.DateTime,    server_default=sa.func.now()),
          sa.Column("updated_at",      sa.DateTime,    server_default=sa.func.now(), onupdate=sa.func.now()),
      )
      op.create_index("ix_redeem_operations_redeem_op_id", "redeem_operations", ["redeem_op_id"], unique=True)
      op.create_index("ix_redeem_operations_condition_id", "redeem_operations", ["condition_id"])
      op.create_index("ix_redeem_operations_status",       "redeem_operations", ["status"])
      op.create_index("ix_redeem_operations_created_at",   "redeem_operations", ["created_at"])
      op.create_index("ix_redeem_operations_tx_hash",      "redeem_operations", ["tx_hash"])

  
  def downgrade() -> None:
      op.drop_index("ix_redeem_operations_tx_hash",      table_name="redeem_operations")
      op.drop_index("ix_redeem_operations_created_at",   table_name="redeem_operations")
      op.drop_index("ix_redeem_operations_status",       table_name="redeem_operations")
      op.drop_index("ix_redeem_operations_condition_id", table_name="redeem_operations")
      op.drop_index("ix_redeem_operations_redeem_op_id", table_name="redeem_operations")
      op.drop_table("redeem_operations")