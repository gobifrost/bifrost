"""The warm execution template must not preload the platform database stack."""

import json
import subprocess
import sys
import textwrap


def test_ready_template_keeps_engine_warm_without_database_or_http_server():
    probe = textwrap.dedent('''
        import json
        import sys
        from src.services.execution import simple_worker
        # The supervisor installs packages before starting/restarting a template.
        installs = []
        def install_requirements():
            installs.append(True)
            return simple_worker.RequirementsInstallResult()
        simple_worker.install_requirements = install_requirements
        from src.services.execution.template_process import _template_main

        class StartupPipe:
            def send(self, message):
                assert message['status'] == 'ready', message
            def poll(self, timeout):
                return True
            def recv(self):
                return {'action': 'shutdown'}

        _template_main(StartupPipe())
        print(json.dumps({"loaded": sorted(sys.modules), "installs": installs}))
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
