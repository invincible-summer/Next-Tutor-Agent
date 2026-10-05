"""SQLAlchemy persistence models, split by domain.

Models are private to the persistence layer: domain services consume the
plain-data records in ``app.persistence.repositories.records`` via the
repository protocols. Alembic's env.py imports ``Base`` from here.
"""
from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base with stable constraint names for Alembic diffs."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)


from .identity import (AuditEventModel, AuthSessionModel, CredentialModel,  # noqa: E402
                       IdentityProviderModel, MembershipModel, RefreshTokenModel,
                       TenantModel, UserModel)

__all__ = [
    "Base", "UserModel", "CredentialModel", "TenantModel", "MembershipModel",
    "AuthSessionModel", "RefreshTokenModel", "IdentityProviderModel",
    "AuditEventModel",
]
