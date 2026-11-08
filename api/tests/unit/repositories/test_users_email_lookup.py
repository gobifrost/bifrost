"""Email identity lookup matches the literal address, not a SQL pattern."""

from datetime import datetime, timezone
from uuid import uuid4

from src.models.orm.users import User
from src.repositories.users import UserRepository


async def test_get_by_email_treats_like_wildcards_literally(db_session):
    suffix = uuid4().hex[:8]
    attacker_email = f"adm_n{suffix}@example.com"
    victim_email = f"admin{suffix}@example.com"

    def make(email: str) -> User:
        return User(
            id=uuid4(),
            email=email,
            name="Email Lookup",
            is_active=True,
            is_superuser=True,
            is_verified=True,
            is_registered=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    attacker = make(attacker_email)
    victim = make(victim_email)
    db_session.add_all([attacker, victim])
    await db_session.flush()

    repo = UserRepository(db_session)

    # Literal lookup finds only the requested account. With ``ilike`` the
    # underscore is a wildcard and both rows match.
    found = await repo.get_by_email(attacker_email)
    assert found is not None
    assert found.id == attacker.id

    assert (await repo.get_by_email(victim_email.upper())) is not None
    assert (await repo.get_by_email(victim_email.upper())).id == victim.id

    # A wildcard pattern is just a different (non-matching) literal address.
    assert await repo.get_by_email(f"adm%{suffix}@example.com") is None
    assert await repo.get_by_email(f"adminX{suffix}@example.com") is None
