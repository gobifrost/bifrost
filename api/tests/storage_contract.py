"""Fixed local storage settings shared by the real-backend contract lanes."""

from pydantic import SecretStr

from src.config import Settings


LOCAL_ACCOUNT_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def local_settings(provider: str) -> Settings:
    """Never select a cloud endpoint or credentials from the caller's environment."""
    assert provider in {"s3", "azure_blob"}
    return Settings(
        _env_file=None,
        object_storage_provider=provider,
        s3_bucket="bifrost-test" if provider == "s3" else None,
        s3_endpoint_url="http://seaweedfs:8333" if provider == "s3" else None,
        s3_access_key="bifrost" if provider == "s3" else None,
        s3_secret_key="bifrost_test_key" if provider == "s3" else None,
        s3_region="us-east-1",
        s3_public_endpoint_url=None,
        azure_blob_auth="account_key" if provider == "azure_blob" else "managed_identity",
        azure_blob_account_url="http://azurite:10000/bifrostcontract" if provider == "azure_blob" else None,
        azure_blob_container="storage-contract" if provider == "azure_blob" else None,
        azure_blob_account_name="bifrostcontract" if provider == "azure_blob" else None,
        azure_blob_account_key=SecretStr(LOCAL_ACCOUNT_KEY) if provider == "azure_blob" else None,
        azure_blob_managed_identity_client_id=None,
    )
