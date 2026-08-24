"""Business logic for reusable AI provider connections and model profiles."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.config import get_settings
from src.models.contracts.ai_models import AIModelAssignmentKey, AIProviderKind
from src.models.contracts.artifacts import ModelCapabilities
from src.models.orm.agents import Agent
from src.models.orm.ai_models import (
    AIEmbeddingConfig,
    AIModelAssignment,
    AIModelProfile,
    AIProviderConnection,
)

if TYPE_CHECKING:
    from src.services.embeddings.base import EmbeddingConfig
    from src.services.llm.base import LLMConfig
    from src.services.provider_catalog_service import (
        ProviderModelInfo,
        ProviderTestResult,
    )

OPENROUTER_DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1"
PROVIDER_DEFAULT_ENDPOINTS: dict[AIProviderKind, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "google": "https://generativelanguage.googleapis.com",
    "openrouter": OPENROUTER_DEFAULT_ENDPOINT,
}
ASSIGNMENT_KEYS: tuple[AIModelAssignmentKey, ...] = (
    "primary",
    "summarization",
    "tuning",
    "image_generation",
    "video_generation",
    "chat_default",
)


@dataclass(frozen=True)
class ProviderConnectionTestConfig:
    provider: AIProviderKind
    api_key: str
    endpoint: str | None


@dataclass(frozen=True)
class ModelProfileMergeResult:
    profile: AIModelProfile
    merged_profile_ids: tuple[UUID, ...]
    reassigned_agent_count: int
    reassigned_assignment_keys: tuple[AIModelAssignmentKey, ...]


class AIModelService:
    """Manage named provider connections, reusable profiles, and global assignments."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    def _get_fernet(self) -> Fernet:
        key_bytes = self.settings.secret_key.encode()[:32].ljust(32, b"0")
        return Fernet(base64.urlsafe_b64encode(key_bytes))

    def encrypt_api_key(self, api_key: str) -> str:
        if not api_key or not api_key.strip():
            raise ValueError("API key is required")
        return self._get_fernet().encrypt(api_key.strip().encode()).decode()

    def decrypt_api_key(self, encrypted_api_key: str) -> str:
        return self._get_fernet().decrypt(encrypted_api_key.encode()).decode()

    async def _ensure_unique_connection_name(
        self, name: str, *, exclude_id: UUID | None = None
    ) -> None:
        statement = select(AIProviderConnection).where(
            func.lower(AIProviderConnection.name) == name.casefold()
        )
        if exclude_id:
            statement = statement.where(AIProviderConnection.id != exclude_id)
        existing = (await self.session.execute(statement)).scalars().first()
        if existing:
            raise ValueError("Provider connection name already exists")

    async def _ensure_unique_profile_name(
        self, name: str, *, exclude_id: UUID | None = None
    ) -> None:
        statement = select(AIModelProfile).where(
            func.lower(AIModelProfile.name) == name.casefold()
        )
        if exclude_id:
            statement = statement.where(AIModelProfile.id != exclude_id)
        existing = (await self.session.execute(statement)).scalars().first()
        if existing:
            raise ValueError("Model profile name already exists")

    def normalize_endpoint(
        self, provider: AIProviderKind, endpoint: str | None
    ) -> str | None:
        value = endpoint.strip().rstrip("/") if endpoint else None
        if value:
            return value
        if provider == "openai_compatible":
            raise ValueError("Endpoint is required for OpenAI-compatible providers")
        return PROVIDER_DEFAULT_ENDPOINTS[provider]

    def client_provider(self, provider: AIProviderKind) -> str:
        if provider in ("openrouter", "openai_compatible"):
            return "openai"
        return provider

    def embedding_client_endpoint(
        self, provider: AIProviderKind, endpoint: str | None
    ) -> str | None:
        if provider == "openai" and endpoint in (None, "https://api.openai.com/v1"):
            return None
        return endpoint

    async def resolve_config(
        self,
        *,
        profile_id: UUID | None = None,
        profile_name: str | None = None,
        assignment_key: AIModelAssignmentKey = "primary",
    ) -> LLMConfig:
        """Resolve a profile UUID or assignment key into decrypted runtime LLM config."""
        from src.services.llm.base import LLMConfig

        if profile_id is not None and profile_name is not None:
            raise ValueError("Specify either a model profile id or name, not both")
        if profile_id is not None:
            profile = (
                (
                    await self.session.execute(
                        select(AIModelProfile)
                        .options(joinedload(AIModelProfile.connection))
                        .where(AIModelProfile.id == profile_id)
                    )
                )
                .scalars()
                .first()
            )
            if profile is None:
                raise LookupError("Model profile not found")
        elif profile_name is not None:
            trimmed_profile_name = profile_name.strip()
            if not trimmed_profile_name:
                raise ValueError("Model profile name cannot be blank")
            profile = (
                (
                    await self.session.execute(
                        select(AIModelProfile)
                        .options(joinedload(AIModelProfile.connection))
                        .where(
                            func.lower(AIModelProfile.name)
                            == trimmed_profile_name.casefold()
                        )
                    )
                )
                .scalars()
                .first()
            )
            if profile is None:
                raise ValueError(
                    f"Model profile '{trimmed_profile_name}' was not found"
                )
        else:
            assignment = (
                (
                    await self.session.execute(
                        select(AIModelAssignment)
                        .options(
                            joinedload(AIModelAssignment.profile).joinedload(
                                AIModelProfile.connection
                            )
                        )
                        .where(AIModelAssignment.assignment_key == assignment_key)
                    )
                )
                .scalars()
                .first()
            )
            if not assignment:
                raise ValueError(
                    f"LLM model assignment '{assignment_key}' is not configured. "
                    "Please configure AI model profiles in System Settings > AI Configuration."
                )
            profile = assignment.profile

        connection = profile.connection
        provider = self.client_provider(connection.provider)
        if provider not in ("openai", "anthropic", "google"):
            raise ValueError(f"Unsupported LLM provider '{connection.provider}'.")

        api_key = self.decrypt_api_key(connection.encrypted_api_key)
        if not api_key:
            raise ValueError(
                f"No API key configured for LLM provider connection '{connection.name}'. "
                "Please configure the API key in System Settings > AI Configuration."
            )

        return LLMConfig(
            provider=provider,
            model=profile.model,
            api_key=api_key,
            endpoint=connection.endpoint,
        )

    async def list_chat_profiles(self) -> tuple[list[AIModelProfile], UUID | None]:
        """Return profiles enabled for Chat plus the configured default profile id."""
        profiles = list(
            (
                await self.session.execute(
                    select(AIModelProfile)
                    .options(
                        joinedload(AIModelProfile.connection),
                        selectinload(AIModelProfile.assignments),
                    )
                    .where(AIModelProfile.enabled_for_chat.is_(True))
                    .order_by(func.lower(AIModelProfile.name))
                )
            )
            .unique()
            .scalars()
            .all()
        )
        assignment = (
            (
                await self.session.execute(
                    select(AIModelAssignment).where(
                        AIModelAssignment.assignment_key == "chat_default"
                    )
                )
            )
            .scalars()
            .first()
        )
        return profiles, assignment.profile_id if assignment else None

    def normalized_profile_capabilities(
        self, profile: AIModelProfile
    ) -> ModelCapabilities:
        """Normalize a profile's stored capability metadata without exposing model ids."""
        # Keep capability discovery lazy: that module imports the LLM package,
        # whose public factory resolves profiles through this service.
        from src.services.model_capabilities import normalize_capabilities

        return normalize_capabilities(
            ModelCapabilities.model_validate(profile.capabilities)
            if profile.capabilities
            else None,
            provider=profile.connection.provider,
            model=profile.model,
            endpoint=profile.connection.endpoint,
        )

    async def resolve_chat_profile(
        self,
        profile_id: UUID | None = None,
    ) -> tuple[AIModelProfile, LLMConfig, ModelCapabilities]:
        """Resolve an explicit Chat profile UUID or the default Chat assignment."""
        if profile_id is not None:
            profile = await self.get_profile(profile_id)
        else:
            assignment = (
                (
                    await self.session.execute(
                        select(AIModelAssignment)
                        .options(
                            joinedload(AIModelAssignment.profile).joinedload(
                                AIModelProfile.connection
                            ),
                            joinedload(AIModelAssignment.profile).selectinload(
                                AIModelProfile.assignments
                            ),
                        )
                        .where(AIModelAssignment.assignment_key == "chat_default")
                    )
                )
                .unique()
                .scalars()
                .first()
            )
            if not assignment:
                raise ValueError(
                    "Default Chat model profile is not configured. "
                    "Please configure AI model profiles in System Settings > AI Configuration."
                )
            profile = assignment.profile

        if not profile.enabled_for_chat:
            raise ValueError(f"Model profile '{profile.name}' is not enabled for Chat.")

        config = await self.resolve_config(profile_id=profile.id)
        return profile, config, self.normalized_profile_capabilities(profile)

    async def list_connections(self) -> list[AIProviderConnection]:
        return list(
            (
                await self.session.execute(
                    select(AIProviderConnection).order_by(
                        func.lower(AIProviderConnection.name)
                    )
                )
            )
            .unique()
            .scalars()
            .all()
        )

    async def get_connection(self, connection_id: UUID) -> AIProviderConnection:
        connection = (
            (
                await self.session.execute(
                    select(AIProviderConnection)
                    .options(selectinload(AIProviderConnection.profiles))
                    .where(AIProviderConnection.id == connection_id)
                )
            )
            .unique()
            .scalars()
            .first()
        )
        if not connection:
            raise LookupError("Provider connection not found")
        return connection

    async def create_connection(
        self,
        *,
        name: str,
        provider: AIProviderKind,
        api_key: str,
        endpoint: str | None,
    ) -> AIProviderConnection:
        trimmed_name = name.strip()
        if not trimmed_name:
            raise ValueError("Provider connection name is required")
        await self._ensure_unique_connection_name(trimmed_name)
        connection = AIProviderConnection(
            name=trimmed_name,
            provider=provider,
            endpoint=self.normalize_endpoint(provider, endpoint),
            encrypted_api_key=self.encrypt_api_key(api_key),
        )
        self.session.add(connection)
        await self.session.flush()
        return connection

    async def update_connection(
        self,
        connection_id: UUID,
        *,
        name: str | None = None,
        provider: AIProviderKind | None = None,
        api_key: str | None = None,
        endpoint: str | None = None,
        endpoint_provided: bool = False,
    ) -> AIProviderConnection:
        connection = await self.get_connection(connection_id)
        if name is not None:
            trimmed_name = name.strip()
            if not trimmed_name:
                raise ValueError("Provider connection name is required")
            await self._ensure_unique_connection_name(
                trimmed_name, exclude_id=connection.id
            )
            connection.name = trimmed_name
        if provider is not None:
            connection.provider = provider
        if provider is not None or endpoint_provided:
            connection.endpoint = self.normalize_endpoint(connection.provider, endpoint)
        if api_key is not None:
            connection.encrypted_api_key = self.encrypt_api_key(api_key)
        connection.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return connection

    async def delete_connection(self, connection_id: UUID) -> None:
        connection = await self.get_connection(connection_id)
        profile_count = (
            await self.session.execute(
                select(func.count())
                .select_from(AIModelProfile)
                .where(AIModelProfile.connection_id == connection.id)
            )
        ).scalar_one()
        if profile_count:
            raise ValueError("Provider connection is used by model profiles")
        embedding_count = (
            await self.session.execute(
                select(func.count())
                .select_from(AIEmbeddingConfig)
                .where(AIEmbeddingConfig.connection_id == connection.id)
            )
        ).scalar_one()
        if embedding_count:
            raise ValueError(
                "Provider connection is used by the embedding configuration"
            )
        await self.session.delete(connection)
        await self.session.flush()

    async def list_profiles(self) -> list[AIModelProfile]:
        return list(
            (
                await self.session.execute(
                    select(AIModelProfile).order_by(func.lower(AIModelProfile.name))
                )
            )
            .unique()
            .scalars()
            .all()
        )

    async def get_profile(self, profile_id: UUID) -> AIModelProfile:
        profile = (
            (
                await self.session.execute(
                    select(AIModelProfile)
                    .options(
                        joinedload(AIModelProfile.connection),
                        selectinload(AIModelProfile.assignments),
                    )
                    .where(AIModelProfile.id == profile_id)
                    .execution_options(populate_existing=True)
                )
            )
            .unique()
            .scalars()
            .first()
        )
        if not profile:
            raise LookupError("Model profile not found")
        return profile

    async def create_profile(
        self,
        *,
        name: str,
        connection_id: UUID,
        model: str,
        capabilities: ModelCapabilities | None,
        enabled_for_chat: bool,
    ) -> AIModelProfile:
        trimmed_name = name.strip()
        trimmed_model = model.strip()
        if not trimmed_name:
            raise ValueError("Model profile name is required")
        if not trimmed_model:
            raise ValueError("Model id is required")
        await self._ensure_unique_profile_name(trimmed_name)
        await self.get_connection(connection_id)
        is_first_profile = (
            await self.session.execute(select(AIModelProfile.id).limit(1))
        ).scalar_one_or_none() is None
        profile = AIModelProfile(
            name=trimmed_name,
            connection_id=connection_id,
            model=trimmed_model,
            capabilities=capabilities.model_dump(mode="json") if capabilities else None,
            enabled_for_chat=enabled_for_chat or is_first_profile,
        )
        self.session.add(profile)
        await self.session.flush()
        if is_first_profile:
            self.session.add_all(
                [
                    AIModelAssignment(
                        assignment_key=assignment_key,
                        profile_id=profile.id,
                    )
                    for assignment_key in ASSIGNMENT_KEYS
                ]
            )
            await self.session.flush()
        elif enabled_for_chat and not await self.has_assignment("chat_default"):
            assignment = AIModelAssignment(
                assignment_key="chat_default",
                profile_id=profile.id,
            )
            self.session.add(assignment)
            await self.session.flush()
        return await self.get_profile(profile.id)

    async def update_profile(
        self,
        profile_id: UUID,
        *,
        name: str | None = None,
        connection_id: UUID | None = None,
        model: str | None = None,
        capabilities: ModelCapabilities | None = None,
        capabilities_provided: bool = False,
        enabled_for_chat: bool | None = None,
    ) -> AIModelProfile:
        profile = await self.get_profile(profile_id)
        if name is not None:
            trimmed_name = name.strip()
            if not trimmed_name:
                raise ValueError("Model profile name is required")
            await self._ensure_unique_profile_name(trimmed_name, exclude_id=profile.id)
            profile.name = trimmed_name
        if connection_id is not None:
            await self.get_connection(connection_id)
            profile.connection_id = connection_id
        if model is not None:
            trimmed_model = model.strip()
            if not trimmed_model:
                raise ValueError("Model id is required")
            profile.model = trimmed_model
        if capabilities_provided:
            profile.capabilities = (
                capabilities.model_dump(mode="json") if capabilities else None
            )
        if enabled_for_chat is not None:
            if not enabled_for_chat and await self._profile_has_assignment(
                profile.id, "chat_default"
            ):
                raise ValueError("The default chat profile must stay enabled for chat")
            profile.enabled_for_chat = enabled_for_chat
            if enabled_for_chat and not await self.has_assignment("chat_default"):
                assignment = AIModelAssignment(
                    assignment_key="chat_default",
                    profile_id=profile.id,
                )
                self.session.add(assignment)
        profile.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return await self.get_profile(profile.id)

    async def delete_profile(self, profile_id: UUID) -> None:
        profile = await self.get_profile(profile_id)
        if await self._profile_has_any_assignment(profile.id):
            raise ValueError("Model profile is used by assignments")
        if profile.agents:
            raise ValueError("Model profile is used by agents")
        await self.session.delete(profile)
        await self.session.flush()

    async def merge_profiles(
        self,
        *,
        profile_ids: list[UUID],
        target_profile_id: UUID,
    ) -> ModelProfileMergeResult:
        selected_ids = set(profile_ids)
        if len(selected_ids) != len(profile_ids):
            raise ValueError("Profile selection must not contain duplicates")
        if len(selected_ids) < 2:
            raise ValueError("Select at least two model profiles to merge")
        if target_profile_id not in selected_ids:
            raise ValueError("Target profile must be included in the profile selection")

        profiles = list(
            (
                await self.session.execute(
                    select(AIModelProfile)
                    .where(AIModelProfile.id.in_(selected_ids))
                    .with_for_update(of=AIModelProfile)
                )
            )
            .unique()
            .scalars()
            .all()
        )
        if len(profiles) != len(selected_ids):
            raise LookupError("One or more model profiles were not found")

        target = next(
            profile for profile in profiles if profile.id == target_profile_id
        )
        source_ids = selected_ids - {target_profile_id}
        preserve_chat = any(profile.enabled_for_chat for profile in profiles)

        assignments = list(
            (
                await self.session.execute(
                    select(AIModelAssignment)
                    .where(AIModelAssignment.profile_id.in_(source_ids))
                    .with_for_update(of=AIModelAssignment)
                )
            )
            .scalars()
            .all()
        )
        for assignment in assignments:
            assignment.profile = target

        agents = list(
            (
                await self.session.execute(
                    select(Agent)
                    .where(Agent.llm_profile_id.in_(source_ids))
                    .with_for_update(of=Agent)
                )
            )
            .scalars()
            .all()
        )
        for agent in agents:
            agent.llm_profile = target

        target.enabled_for_chat = preserve_chat
        target.updated_at = datetime.now(timezone.utc)
        for profile in profiles:
            if profile.id in source_ids:
                await self.session.delete(profile)
        await self.session.flush()

        merged_profile_ids = tuple(sorted(source_ids, key=str))
        assignment_keys = tuple(
            sorted(
                (assignment.assignment_key for assignment in assignments),
            )
        )
        return ModelProfileMergeResult(
            profile=await self.get_profile(target.id),
            merged_profile_ids=merged_profile_ids,
            reassigned_agent_count=len(agents),
            reassigned_assignment_keys=assignment_keys,
        )

    async def list_assignments(self) -> list[AIModelAssignment]:
        return list(
            (
                await self.session.execute(
                    select(AIModelAssignment)
                    .options(
                        joinedload(AIModelAssignment.profile).joinedload(
                            AIModelProfile.connection
                        ),
                        joinedload(AIModelAssignment.profile).selectinload(
                            AIModelProfile.assignments
                        ),
                    )
                    .order_by(AIModelAssignment.assignment_key)
                )
            )
            .unique()
            .scalars()
            .all()
        )

    async def set_assignment(
        self, assignment_key: AIModelAssignmentKey, profile_id: UUID
    ) -> AIModelAssignment:
        if assignment_key not in ASSIGNMENT_KEYS:
            raise ValueError("Unknown model assignment")
        profile = await self.get_profile(profile_id)
        if assignment_key == "chat_default" and not profile.enabled_for_chat:
            raise ValueError("Default chat assignment requires a chat-enabled profile")
        assignment = await self.session.get(AIModelAssignment, assignment_key)
        if assignment:
            assignment.profile = profile
            assignment.updated_at = datetime.now(timezone.utc)
        else:
            assignment = AIModelAssignment(
                assignment_key=assignment_key, profile_id=profile.id
            )
            self.session.add(assignment)
        await self.session.flush()
        return assignment

    async def clear_assignment(self, assignment_key: AIModelAssignmentKey) -> bool:
        if assignment_key in ("primary", "chat_default"):
            raise ValueError(f"The '{assignment_key}' assignment is required")
        result = await self.session.execute(
            delete(AIModelAssignment).where(
                AIModelAssignment.assignment_key == assignment_key
            )
        )
        await self.session.flush()
        return bool(result.rowcount)

    async def has_assignment(self, assignment_key: AIModelAssignmentKey) -> bool:
        return bool(
            (
                await self.session.execute(
                    select(AIModelAssignment.assignment_key).where(
                        AIModelAssignment.assignment_key == assignment_key
                    )
                )
            ).first()
        )

    async def test_connection_config(
        self, config: ProviderConnectionTestConfig
    ) -> ProviderTestResult:
        from src.services.provider_catalog_service import (
            ProviderCatalogService,
            ProviderTestResult,
        )

        service = ProviderCatalogService()
        provider = self.client_provider(config.provider)
        endpoint = self.normalize_endpoint(config.provider, config.endpoint)
        if provider == "openai":
            return await service.list_openai(config.api_key, endpoint)
        if provider == "anthropic":
            return await service.list_anthropic(config.api_key, endpoint)
        if provider == "google":
            return await service.list_google(config.api_key, endpoint)
        return ProviderTestResult(
            success=False, message=f"Unknown provider: {config.provider}"
        )

    async def test_saved_connection(self, connection_id: UUID) -> ProviderTestResult:
        connection = await self.get_connection(connection_id)
        return await self.test_connection_config(
            ProviderConnectionTestConfig(
                provider=connection.provider,
                api_key=self.decrypt_api_key(connection.encrypted_api_key),
                endpoint=connection.endpoint,
            )
        )

    async def list_models(self, connection_id: UUID) -> list[ProviderModelInfo] | None:
        result = await self.test_saved_connection(connection_id)
        if not result.success:
            return None
        return result.models

    async def get_embedding_config_row(self) -> AIEmbeddingConfig | None:
        return (
            (
                await self.session.execute(
                    select(AIEmbeddingConfig)
                    .options(joinedload(AIEmbeddingConfig.connection))
                    .where(AIEmbeddingConfig.key == "default")
                )
            )
            .unique()
            .scalars()
            .first()
        )

    async def set_embedding_config(
        self,
        *,
        connection_id: UUID,
        model: str,
        dimensions: int,
    ) -> AIEmbeddingConfig:
        trimmed_model = model.strip()
        if not trimmed_model:
            raise ValueError("Embedding model id is required")
        if dimensions < 1:
            raise ValueError("Embedding dimensions must be positive")
        await self.get_connection(connection_id)
        config = await self.session.get(AIEmbeddingConfig, "default")
        if config:
            config.connection_id = connection_id
            config.model = trimmed_model
            config.dimensions = dimensions
            config.updated_at = datetime.now(timezone.utc)
        else:
            config = AIEmbeddingConfig(
                key="default",
                connection_id=connection_id,
                model=trimmed_model,
                dimensions=dimensions,
            )
            self.session.add(config)
        await self.session.flush()
        return await self.get_embedding_config_row() or config

    async def delete_embedding_config(self) -> bool:
        config = await self.session.get(AIEmbeddingConfig, "default")
        if not config:
            return False
        await self.session.delete(config)
        await self.session.flush()
        return True

    async def resolve_embedding_config(self) -> EmbeddingConfig:
        from src.services.embeddings.base import EmbeddingConfig

        config = await self.get_embedding_config_row()
        if not config:
            raise ValueError(
                "No embedding configuration found. "
                "Please configure embedding settings in System Settings > AI Configuration."
            )
        connection = config.connection
        if self.client_provider(connection.provider) != "openai":
            raise ValueError(
                f"Embedding provider connection '{connection.name}' is not OpenAI-compatible."
            )
        return EmbeddingConfig(
            api_key=self.decrypt_api_key(connection.encrypted_api_key),
            model=config.model,
            dimensions=config.dimensions,
            endpoint=self.embedding_client_endpoint(
                connection.provider, connection.endpoint
            ),
        )

    async def _profile_has_any_assignment(self, profile_id: UUID) -> bool:
        return bool(
            (
                await self.session.execute(
                    select(AIModelAssignment.assignment_key)
                    .where(AIModelAssignment.profile_id == profile_id)
                    .limit(1)
                )
            ).first()
        )

    async def _profile_has_assignment(
        self, profile_id: UUID, assignment_key: AIModelAssignmentKey
    ) -> bool:
        return bool(
            (
                await self.session.execute(
                    select(AIModelAssignment.assignment_key).where(
                        AIModelAssignment.profile_id == profile_id,
                        AIModelAssignment.assignment_key == assignment_key,
                    )
                )
            ).first()
        )
