"""Bounded source-only Python preflight on the shared file compute transport."""
from __future__ import annotations

import json
import posixpath
import subprocess
import sys
import tempfile
import tomllib
from datetime import date, datetime, time
from pathlib import Path, PurePosixPath

from bifrost.decorator_validation import validate_python_decorators
from bifrost.definition_validation import DefinitionValidationError
from shared.file_compute import FileCompute, encode_file_transfer, decode_file_result
from shared.json_compute_transfer import write_json_transfer, read_json_transfer
from src.models.contracts.github import PreflightIssue
from src.services.editor.file_filter import is_excluded_path

OPERATION = 'workspace.preflight.v1'
INPUT_KIND = 'workspace_preflight_inputs'
RESULT_KIND = 'workspace_preflight_results'
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_FILES = 4096
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_ISSUES = 10000


def source_path(path: object) -> str:
    if not isinstance(path, str) or not path or len(path) > 4096:
        raise ValueError('Invalid preflight source path')
    parts = PurePosixPath(path).parts
    if (PurePosixPath(path).is_absolute() or '..' in parts or '\\' in path or '\0' in path
            or str(PurePosixPath(path)) != path or '.bifrost' in parts or is_excluded_path(path)
            or any(part.startswith('.env') for part in parts)):
        raise ValueError('Invalid preflight source path')
    return path


def ruff_extension(path: str, content: str) -> str | None:
    try:
        value = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return None  # Ruff retains responsibility for malformed lint config.
    settings = value.get('tool', {}).get('ruff', {}) if Path(path).name == 'pyproject.toml' else value
    extend = settings.get('extend') if isinstance(settings, dict) else None
    if not isinstance(extend, str):
        return None
    if PurePosixPath(extend).is_absolute() or '\\' in extend or '$' in extend:
        raise ValueError('Ruff configuration must extend an in-workspace file')
    return source_path(posixpath.normpath(posixpath.join(posixpath.dirname(path), extend)))


def _literal(value):
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + '=' + _literal(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ','.join(_literal(v) for v in value) + ']'
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return json.dumps(value, ensure_ascii=False)


def lint_configuration(path: str, content: str) -> str:
    """Exclude unrelated project metadata, credentials and build configuration."""
    try:
        value = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError('Ruff configuration is not valid TOML') from exc
    if Path(path).name == 'pyproject.toml':
        tool = value.get('tool', {})
        project = value.get('project', {})
        value = {'tool': {'ruff': tool['ruff']}} if isinstance(tool, dict) and 'ruff' in tool else {}
        if isinstance(project, dict) and 'requires-python' in project:
            value['project'] = {'requires-python': project['requires-python']}
    return '\n'.join(json.dumps(k) + '=' + _literal(v) for k, v in value.items())


def validate_inputs(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {'sources', 'configs'}:
        raise ValueError('Invalid preflight source snapshot')
    sources, configs = value['sources'], value['configs']
    if not isinstance(sources, dict) or not isinstance(configs, dict) or len(sources) + len(configs) > MAX_FILES:
        raise ValueError('Preflight snapshot exceeds file budget')
    if set(sources) & set(configs):
        raise ValueError('Conflicting preflight source paths')
    total = 0
    for path, content in {**sources, **configs}.items():
        source_path(path)
        if not isinstance(content, str) or len(content.encode()) > MAX_FILE_BYTES:
            raise ValueError('Preflight source exceeds file byte budget')
        total += len(path.encode()) + len(content.encode())
        if total > MAX_SOURCE_BYTES:
            raise ValueError('Preflight snapshot exceeds source byte budget')
        if path in sources and not path.endswith('.py'):
            raise ValueError('Preflight accepts Python source only')
    for path, content in configs.items():
        dependency = ruff_extension(path, content)
        if dependency is not None and dependency not in configs:
            raise ValueError('Ruff extension is outside the source snapshot')
    return value


def encode_inputs(value):
    return write_json_transfer(validate_inputs(value), INPUT_KIND, MAX_INPUT_BYTES)


def decode_inputs(transfer):
    return validate_inputs(read_json_transfer(transfer, INPUT_KIND, MAX_INPUT_BYTES))


def _issues(value: object, paths: set[str]) -> list[PreflightIssue]:
    if not isinstance(value, list) or len(value) > MAX_ISSUES:
        raise ValueError('Preflight result exceeds issue budget')
    result = [PreflightIssue.model_validate(item) for item in value]
    if any(item.path not in paths or (item.category, item.severity) not in {('syntax', 'error'), ('lint', 'warning')}
            or (item.line is not None and item.line < 1) for item in result):
        raise ValueError('Preflight result is outside its source snapshot')
    return result


def encode_results(issues, digest, *, temp_dir: Path | None = None):
    return write_json_transfer({'input_sha256': digest, 'issues': [i.model_dump(mode='json') for i in issues]}, RESULT_KIND, MAX_RESULT_BYTES, temp_dir=temp_dir)


def decode_results(transfer, *, digest, paths):
    value = read_json_transfer(transfer, RESULT_KIND, MAX_RESULT_BYTES)
    if value.get('input_sha256') != digest:
        raise ValueError('Preflight result belongs to another source snapshot')
    return _issues(value.get('issues'), paths)


def analyze(value: dict, *, workspace: Path | None = None) -> list[PreflightIssue]:
    """Compile/parse source without importing or executing authored code."""
    value = validate_inputs(value)
    issues = []
    for path, source in value['sources'].items():
        try:
            compile(source, path, 'exec')
        except SyntaxError as error:
            issues.append(PreflightIssue(path=path, line=error.lineno, message=f'Syntax error: {error.msg}',
                severity='error', category='syntax', fix_hint=f'Fix the syntax error in {path} at line {error.lineno}.'))
    with tempfile.TemporaryDirectory(prefix='bifrost-preflight-source-', dir=workspace) as raw:
        root = Path(raw)
        for path, content in {**value['sources'], **value['configs']}.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
        if value['sources']:
            try:
                process = subprocess.run([sys.executable, '-m', 'ruff', 'check', '--output-format=json', '--no-fix', '--no-cache',
                    *[str(root / path) for path in value['sources']]], capture_output=True, text=True, timeout=60, cwd=root)
                if process.stdout.strip():
                    for violation in json.loads(process.stdout):
                        path = Path(violation['filename']).relative_to(root).as_posix()
                        issues.append(PreflightIssue(path=path, line=violation.get('location', {}).get('row'),
                            message=f"{violation.get('code', '?')}: {violation.get('message', '')}", severity='warning', category='lint',
                            fix_hint="This is a style warning and won't block your commit."))
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass  # Preserve the existing optional lint behavior.
    for path, source in value['sources'].items():
        try:
            validate_python_decorators(source, path=path)
        except DefinitionValidationError as error:
            for issue in error.issues:
                field = '.'.join(map(str, issue.location))
                issues.append(PreflightIssue(path=issue.path, line=issue.line,
                    message=f'{field}: {issue.message}' if field else issue.message,
                    severity='error', category='syntax', fix_hint='Remove or migrate the unsupported decorator setting, then retry.'))
    return _issues([i.model_dump(mode='json') for i in issues], set(value['sources']))


async def analyze_using(value: dict, compute: FileCompute) -> list[PreflightIssue]:
    transfer = await encode_file_transfer(encode_inputs, value)
    try:
        result = await compute(OPERATION, transfer)
        return await decode_file_result(result, decode_results, digest=transfer.manifest.sha256, paths=set(value['sources']))
    finally:
        transfer.close()
