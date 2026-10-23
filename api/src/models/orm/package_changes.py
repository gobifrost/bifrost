"""The desired package generation: every change to the workspace's packages."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, String, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class PackageChange(Base):
    """One package change; its ``id`` is the package generation it creates.

    Executors apply every change newer than their own generation, in order,
    before reporting that generation. A worker that was down during a change
    still catches up, because the change is a row, not a message.
    """

    __tablename__ = "package_changes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # "install" (``package`` None: install requirements.txt) or "uninstall".
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    package: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Shared with the progress hash, so every worker reports into one status.
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
