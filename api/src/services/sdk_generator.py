"""
SDK Generator Service for Bifrost.

Generates Python SDK modules from OpenAPI specifications with integration-aware
authentication. Generated SDKs automatically fetch credentials from Bifrost
integrations at runtime.

Supported auth types:
- bearer: Authorization: Bearer {token}
- api_key: Custom header with API key (e.g., x-api-key)
- basic: Authorization: Basic {base64(username:password)}
- oauth: Uses Bifrost OAuth provider tokens
"""

import ipaddress
import json
import logging
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import requests
import yaml
from jinja2 import FileSystemLoader
from jinja2.sandbox import SandboxedEnvironment

from src.core.log_safety import log_safe

logger = logging.getLogger(__name__)

AuthType = Literal["bearer", "api_key", "basic", "oauth"]

# Template directory
TEMPLATE_DIR = Path(__file__).parent / "templates"


# =============================================================================
# Data Structures for Template
# =============================================================================


@dataclass
class FieldInfo:
    """Information about a dataclass field."""

    name: str
    original_name: str
    type: str
    default: str | None = None
    comment: str | None = None


@dataclass
class ModelInfo:
    """Information about a generated model/dataclass."""

    name: str
    description: str | None = None
    fields: list[FieldInfo] = field(default_factory=list)


@dataclass
class MethodInfo:
    """Information about a generated API method."""

    name: str
    http_method: str
    url_template: str
    params: str
    return_type: str
    summary: str


@dataclass(frozen=True)
class ValidatedSpecUrl:
    """OpenAPI spec URL that has passed SSRF safety checks."""

    value: str


# =============================================================================
# Spec Loading
# =============================================================================


# Optional defense-in-depth: operators can pin SDK fetches to a known set of
# hostnames via the `SDK_GENERATOR_ALLOWED_HOSTS` env var (comma-separated).
# When unset, the URL still has to be https + resolve to a public IP. When set,
# the URL must additionally match one of the listed hostnames (case-insensitive,
# exact match — no wildcards). This lets an operator say "we only fetch specs
# from api.example.com and docs.example.com" without code changes.
_SDK_ALLOWED_HOSTS_ENV = "SDK_GENERATOR_ALLOWED_HOSTS"


