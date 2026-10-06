"""Domain document tables: one JSONB document table per business domain.

chat/library/textbooks/notes/assessment/evidence/classroom/orchestration/
assistant (assistant also serves illustration kinds). Shared column shape:
tenant/owner isolation keys, kind + doc_id resource key, JSONB payload,
epoch for optimistic concurrency, float unix timestamps.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOMAINS = ("chat", "library", "textbooks", "notes", "assessment",
            "evidence", "classroom", "orchestration", "assistant")


def _upgrade_domain(domain: str) -> None:
    table = f"{domain}_documents"
    op.create_table(table,
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('owner_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=48), nullable=False),
    sa.Column('doc_id', sa.String(length=128), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('epoch', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('created_at', sa.Float(), nullable=False),
    sa.Column('updated_at', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f(f'pk_{table}')),
    sa.UniqueConstraint('tenant_id', 'owner_id', 'kind', 'doc_id', name=op.f(f'uq_{table}_scope_key'))
    )
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.create_index(batch_op.f(f'ix_{table}_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f(f'ix_{table}_owner_id'), ['owner_id'], unique=False)
        batch_op.create_index(batch_op.f(f'ix_{table}_owner_kind'), ['owner_id', 'kind'], unique=False)
        batch_op.create_index(batch_op.f(f'ix_{table}_tenant_owner'), ['tenant_id', 'owner_id'], unique=False)


def upgrade() -> None:
    for domain in _DOMAINS:
        _upgrade_domain(domain)


def downgrade() -> None:
    for domain in reversed(_DOMAINS):
        table = f"{domain}_documents"
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_index(batch_op.f(f'ix_{table}_tenant_owner'))
            batch_op.drop_index(batch_op.f(f'ix_{table}_owner_kind'))
            batch_op.drop_index(batch_op.f(f'ix_{table}_owner_id'))
            batch_op.drop_index(batch_op.f(f'ix_{table}_tenant_id'))
        op.drop_table(table)
