"""
MFA Service - Core business logic for Multi-Factor Authentication.

Handles TOTP secret generation, verification, recovery codes, and trusted devices.
"""

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pyotp
from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.security import decrypt_secret, encrypt_secret, get_password_hash, verify_password
from src.models import MFARecoveryCode, TrustedDevice, User, UserMFAMethod
from src.models.enums import MFAMethodStatus, MFAMethodType


def _totp_counter_for_code(
    totp: pyotp.TOTP,
    code: str,
    valid_window: int,
    now: float | None = None,
) -> int | None:
    """Return the time-step matching ``code`` within the accepted window.

    ``pyotp`` only reports valid/invalid, so the matching step is located
    explicitly. Callers persist it to reject replay of an accepted code.
    """
    if not isinstance(code, str):
        return None
    candidate = code.strip()
    # ``secrets.compare_digest`` raises TypeError on non-ASCII input, and the
    # request contract accepts any string. Reject anything that is not exactly
    # one TOTP code's worth of ASCII digits.
    if len(candidate) != totp.digits or not (candidate.isascii() and candidate.isdigit()):
        return None
    now = time.time() if now is None else now
    current = int(now // totp.interval)
    for step in range(current - valid_window, current + valid_window + 1):
        if secrets.compare_digest(totp.at(step * totp.interval), candidate):
            return step
    return None


class MFAService:
    """Service for MFA operations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()

    # ========================================================================
    # TOTP Operations
    # ========================================================================

    async def setup_totp(self, user: User, force_new: bool = False) -> dict:
        """
        Initialize TOTP enrollment for a user.

        Creates a pending MFA method with encrypted secret, or returns existing
        pending setup if still valid (to prevent QR code invalidation on page refresh).

        Args:
            user: User to set up TOTP for
            force_new: If True, always generate a new secret even if pending exists

        Returns:
            Dictionary with secret, QR code URI, and provisioning details
        """
        # Check for existing valid pending TOTP (to prevent invalidation on refresh)
        pending_validity_minutes = self.settings.mfa_pending_validity_minutes
        if not force_new:
            existing = await self._get_pending_totp(user.id)
            if existing and existing.created_at:
                age = datetime.now(timezone.utc) - existing.created_at
                if age < timedelta(minutes=pending_validity_minutes):
                    # Return existing pending setup instead of regenerating
                    if existing.encrypted_secret:
                        secret = decrypt_secret(existing.encrypted_secret)
                        totp = pyotp.TOTP(secret)
                        provisioning_uri = totp.provisioning_uri(
                            name=user.email,
                            issuer_name=self.settings.mfa_totp_issuer
                        )
                        return {
                            "secret": secret,
                            "provisioning_uri": provisioning_uri,
                            "qr_code_uri": provisioning_uri,
                            "issuer": self.settings.mfa_totp_issuer,
                            "account_name": user.email,
                            "is_existing": True,
                        }

        # Delete any existing pending TOTP methods
        await self._delete_pending_totp(user.id)

        # Generate new secret
        secret = pyotp.random_base32()

        # Create pending MFA method
        mfa_method = UserMFAMethod(
            user_id=user.id,
            method_type=MFAMethodType.TOTP,
            status=MFAMethodStatus.PENDING,
            encrypted_secret=encrypt_secret(secret),
        )
        self.db.add(mfa_method)
        await self.db.flush()

        # Generate provisioning URI
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(
            name=user.email,
            issuer_name=self.settings.mfa_totp_issuer
        )

        return {
            "secret": secret,
            "provisioning_uri": provisioning_uri,
            "qr_code_uri": provisioning_uri,  # Same URI, frontend generates QR
            "issuer": self.settings.mfa_totp_issuer,
            "account_name": user.email,
            "is_existing": False,
        }

    async def verify_totp_enrollment(self, user: User, code: str) -> list[str]:
        """
        Verify TOTP code to complete enrollment.

        Args:
            user: User completing enrollment
            code: 6-digit TOTP code from authenticator app

        Returns:
            List of recovery codes on success

        Raises:
            ValueError: If no pending enrollment or invalid code
        """
        # Get pending TOTP method
        mfa_method = await self._get_pending_totp(user.id)
        if not mfa_method:
            raise ValueError("No pending TOTP enrollment found")

        # Verify code
        if not mfa_method.encrypted_secret:
            raise ValueError("MFA method has no encrypted secret")
        secret = decrypt_secret(mfa_method.encrypted_secret)
        totp = pyotp.TOTP(secret)

        # Use wider window for enrollment to accommodate clock skew
        enrollment_window = self.settings.mfa_totp_enrollment_window
        step = _totp_counter_for_code(totp, code, enrollment_window)
        if step is None:
            raise ValueError("Invalid TOTP code")

        # Activate method. Enrollment issues a session, so the accepted code's
        # time-step is consumed here too; it must not authenticate again.
        mfa_method.status = MFAMethodStatus.ACTIVE
        mfa_method.verified_at = datetime.now(timezone.utc)
        mfa_method.last_used_counter = step
        mfa_method.last_used_at = datetime.now(timezone.utc)

        # Enable MFA on user
        user.mfa_enabled = True

        # Generate recovery codes
        recovery_codes = await self._generate_recovery_codes(user.id)

        await self.db.flush()

        return recovery_codes

    async def verify_totp_code(self, user_id: UUID, code: str) -> bool:
        """
        Verify a TOTP code during login.

        Args:
            user_id: User ID
            code: 6-digit TOTP code

        Returns:
            True if code is valid

        Raises:
            ValueError: If TOTP not configured
        """
        mfa_method = await self._get_active_totp(user_id)
        if not mfa_method:
            raise ValueError("TOTP not configured")

        if not mfa_method.encrypted_secret:
            raise ValueError("MFA method has no encrypted secret")
        secret = decrypt_secret(mfa_method.encrypted_secret)
        totp = pyotp.TOTP(secret)

        # Use standard window for login verification
        login_window = self.settings.mfa_totp_login_window
        step = _totp_counter_for_code(totp, code, login_window)
        if step is None:
            return False
        # Reject replay of an already-accepted code (including within the
        # validity window). ``last_used_counter`` stores the accepted step.
        if (
            mfa_method.last_used_counter is not None
            and step <= mfa_method.last_used_counter
        ):
            return False

        mfa_method.last_used_counter = step
        mfa_method.last_used_at = datetime.now(timezone.utc)
        await self.db.flush()

        return True

    async def remove_totp(self, user: User) -> None:
        """
        Remove TOTP enrollment for a user.

        Args:
            user: User to remove TOTP for
        """
        # Delete all TOTP methods
        await self.db.execute(
            delete(UserMFAMethod).where(
                UserMFAMethod.user_id == user.id,
                UserMFAMethod.method_type == MFAMethodType.TOTP
            )
        )

        # Delete recovery codes
        await self._delete_recovery_codes(user.id)

        # Disable MFA on user if no other methods
        remaining = await self._count_active_methods(user.id)
        if remaining == 0:
            user.mfa_enabled = False

        await self.db.flush()

    async def get_mfa_status(self, user: User) -> dict:
        """
        Get MFA status for a user.

        Args:
            user: User to get status for

        Returns:
            Dictionary with MFA status information
        """
        methods = await self._get_active_methods(user.id)
        recovery_count = await self._count_unused_recovery_codes(user.id)

        return {
            "mfa_enabled": user.mfa_enabled,
            "mfa_required": True,  # Always required for password auth
            "enforcement_deadline": user.mfa_enforced_at,
            "enrolled_methods": [m.method_type.value for m in methods],
            "recovery_codes_remaining": recovery_count,
        }

    # ========================================================================
    # Recovery Codes
    # ========================================================================

    async def _generate_recovery_codes(self, user_id: UUID) -> list[str]:
        """
        Generate new recovery codes, invalidating any existing ones.

        Args:
            user_id: User ID

        Returns:
            List of plaintext recovery codes
        """
        # Delete existing codes
        await self._delete_recovery_codes(user_id)

        codes = []
        for _ in range(self.settings.mfa_recovery_code_count):
            # Generate readable code (e.g., "ABCD-1234")
            code = self._generate_recovery_code()
            codes.append(code)

            # Store hashed
            recovery_code = MFARecoveryCode(
                user_id=user_id,
                code_hash=get_password_hash(code.replace("-", "").upper()),
            )
            self.db.add(recovery_code)

        await self.db.flush()
        return codes

    def _generate_recovery_code(self) -> str:
        """Generate a readable recovery code."""
        chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # Exclude confusing chars
        code = "".join(secrets.choice(chars) for _ in range(8))
        return f"{code[:4]}-{code[4:]}"

    async def verify_recovery_code(
        self,
        user_id: UUID,
        code: str,
        ip_address: str | None = None
    ) -> bool:
        """
        Verify and consume a recovery code.

        Args:
            user_id: User ID
            code: Recovery code to verify
            ip_address: IP address for audit

        Returns:
            True if code is valid and was consumed
        """
        normalized = code.replace("-", "").upper()

        # Get unused recovery codes
        codes = await self._get_unused_recovery_codes(user_id)

        for recovery_code in codes:
            if verify_password(normalized, recovery_code.code_hash):
                # Mark as used
                recovery_code.is_used = True
                recovery_code.used_at = datetime.now(timezone.utc)
                recovery_code.used_from_ip = ip_address
                await self.db.flush()
                return True

        return False

    async def regenerate_recovery_codes(self, user_id: UUID) -> list[str]:
        """
        Regenerate recovery codes for a user.

        Args:
            user_id: User ID

        Returns:
            New list of recovery codes
        """
        return await self._generate_recovery_codes(user_id)

    async def get_recovery_codes_count(self, user_id: UUID) -> dict:
        """
        Get count of recovery codes.

        Args:
            user_id: User ID

        Returns:
            Dictionary with total and remaining counts
        """
        total = await self._count_recovery_codes(user_id)
        remaining = await self._count_unused_recovery_codes(user_id)

        return {
            "total": total,
            "remaining": remaining,
        }

    # ========================================================================
    # Trusted Devices
    # ========================================================================

    async def create_trusted_device(
        self,
        user_id: UUID,
        fingerprint: str,
        device_name: str | None = None,
        ip_address: str | None = None,
    ) -> TrustedDevice:
        """
        Create a trusted device entry.

        Args:
            user_id: User ID
            fingerprint: Device fingerprint hash
            device_name: Human-readable device name
            ip_address: IP address

        Returns:
            Created TrustedDevice
        """
        # Check for existing device with same fingerprint
        existing = await self._get_trusted_device(user_id, fingerprint)
        if existing:
            # Update expiry
            existing.expires_at = datetime.now(timezone.utc) + timedelta(
                days=self.settings.mfa_trusted_device_days
            )
            existing.last_used_at = datetime.now(timezone.utc)
            existing.last_ip_address = ip_address
            await self.db.flush()
            return existing

        device = TrustedDevice(
            user_id=user_id,
            device_fingerprint=fingerprint,
            device_name=device_name,
            expires_at=datetime.now(timezone.utc) + timedelta(
                days=self.settings.mfa_trusted_device_days
            ),
            last_ip_address=ip_address,
        )
        self.db.add(device)
        await self.db.flush()
        return device

    async def is_device_trusted(
        self,
        user_id: UUID,
        fingerprint: str,
        ip_address: str | None = None,
    ) -> bool:
        """
        Check if a device is trusted.

        Args:
            user_id: User ID
            fingerprint: Device fingerprint hash
            ip_address: IP address for audit

        Returns:
            True if device is trusted and not expired
        """
        device = await self._get_trusted_device(user_id, fingerprint)

        if not device:
            return False

        if device.expires_at < datetime.now(timezone.utc):
            return False

        # Update last used
        device.last_used_at = datetime.now(timezone.utc)
        device.last_ip_address = ip_address
        await self.db.flush()

        return True

    async def get_trusted_devices(self, user_id: UUID) -> list[TrustedDevice]:
        """
        Get all trusted devices for a user.

        Args:
            user_id: User ID

        Returns:
            List of trusted devices
        """
        result = await self.db.execute(
            select(TrustedDevice)
            .where(TrustedDevice.user_id == user_id)
            .order_by(TrustedDevice.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke_trusted_device(self, user_id: UUID, device_id: UUID) -> bool:
        """
        Revoke trust for a specific device.

        Args:
            user_id: User ID
            device_id: Device ID to revoke

        Returns:
            True if device was found and revoked
        """
        result: CursorResult = await self.db.execute(  # type: ignore[assignment]
            delete(TrustedDevice).where(
                TrustedDevice.id == device_id,
                TrustedDevice.user_id == user_id
            )
        )
        await self.db.flush()
        return result.rowcount > 0

    async def revoke_all_trusted_devices(self, user_id: UUID) -> int:
        """
        Revoke all trusted devices for a user.

        Args:
            user_id: User ID

        Returns:
            Number of devices revoked
        """
        result: CursorResult = await self.db.execute(  # type: ignore[assignment]
            delete(TrustedDevice).where(TrustedDevice.user_id == user_id)
        )
        await self.db.flush()
        return result.rowcount

    @staticmethod
    def generate_device_fingerprint(user_agent: str, additional_data: str = "") -> str:
        """
        Generate device fingerprint from browser data.

        Args:
            user_agent: Browser user agent string
            additional_data: Additional data for fingerprinting

        Returns:
            64-character hex fingerprint
        """
        data = f"{user_agent}:{additional_data}"
        return hashlib.sha256(data.encode()).hexdigest()[:64]

    # ========================================================================
    # Private Helper Methods
    # ========================================================================

    async def _get_pending_totp(self, user_id: UUID) -> UserMFAMethod | None:
        """Get pending TOTP method for user (most recent if multiple exist).

        The row is locked so two concurrent enrollment verifications cannot
        both activate the same pending method and consume the same code.
        """
        result = await self.db.execute(
            select(UserMFAMethod)
            .where(
                UserMFAMethod.user_id == user_id,
                UserMFAMethod.method_type == MFAMethodType.TOTP,
                UserMFAMethod.status == MFAMethodStatus.PENDING
            )
            .order_by(UserMFAMethod.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def _get_active_totp(self, user_id: UUID) -> UserMFAMethod | None:
        """Get active TOTP method for user (most recent if multiple exist).

        The row is locked so a concurrent login cannot read the same
        ``last_used_counter`` and accept the same code twice.
        """
        result = await self.db.execute(
            select(UserMFAMethod)
            .where(
                UserMFAMethod.user_id == user_id,
                UserMFAMethod.method_type == MFAMethodType.TOTP,
                UserMFAMethod.status == MFAMethodStatus.ACTIVE
            )
            .order_by(UserMFAMethod.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def _delete_pending_totp(self, user_id: UUID) -> None:
        """Delete pending TOTP methods for user."""
        await self.db.execute(
            delete(UserMFAMethod).where(
                UserMFAMethod.user_id == user_id,
                UserMFAMethod.method_type == MFAMethodType.TOTP,
                UserMFAMethod.status == MFAMethodStatus.PENDING
            )
        )

    async def _get_active_methods(self, user_id: UUID) -> list[UserMFAMethod]:
        """Get all active MFA methods for user."""
        result = await self.db.execute(
            select(UserMFAMethod).where(
                UserMFAMethod.user_id == user_id,
                UserMFAMethod.status == MFAMethodStatus.ACTIVE
            )
        )
        return list(result.scalars().all())

    async def _count_active_methods(self, user_id: UUID) -> int:
        """Count active MFA methods for user."""
        from sqlalchemy import func
        result = await self.db.execute(
            select(func.count()).where(
                UserMFAMethod.user_id == user_id,
                UserMFAMethod.status == MFAMethodStatus.ACTIVE
            )
        )
        return result.scalar() or 0

    async def _delete_recovery_codes(self, user_id: UUID) -> None:
        """Delete all recovery codes for user."""
        await self.db.execute(
            delete(MFARecoveryCode).where(MFARecoveryCode.user_id == user_id)
        )

    async def _get_unused_recovery_codes(self, user_id: UUID) -> list[MFARecoveryCode]:
        """Get unused recovery codes for user.

        Rows are locked so two concurrent verifications cannot both consume
        the same recovery code.
        """
        result = await self.db.execute(
            select(MFARecoveryCode)
            .where(
                MFARecoveryCode.user_id == user_id,
                MFARecoveryCode.is_used == False  # noqa: E712
            )
            .with_for_update()
        )
        return list(result.scalars().all())

    async def _count_recovery_codes(self, user_id: UUID) -> int:
        """Count total recovery codes for user."""
        from sqlalchemy import func
        result = await self.db.execute(
            select(func.count()).where(MFARecoveryCode.user_id == user_id)
        )
        return result.scalar() or 0

    async def _count_unused_recovery_codes(self, user_id: UUID) -> int:
        """Count unused recovery codes for user."""
        from sqlalchemy import func
        result = await self.db.execute(
            select(func.count()).where(
                MFARecoveryCode.user_id == user_id,
                MFARecoveryCode.is_used == False  # noqa: E712
            )
        )
        return result.scalar() or 0

    async def _get_trusted_device(
        self,
        user_id: UUID,
        fingerprint: str
    ) -> TrustedDevice | None:
        """Get trusted device by fingerprint."""
        result = await self.db.execute(
            select(TrustedDevice).where(
                TrustedDevice.user_id == user_id,
                TrustedDevice.device_fingerprint == fingerprint
            )
        )
        return result.scalar_one_or_none()
