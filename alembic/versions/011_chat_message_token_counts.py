"""Add estimated token counts to chat messages.

Revision ID: 011_chat_message_token_counts
Revises: 010_conversation_summary
Create Date: 2026-07-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "011_chat_message_token_counts"
down_revision: Union[str, None] = "010_conversation_summary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("chat_messages", sa.Column("tokenizer_model", sa.String(120), nullable=False, server_default="unknown"))
    op.add_column("chat_messages", sa.Column("token_count_method", sa.String(32), nullable=False, server_default="estimated"))
    op.add_column("chat_messages", sa.Column("token_count_version", sa.String(40), nullable=False, server_default="char-heuristic-v1"))
    op.create_index("ix_chat_messages_tokenizer_model", "chat_messages", ["tokenizer_model"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_tokenizer_model", table_name="chat_messages")
    op.drop_column("chat_messages", "token_count_version")
    op.drop_column("chat_messages", "token_count_method")
    op.drop_column("chat_messages", "tokenizer_model")
    op.drop_column("chat_messages", "token_count")
