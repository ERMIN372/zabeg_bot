# noqa
"""support_messages.question_text

Revision ID: a1b2c3d4e5f6
Revises: 093d14cdb0a8
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '093d14cdb0a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'support_messages',
        sa.Column('question_text', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('support_messages', 'question_text')
