"""
AST parsing utilities for extracting metadata from Python workflow files.

Provides methods for parsing decorators, function signatures, and type annotations
to extract parameter metadata for workflows, tools, and data providers.
"""

import ast
import re
from typing import Any


class ASTMetadataParser:
    """Parse AST nodes to extract metadata from workflow/tool/provider decorators."""

    def parse_decorator(self, decorator: ast.AST) -> tuple[str, dict[str, Any]] | None:
        """
        Parse a decorator AST node to extract name and keyword arguments.

        Args:
            decorator: AST node representing the decorator

        Returns:
            Tuple of (decorator_name, kwargs_dict) or None if not a workflow/provider decorator
        """
        # Handle @workflow (no parentheses)
        if isinstance(decorator, ast.Name):
            if decorator.id in ("workflow", "tool", "data_provider", "service"):
                return decorator.id, {}
            return None

        # Handle @workflow(...) (with parentheses)
        if isinstance(decorator, ast.Call):
            if isinstance(decorator.func, ast.Name):
                decorator_name = decorator.func.id
            elif isinstance(decorator.func, ast.Attribute):
                # Handle module.workflow (e.g., bifrost.workflow)
                decorator_name = decorator.func.attr
            else:
                return None

            if decorator_name not in ("workflow", "tool", "data_provider", "service"):
                return None

            # Extract keyword arguments
            kwargs = {}
            for keyword in decorator.keywords:
                if keyword.arg:
                    value = self.ast_value_to_python(keyword.value)
                    if value is not None:
                        kwargs[keyword.arg] = value

            return decorator_name, kwargs

        return None

    def ast_value_to_python(self, node: ast.AST) -> Any:
        """
        Convert an AST node to a Python value.

        Args:
            node: AST node to convert

        Returns:
            Python value or None if conversion not possible
        """
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.List):
            return [self.ast_value_to_python(elt) for elt in node.elts]
        elif isinstance(node, ast.Dict):
            return {
                self.ast_value_to_python(k): self.ast_value_to_python(v)
                for k, v in zip(node.keys, node.values)
                if k is not None
            }
        elif isinstance(node, ast.Name):
            # Handle True, False, None
            if node.id == "True":
                return True
            elif node.id == "False":
                return False
            elif node.id == "None":
                return None
        return None

    def extract_parameters_from_ast(
        self, func_node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> list[dict[str, Any]]:
        """
        Extract parameter metadata from function definition AST.

        Args:
            func_node: Function definition AST node

        Returns:
            List of parameter dicts with: name, type, required, label, default_value
        """
        parameters: list[dict[str, Any]] = []
        args = func_node.args

        # Get defaults - they align with the end of the args list
        defaults = args.defaults
        num_defaults = len(defaults)
        num_args = len(args.args)

        for i, arg in enumerate(args.args):
            param_name = arg.arg

            # Skip 'self', 'cls', and context parameters
            if param_name in ("self", "cls", "context"):
                continue

            # Skip ExecutionContext parameter (by annotation)
            if arg.annotation:
                annotation_str = self.annotation_to_string(arg.annotation)
                if "ExecutionContext" in annotation_str:
                    continue

            # Determine if parameter has a default
            default_index = i - (num_args - num_defaults)
            has_default = default_index >= 0

            # Get default value
            default_value = None
            if has_default:
                default_node = defaults[default_index]
                default_value = self.ast_value_to_python(default_node)

            # Determine type from annotation
            ui_type = "string"
            is_optional = has_default
            options = None
            if arg.annotation:
                ui_type = self.annotation_to_ui_type(arg.annotation)
                is_optional = is_optional or self.is_optional_annotation(arg.annotation)
                options = self.extract_literal_options(arg.annotation)

            # Generate label from parameter name
            label = re.sub(r"([a-z])([A-Z])", r"\1 \2", param_name.replace("_", " ")).title()

            param_meta = {
                "name": param_name,
                "type": ui_type,
                "required": not is_optional,
                "label": label,
            }

            if default_value is not None:
                param_meta["default_value"] = default_value

            if options:
                param_meta["options"] = options

            parameters.append(param_meta)

        return parameters

    def annotation_to_string(self, annotation: ast.AST) -> str:
        """
        Convert annotation AST to string representation.

        Args:
            annotation: Annotation AST node

        Returns:
            String representation of the annotation
        """
        if isinstance(annotation, ast.Name):
            return annotation.id
        elif isinstance(annotation, ast.Constant):
            return str(annotation.value)
        elif isinstance(annotation, ast.Subscript):
            return f"{self.annotation_to_string(annotation.value)}[...]"
        elif isinstance(annotation, ast.Attribute):
            return f"{self.annotation_to_string(annotation.value)}.{annotation.attr}"
        elif isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            # Python 3.10+ union syntax: str | None
            left = self.annotation_to_string(annotation.left)
            right = self.annotation_to_string(annotation.right)
            return f"{left} | {right}"
        return ""

    def annotation_to_ui_type(self, annotation: ast.AST) -> str:
        """
        Convert annotation AST to UI type string.

        Args:
            annotation: Annotation AST node

        Returns:
            UI type string (string, int, float, bool, list, json)
        """
        type_mapping = {
            "str": "string",
            "int": "int",
            "float": "float",
            "bool": "bool",
            "list": "list",
            "dict": "json",
        }

        if isinstance(annotation, ast.Name):
            return type_mapping.get(annotation.id, "json")

        elif isinstance(annotation, ast.Subscript):
            # Handle list[str], dict[str, Any], Literal[...], etc.
            if isinstance(annotation.value, ast.Name):
                base_type = annotation.value.id
                if base_type == "list":
                    return "list"
                elif base_type == "dict":
                    return "json"
                elif base_type == "Optional":
                    # Optional[str] -> string
                    if isinstance(annotation.slice, ast.Name):
                        return type_mapping.get(annotation.slice.id, "string")
                    return "string"
                elif base_type == "Literal":
                    # Literal["a", "b"] -> infer type from values
                    return self.infer_literal_type(annotation.slice)

        elif isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            # str | None -> string
            left_type = self.annotation_to_ui_type(annotation.left)
            return left_type

        return "json"

    def infer_literal_type(self, slice_node: ast.AST) -> str:
        """
        Infer UI type from Literal values.

        Args:
            slice_node: The slice node from a Literal subscript

        Returns:
            Inferred UI type string
        """
        # Get the first value from the Literal
        if isinstance(slice_node, ast.Tuple):
            # Literal["a", "b"] - multiple values
            if slice_node.elts:
                first_val = self.ast_value_to_python(slice_node.elts[0])
            else:
                return "string"
        else:
            # Literal["a"] - single value
            first_val = self.ast_value_to_python(slice_node)

        if first_val is None:
            return "string"
        if isinstance(first_val, str):
            return "string"
        if isinstance(first_val, bool):
            return "bool"
        if isinstance(first_val, int):
            return "int"
        if isinstance(first_val, float):
            return "float"
        return "string"

    def extract_literal_options(self, annotation: ast.AST) -> list[dict[str, str]] | None:
        """
        Extract options from Literal type annotation.

        Args:
            annotation: Annotation AST node

        Returns:
            List of {label, value} dicts or None if not a Literal
        """
        if not isinstance(annotation, ast.Subscript):
            return None
        if not isinstance(annotation.value, ast.Name):
            return None
        if annotation.value.id != "Literal":
            return None

        # Get values from the Literal
        slice_node = annotation.slice
        values = []

        if isinstance(slice_node, ast.Tuple):
            # Literal["a", "b"] - multiple values
            for elt in slice_node.elts:
                val = self.ast_value_to_python(elt)
                if val is not None:
                    values.append({"label": str(val), "value": str(val)})
        else:
            # Literal["a"] - single value
            val = self.ast_value_to_python(slice_node)
            if val is not None:
                values.append({"label": str(val), "value": str(val)})

        return values if values else None

    def is_optional_annotation(self, annotation: ast.AST) -> bool:
        """
        Check if annotation represents an optional type.

        Args:
            annotation: Annotation AST node

        Returns:
            True if the annotation is Optional or Union with None
        """
        if isinstance(annotation, ast.Subscript):
            if isinstance(annotation.value, ast.Name):
                if annotation.value.id == "Optional":
                    return True

        elif isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            # Check for str | None pattern
            right_str = self.annotation_to_string(annotation.right)
            left_str = self.annotation_to_string(annotation.left)
            if right_str == "None" or left_str == "None":
                return True

        return False
