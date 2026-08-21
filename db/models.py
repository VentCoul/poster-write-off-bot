from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DraftStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"
    cancelled = "cancelled"


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    access_token: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    staff: Mapped[list["Staff"]] = relationship(back_populates="client")
    admins: Mapped[list["Admin"]] = relationship(back_populates="client")
    drafts: Mapped[list["WriteoffDraft"]] = relationship(back_populates="client")


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    invite_token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    client: Mapped["Client"] = relationship(back_populates="staff")


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    client: Mapped["Client"] = relationship(back_populates="admins")


class WriteoffDraft(Base):
    __tablename__ = "writeoff_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    staff_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    staff_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default=DraftStatus.pending)
    reject_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    poster_reason_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason_name: Mapped[str | None] = mapped_column(String, nullable=True)
    storage_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(String, nullable=True)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    client: Mapped["Client"] = relationship(back_populates="drafts")
    items: Mapped[list["WriteoffItem"]] = relationship(back_populates="draft", cascade="all, delete-orphan")
    log: Mapped["WriteoffLog | None"] = relationship(back_populates="draft", uselist=False)


class WriteoffItem(Base):
    __tablename__ = "writeoff_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("writeoff_drafts.id"), nullable=False)
    ingredient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ingredient_name: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    # Poster write-off type: 1=product, 2=dish, 3=prepack, 4=ingredient, 5=modifier
    item_type: Mapped[str] = mapped_column(String, default="4", server_default="4", nullable=False)

    draft: Mapped["WriteoffDraft"] = relationship(back_populates="items")


class WriteoffReason(Base):
    __tablename__ = "writeoff_reasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    poster_reason_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PendingOAuth(Base):
    """Short-lived token that links an OAuth flow to a specific admin tg_id."""
    __tablename__ = "pending_oauth"

    token: Mapped[str] = mapped_column(String, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WriteoffLog(Base):
    __tablename__ = "writeoff_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("writeoff_drafts.id"), nullable=False)
    poster_writeoff_id: Mapped[str | None] = mapped_column(String, nullable=True)
    admin_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    draft: Mapped["WriteoffDraft"] = relationship(back_populates="log")
