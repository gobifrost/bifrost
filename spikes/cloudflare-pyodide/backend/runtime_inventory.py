"""Validate diagnostic runtime inventory; not authorization or admission."""
import json
import re

from runtime_guard import check_runtime


def validate_inventory(probe):
    if not isinstance(probe, dict) or probe.get('package_environment') != 'pyodide':
        raise ValueError('Actual Python Worker inventory required')
    if probe.get('worker_imported') is not True or probe.get('http_module_path_available') is not True:
        raise ValueError('Upstream execution module probe required')
    check_runtime(probe.get('runtime'))
    packages = probe.get('packages')
    if not isinstance(packages, list) or not 1 <= len(packages) <= 512:
        raise ValueError('Bounded observed package inventory required')
    seen = set()
    for package in packages:
        if not isinstance(package, dict) or set(package) != {'name', 'version'}:
            raise ValueError('Observed name/version package record required')
        name, version = package['name'], package['version']
        if (not isinstance(name, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', name) is None
            or not isinstance(version, str) or not 1 <= len(version) <= 128
            or not version.isascii() or any(char.isspace() or ord(char) < 33 for char in version)):
            raise ValueError('Invalid observed package identity')
        canonical = re.sub(r'[-_.]+', '-', name).lower()
        if canonical in seen:
            raise ValueError('Ambiguous observed package identity')
        seen.add(canonical)
    if not {'httpx', 'pydantic', 'pydantic-core'} <= seen:
        raise ValueError('Required engine/SDK packages absent from inventory')
    return json.loads(json.dumps(probe, allow_nan=False))


def bind_package_requirements(inventory, requirements):
    """Match exact distribution versions; metadata is not behavior qualification."""
    if not isinstance(requirements, dict) or len(requirements) > 32:
        raise ValueError('Explicit bounded package requirements required')
    bound = {}
    for name, version in requirements.items():
        if (not isinstance(name, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', name) is None
            or not isinstance(version, str) or not 1 <= len(version) <= 128
            or not version.isascii() or any(char.isspace() or ord(char) < 33 for char in version)):
            raise ValueError('Exact package identities required')
        canonical = re.sub(r'[-_.]+', '-', name).lower()
        if canonical in bound:
            raise ValueError('Ambiguous package requirement')
        bound[canonical] = version
    if bound:
        if inventory is None:
            raise ValueError('Observed runtime inventory required before package binding')
        observed = {re.sub(r'[-_.]+', '-', item['name']).lower(): item['version']
                    for item in inventory['packages']}
        if any(observed.get(name) != version for name, version in bound.items()):
            raise ValueError('Required package/version absent from pinned runtime')
    return dict(sorted(bound.items()))
