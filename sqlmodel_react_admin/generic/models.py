from uuid import uuid4, UUID
from sqlmodel import SQLModel, Field, UniqueConstraint


class ReactAdminDBModel(SQLModel):
    __table_args__ = (UniqueConstraint("id"),)
    iterator: int = Field(
        default=None,
        nullable=False,
        primary_key=True,
        index=True,
    )
    id: UUID = Field(
        default_factory=uuid4,
        index=True,
        nullable=False,
    )
