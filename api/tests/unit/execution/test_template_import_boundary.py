"""The warm execution template must not preload the platform database stack."""

import json
import subprocess
import sys
import textwrap
from unittest.mock import patch


def test_pre_fork_source_client_cleanup_uses_module_cache_reset_apis():
    """A child inherits no cached module-source connection from the template."""
    from src.services.execution.template_process import _clear_pre_fork_source_clients

    with (
        patch("src.core.module_cache_sync._close_http_client") as close_http,
        patch("src.core.module_cache_sync.reset_sync_redis") as reset_redis,
    ):
        _clear_pre_fork_source_clients()

    close_http.assert_called_once_with()
    reset_redis.assert_called_once_with()


def test_ready_template_keeps_engine_warm_without_database_or_http_server():
    probe = textwrap.dedent('''
        import json
        import os
        import sys
        from src.services.execution import simple_worker
        # The supervisor installs packages before starting/restarting a template.
        installs = []
        def install_requirements():
            installs.append(True)
            return simple_worker.RequirementsInstallResult()
        simple_worker.install_requirements = install_requirements
        os.environ["BIFROST_REDIS_URL"] = "redis://user:password@redis:6379/0"
        os.environ["REDIS_URL"] = "redis://user:password@redis:6379/0"
        blob_keys = [
            "BIFROST_OBJECT_STORAGE_PROVIDER",
            "BIFROST_AZURE_BLOB_ACCOUNT_URL",
            "BIFROST_AZURE_BLOB_CONTAINER",
            "BIFROST_AZURE_BLOB_ACCOUNT_NAME",
            "BIFROST_AZURE_BLOB_ACCOUNT_KEY",
            "BIFROST_AZURE_BLOB_AUTH",
            "BIFROST_AZURE_BLOB_MANAGED_IDENTITY_CLIENT_ID",
        ]
        for key in blob_keys:
            os.environ[key] = "fixture-only"
        os.environ["BIFROST_OBJECT_STORAGE_PROVIDER"] = "azure_blob"
        from src.services.execution.template_process import _template_main

        class StartupPipe:
            def send(self, message):
                assert message['status'] == 'ready', message
            def poll(self, timeout):
                return True
            def recv(self):
                return {'action': 'shutdown'}

        _template_main(StartupPipe())
        from src.config import get_settings
        print(json.dumps({
            "loaded": sorted(sys.modules),
            "installs": installs,
            "redis_env_present": "BIFROST_REDIS_URL" in os.environ,
            "redis_alias_present": "REDIS_URL" in os.environ,
            "settings_redis_url": get_settings().redis_url,
            "blob_env_present": any(key in os.environ for key in blob_keys),
            "settings_blob_key_present": get_settings().azure_blob_account_key is not None,
            "settings_provider": get_settings().object_storage_provider,
        }))
    ''')
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    loaded = set(observed["loaded"])
    assert observed["installs"] == [], "Template must reuse the supervisor-installed packages"
    assert "src.services.execution.engine" in loaded
    assert "src.services.execution.worker" in loaded
    assert {"bifrost.client", "bifrost.models", "src.sdk.decorators"}.issubset(loaded)
    forbidden = {"sqlalchemy", "src.models.orm", "src.core.database", "fastapi", "requests"}
    assert not loaded.intersection(forbidden)
    assert observed["redis_env_present"] is False
    assert observed["redis_alias_present"] is False
    assert observed["settings_redis_url"] == "redis://127.0.0.1:0/0"
    assert observed["blob_env_present"] is False
    assert observed["settings_blob_key_present"] is False
    assert observed["settings_provider"] == "s3"