def _allowed_hosts() -> set[str] | None:
    raw = os.environ.get(_SDK_ALLOWED_HOSTS_ENV, "").strip()
    if not raw:
        return None
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _validate_spec_url(url: str) -> ValidatedSpecUrl:
    """Validate a URL is safe to fetch (https only, public address).

    Prevents SSRF by rejecting non-https schemes and hostnames that resolve
    to private, loopback, link-local, reserved, or multicast addresses.
    Optionally enforces a hostname allowlist from `SDK_GENERATOR_ALLOWED_HOSTS`.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Only https URLs are allowed, got {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("URL must have a hostname")

    hostname = parsed.hostname.lower()
    allowed = _allowed_hosts()
    if allowed is not None and hostname not in allowed:
        raise ValueError(
            f"Hostname {hostname!r} is not in SDK_GENERATOR_ALLOWED_HOSTS"
        )

    try:
        addr_info = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        raise ValueError(f"Cannot resolve hostname {parsed.hostname}: {e}") from e
    for _family, _type, _proto, _canon, sockaddr in addr_info:
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                f"URL hostname resolves to non-public address: {ip_str}"
            )
    return ValidatedSpecUrl(parsed.geturl())


def load_spec_from_url(url: str) -> dict:
    """Load OpenAPI spec from a URL.

    Only https URLs that resolve to public addresses are allowed. Redirects
    are disabled to prevent redirect-to-private bypass.
    """
    safe_url = _validate_spec_url(url)
    # CodeQL/Sonar: _validate_spec_url rejects non-HTTPS and non-public hosts;
    # redirects stay disabled so validation cannot be bypassed post-check.
    response = requests.get(safe_url.value, timeout=30, allow_redirects=False)  # NOSONAR
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")

    if "json" in content_type or url.endswith(".json"):
        return response.json()
    else:
        return yaml.safe_load(response.text)


def load_spec_from_content(content: str, content_type: str = "json") -> dict:
    """Load OpenAPI spec from raw content."""
    if content_type == "json":
        return json.loads(content)
    else:
        return yaml.safe_load(content)


# =============================================================================
# Name Utilities
# =============================================================================


def to_snake_case(name: str) -> str:
    """Convert PascalCase or camelCase to snake_case."""
    s1 = re.sub(r"([a-z0-9])(?=[A-Z])", r"\1_", name)
    s2 = re.sub(r"([A-Z])(?=[A-Z][a-z])", r"\1_", s1)
    return s2.lower().replace("-", "_").replace(" ", "_")


def to_pascal_case(name: str) -> str:
    """Convert snake_case, kebab-case, or camelCase to PascalCase."""
    name = re.sub(r"\d{1,32}\.\d{1,32}", "", name)
    words = re.split(r"[_\-\s.]+", name)

    result_parts = []
    for word in words:
        if not word:
            continue
        if word.isupper() and len(word) > 1:
            result_parts.append(word)
        elif any(c.isupper() for c in word[1:]):
            camel_words = re.sub(
                "([A-Z][a-z]+)", r" \1", re.sub("([A-Z]+)", r" \1", word)
            ).split()
            for cw in camel_words:
                if cw:
                    result_parts.append(cw.capitalize())
        else:
            result_parts.append(word.capitalize())

    result = "".join(result_parts)
    result = re.sub(r"[^a-zA-Z0-9]", "", result)
    if result and result[0].isdigit():
        result = f"Api{result}"
    return result or "ApiClient"


def sanitize_class_name(name: str) -> str:
    """Sanitize a string to be a valid Python class name."""
    name = re.sub(r"\d{1,32}\.\d{1,32}", "", name)
    name = re.sub(r"[^a-zA-Z0-9]+", "", name)
    if name and name[0].isdigit():
        name = f"Api{name}"
    return name or "ApiClient"


def pluralize(word: str) -> str:
    """Convert a singular English word to its plural form."""
    if not word:
        return word

    if word.endswith("s") and not word.endswith(("ss", "us", "is", "os")):
        return word

    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"

    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"

    if word.endswith("f") and word not in ("roof", "proof", "chief", "belief"):
        return word[:-1] + "ves"

    if word.endswith("fe"):
        return word[:-2] + "ves"

    if word.endswith("o") and len(word) > 1 and word[-2] not in "aeiou":
        if word not in ("photo", "piano", "memo", "zoo"):
            return word + "es"

    return word + "s"


PYTHON_KEYWORDS = {
    "and", "as", "assert", "break", "class", "continue", "def", "del", "elif",
    "else", "except", "False", "finally", "for", "from", "global", "if",
    "import", "in", "is", "lambda", "None", "nonlocal", "not", "or", "pass",
    "raise", "return", "True", "try", "while", "with", "yield", "async", "await",
}


def sanitize_field_name(name: str) -> str:
    """Sanitize a field name to be a valid Python identifier."""
    py_name = to_snake_case(name)

    if py_name and py_name[0].isdigit():
        py_name = f"field_{py_name}"

    py_name = re.sub(r"^[^a-zA-Z_]", "_", py_name)
    py_name = re.sub(r"[^a-zA-Z0-9_]", "_", py_name)

    if py_name in PYTHON_KEYWORDS:
        py_name = f"{py_name}_"

    return py_name


# =============================================================================
# Spec Sanitization
# =============================================================================


def _sanitize_value_recursive(value: Any) -> Any:
    """Recursively normalize OpenAPI type names."""
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            result[k] = _sanitize_value_recursive(v)

        if "type" in result and isinstance(result["type"], str):
            type_val = result["type"]

            if type_val.startswith("List<") and type_val.endswith(">"):
                inner_type = type_val[5:-1]
                inner_type_mapping = {
                    "int": "integer",
                    "bool": "boolean",
                    "float": "number",
                    "str": "string",
                    "string": "string",
                    "integer": "integer",
                    "boolean": "boolean",
                    "number": "number",
                }
                mapped_inner = inner_type_mapping.get(inner_type, inner_type)
                result["type"] = "array"
                result["items"] = {"type": mapped_inner}
            else:
                type_mapping = {
                    "int": "integer",
                    "bool": "boolean",
                    "float": "number",
                    "double": "number",
                    "str": "string",
                    "dict": "object",
                    "list": "array",
                    "DateTime": "string",
                    "datetime": "string",
                    "date": "string",
                    "Date": "string",
                }
                if type_val in type_mapping:
                    result["type"] = type_mapping[type_val]
                    if type_val in ["DateTime", "datetime"]:
                        result["format"] = "date-time"
                    elif type_val in ["date", "Date"]:
                        result["format"] = "date"

        return result
    elif isinstance(value, list):
        return [_sanitize_value_recursive(item) for item in value]
    else:
        return value


def sanitize_spec(spec: dict) -> dict:
    """Fix common OpenAPI spec issues by normalizing type names."""
    return _sanitize_value_recursive(spec)


# =============================================================================
# Type Generation
# =============================================================================


def python_type_from_schema(
    schema: dict[str, Any],
    components: dict,
    inline_schemas: dict[str, dict] | None = None,
    context_name: str | None = None,
) -> str:
    """Convert OpenAPI schema to Python type hint."""
    if not schema:
        return "Any"

    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        return to_pascal_case(ref_name)

    schema_type = schema.get("type", "object")

    if schema_type == "array":
        items = schema.get("items", {})
        item_context = f"{context_name}Item" if context_name else None
        item_type = python_type_from_schema(items, components, inline_schemas, item_context)
        return f"List[{item_type}]"
    elif schema_type == "object":
        properties = schema.get("properties", {})
        if properties and inline_schemas is not None and context_name:
            class_name = to_pascal_case(context_name)
            if class_name not in inline_schemas:
                inline_schemas[class_name] = schema
            return class_name
        else:
            return "Dict[str, Any]"
    elif schema_type == "integer":
        return "int"
    elif schema_type == "number":
        return "float"
    elif schema_type == "boolean":
        return "bool"
    elif schema_type == "string":
        return "str"
    else:
        return "Any"


# =============================================================================
# Model Generation
# =============================================================================


def generate_model(
    name: str,
    schema: dict[str, Any],
    components: dict,
    inline_schemas: dict[str, dict] | None = None,
) -> ModelInfo:
    """Generate a ModelInfo from an OpenAPI schema."""
    class_name = to_pascal_case(name)
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    model = ModelInfo(
        name=class_name,
        description=schema.get("description"),
        fields=[],
    )

    if not properties:
        return model

    # Separate required and optional fields
    required_fields = []
    optional_fields = []

    for prop_name, prop_schema in properties.items():
        py_name = sanitize_field_name(prop_name)

        nested_context = (
            f"{class_name}{to_pascal_case(prop_name)}" if inline_schemas is not None else None
        )
        py_type = python_type_from_schema(prop_schema, components, inline_schemas, nested_context)

        desc = prop_schema.get("description", "")

        field_info = FieldInfo(
            name=py_name,
            original_name=prop_name,
            type=py_type if prop_name in required else f"Optional[{py_type}]",
            default=None if prop_name in required else "None",
            comment=desc if desc else None,
        )

        if prop_name in required:
            required_fields.append(field_info)
        else:
            optional_fields.append(field_info)

    # Required fields first (no defaults), then optional (with defaults)
    model.fields = required_fields + optional_fields

    return model


def generate_method_name(path: str, method: str) -> str:
    """Generate a clean method name from path and HTTP method."""
    clean_path = re.sub(r"\{[^}]{1,256}\}", "", path)
    parts = [p for p in clean_path.split("/") if p]

    resource = to_snake_case(parts[-1] if parts else "resource")
    resource = re.sub(r"[^a-zA-Z0-9_]", "_", resource)
    resource = resource.strip("_")

    if method == "get":
        if "{" in path:
            action = "get"
        else:
            action = "list"
            resource = pluralize(resource)
    elif method == "post":
        action = "create"
    elif method == "put":
        action = "update"
    elif method == "patch":
        action = "patch"
    elif method == "delete":
        action = "delete"
    else:
        action = method

    return f"{action}_{resource}"


# =============================================================================
# SDK Generation
# =============================================================================


def extract_models_and_methods(
    spec: dict[str, Any],
    class_name: str,
) -> tuple[list[ModelInfo], list[MethodInfo]]:
    """Extract models and methods from an OpenAPI spec."""
    components = spec.get("components", {})
    paths = spec.get("paths", {})
    inline_schemas: dict[str, dict] = {}

    models: list[ModelInfo] = []
    methods: list[MethodInfo] = []
    method_names: set[str] = set()

    # First pass: collect inline schemas from responses
    for path, path_item in paths.items():
        for http_method in ["get", "post", "put", "patch", "delete"]:
            if http_method not in path_item:
                continue

            operation = path_item[http_method]
            responses = operation.get("responses", {})

            for status_code in ["200", "201", "204", "default"]:
                response = responses.get(status_code)
                if not response:
                    continue

                content = response.get("content", {})
                json_content = content.get("application/json", {})
                schema = json_content.get("schema", {})

                if schema:
                    operation_id = operation.get("operationId")
                    if operation_id:
                        context_name = f"{to_pascal_case(operation_id)}Response"
                    else:
                        method_name = generate_method_name(path, http_method)
                        context_name = f"{to_pascal_case(method_name)}Response"

                    python_type_from_schema(schema, components, inline_schemas, context_name)

    # Generate models from component schemas
    schemas = components.get("schemas", {})
    for schema_name, schema_def in sorted(schemas.items()):
        if schema_def.get("type") == "object":
            models.append(generate_model(schema_name, schema_def, components, inline_schemas))

    # Generate models from inline schemas
    generated_schemas: set[str] = set()
    while len(generated_schemas) < len(inline_schemas):
        for schema_name, schema_def in sorted(inline_schemas.items()):
            if schema_name not in generated_schemas:
                models.append(generate_model(schema_name, schema_def, components, inline_schemas))
                generated_schemas.add(schema_name)

    # Generate methods
    for path, path_item in sorted(paths.items()):
        for http_method in ["get", "post", "put", "patch", "delete"]:
            if http_method not in path_item:
                continue

            operation = path_item[http_method]

            # Generate unique method name
            base_method_name = generate_method_name(path, http_method)
            method_name = base_method_name
            counter = 1
            while method_name in method_names:
                method_name = f"{base_method_name}_{counter}"
                counter += 1
            method_names.add(method_name)

            # Build parameters
            path_params = re.findall(r"\{([^}]{1,256})\}", path)
            params = []
            for path_param in path_params:
                params.append(f"{to_snake_case(path_param)}: str")

            if http_method in ["post", "put", "patch"]:
                params.append("data: Dict[str, Any] = None")

            params.append("**kwargs")
            params_str = ", ".join(params)

            # Determine return type
            responses = operation.get("responses", {})
            success_response = (
                responses.get("200")
                or responses.get("201")
                or responses.get("204")
                or responses.get("default")
            )
            return_type = "Any"

            if success_response:
                content = success_response.get("content", {})
                json_content = content.get("application/json", {})
                schema = json_content.get("schema", {})

                operation_id = operation.get("operationId")
                if operation_id:
                    context_name = f"{to_pascal_case(operation_id)}Response"
                else:
                    context_name = f"{to_pascal_case(method_name)}Response"

                return_type = python_type_from_schema(
                    schema, components, inline_schemas, context_name
                )

            # Build URL template with snake_case params
            url_template = path
            for path_param in path_params:
                snake_param = to_snake_case(path_param)
                url_template = url_template.replace(f"{{{path_param}}}", f"{{{snake_param}}}")

            summary = operation.get("summary", f"{http_method.upper()} {path}")

            methods.append(
                MethodInfo(
                    name=method_name,
                    http_method=http_method,
                    url_template=url_template,
                    params=params_str,
                    return_type=return_type,
                    summary=summary,
                )
            )

    return models, methods


def generate_sdk(
    spec: dict[str, Any],
    integration_name: str,
    auth_type: AuthType,
    module_name: str | None = None,
) -> tuple[str, str]:
    """
    Generate a complete SDK module from an OpenAPI spec.

    Args:
        spec: Parsed OpenAPI specification
        integration_name: Name of the Bifrost integration
        auth_type: Authentication type (bearer, api_key, basic, oauth)
        module_name: Optional module name (defaults to sanitized spec title)

    Returns:
        Tuple of (generated_code, suggested_module_name)
    """
    spec = sanitize_spec(spec)

    title = spec.get("info", {}).get("title", "API")
    class_name = to_pascal_case(title) or "APIClient"

    if not module_name:
        pythonic_name = re.sub(r"[^a-zA-Z0-9]+", "_", title).lower().strip("_")
        module_name = pythonic_name

    # Extract models and methods from spec
    models, methods = extract_models_and_methods(spec, class_name)

    # Load and render a Python source template. The sandbox keeps template
    # execution constrained while preserving source-code rendering semantics.
    env = SandboxedEnvironment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("sdk.py.j2")

    code = template.render(
        title=title,
        class_name=class_name,
        integration_name=integration_name,
        auth_type=auth_type,
        models=models,
        methods=methods,
    )

    return code, module_name


# =============================================================================
# Public API
# =============================================================================


class SDKGeneratorResult:
    """Result of SDK generation."""

    def __init__(
        self,
        code: str,
        module_name: str,
        class_name: str,
        endpoint_count: int,
        schema_count: int,
    ):
        self.code = code
        self.module_name = module_name
        self.class_name = class_name
        self.endpoint_count = endpoint_count
        self.schema_count = schema_count


def generate_sdk_from_url(
    spec_url: str,
    integration_name: str,
    auth_type: AuthType,
    module_name: str | None = None,
) -> SDKGeneratorResult:
    """
    Generate SDK from an OpenAPI spec URL.

    Args:
        spec_url: URL to OpenAPI spec (JSON or YAML)
        integration_name: Name of the Bifrost integration
        auth_type: Authentication type
        module_name: Optional module name

    Returns:
        SDKGeneratorResult with generated code and metadata
    """
    logger.info(f"Loading OpenAPI spec from {log_safe(spec_url)}")
    spec = load_spec_from_url(spec_url)

    return _generate_sdk_result(spec, integration_name, auth_type, module_name)


def generate_sdk_from_content(
    content: str,
    content_type: str,
    integration_name: str,
    auth_type: AuthType,
    module_name: str | None = None,
) -> SDKGeneratorResult:
    """
    Generate SDK from OpenAPI spec content.

    Args:
        content: Raw OpenAPI spec content
        content_type: "json" or "yaml"
        integration_name: Name of the Bifrost integration
        auth_type: Authentication type
        module_name: Optional module name

    Returns:
        SDKGeneratorResult with generated code and metadata
    """
    logger.info("Parsing OpenAPI spec from provided content")
    spec = load_spec_from_content(content, content_type)

    return _generate_sdk_result(spec, integration_name, auth_type, module_name)


def _generate_sdk_result(
    spec: dict,
    integration_name: str,
    auth_type: AuthType,
    module_name: str | None,
) -> SDKGeneratorResult:
    """Internal helper to generate SDK and build result."""
    code, final_module_name = generate_sdk(spec, integration_name, auth_type, module_name)

    title = spec.get("info", {}).get("title", "API")
    class_name = sanitize_class_name(to_pascal_case(title))

    paths = spec.get("paths", {})
    endpoint_count = sum(
        len([m for m in ["get", "post", "put", "patch", "delete"] if m in item])
        for item in paths.values()
    )
    schema_count = len(spec.get("components", {}).get("schemas", {}))

    logger.info(
        f"Generated SDK: {log_safe(final_module_name)}.py with {endpoint_count} endpoints, {schema_count} schemas"
    )

    return SDKGeneratorResult(
        code=code,
        module_name=final_module_name,
        class_name=class_name,
        endpoint_count=endpoint_count,
        schema_count=schema_count,
    )
