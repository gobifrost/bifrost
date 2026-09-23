"""
Decorator Property Service

Reads and writes decorator properties in Python source files using LibCST.
Preserves formatting, comments, and structure when modifying code.

Primary use case: Auto-inject stable UUIDs into @workflow, @data_provider,
@tool, and @service decorators on first discovery, with extensibility for
future property editing.
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

import libcst as cst
from libcst.helpers import get_full_name_for_node

logger = logging.getLogger(__name__)

DecoratorType = Literal["workflow", "data_provider", "tool", "service"]


@dataclass
class DecoratorInfo:
    """Information about a discovered decorator."""

    decorator_type: DecoratorType
    function_name: str
    line_number: int
    properties: dict[str, Any]
    has_parentheses: bool  # @workflow vs @workflow(...)


@dataclass
class PropertyWriteResult:
    """Result of a property write operation."""

    modified: bool
    new_content: str
    changes: list[str]  # Human-readable list of changes made


class DecoratorPropertyTransformer(cst.CSTTransformer):
    """
    LibCST transformer that modifies decorator properties.

    Handles three decorator syntax cases:
    1. @workflow -> @workflow(id="uuid")
    2. @workflow(name="X") -> @workflow(id="uuid", name="X")
    3. @workflow(id="existing", name="X") -> no change (already has id)
    """

    SUPPORTED_DECORATORS = {"workflow", "data_provider", "tool", "service"}

    def __init__(
        self,
        target_function: str | None = None,
        properties_to_set: dict[str, Any] | None = None,
        inject_id_if_missing: bool = False,
    ):
        super().__init__()
        self.target_function = target_function
        self.properties_to_set = properties_to_set or {}
        self.inject_id_if_missing = inject_id_if_missing
        self.changes_made: list[str] = []
        self._current_function: str | None = None

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        """Track current function name for targeting."""
        self._current_function = node.name.value
        return True

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        """Clear current function tracking."""
        self._current_function = None
        return updated_node

    def leave_Decorator(
        self, original_node: cst.Decorator, updated_node: cst.Decorator
    ) -> cst.Decorator:
        """Transform decorator if it matches our criteria."""
        # Skip if targeting specific function and this isn't it
        if self.target_function and self._current_function != self.target_function:
            return updated_node

        decorator = updated_node.decorator
        decorator_name = self._get_decorator_name(decorator)

        if decorator_name not in self.SUPPORTED_DECORATORS:
            return updated_node

        # Handle @workflow (no parentheses) - convert to @workflow(...) if needed
        if isinstance(decorator, cst.Name):
            # Need to convert bare decorator to call if we have properties to set or ID injection
            if self.inject_id_if_missing or self.properties_to_set:
                # Build args list from properties_to_set
                args = []

                # Add id first if injecting or explicitly setting
                if self.inject_id_if_missing or "id" in self.properties_to_set:
                    new_id = self.properties_to_set.get("id") or str(uuid4())
                    args.append(
                        cst.Arg(
                            keyword=cst.Name("id"),
                            value=cst.SimpleString(f'"{new_id}"'),
                        )
                    )
                    self.changes_made.append(
                        f"Added id='{new_id}' to @{decorator_name} on {self._current_function}"
                    )

                # Add other properties
                for key, value in self.properties_to_set.items():
                    if key == "id":
                        continue  # Already handled above
                    args.append(self._create_arg(key, value))
                    self.changes_made.append(
                        f"Added {key}='{value}' to @{decorator_name} on {self._current_function}"
                    )

                # Fix commas between args
                args = self._fix_trailing_commas(args)

                new_decorator = cst.Call(func=cst.Name(decorator_name), args=args)
                return updated_node.with_changes(decorator=new_decorator)

        # Handle @workflow(...) - add/update properties
        elif isinstance(decorator, cst.Call):
            return self._modify_call_decorator(updated_node, decorator, decorator_name)

        return updated_node

    def _get_decorator_name(self, decorator: cst.BaseExpression) -> str | None:
        """Extract decorator name from various node types."""
        name = get_full_name_for_node(decorator)
        if name:
            # Handle module.workflow -> workflow
            return name.split(".")[-1]
        return None

    def _create_call_with_id(self, decorator_name: str, id_value: str) -> cst.Call:
        """Create @decorator(id="...") from bare @decorator."""
        return cst.Call(
            func=cst.Name(decorator_name),
            args=[
                cst.Arg(
                    keyword=cst.Name("id"),
                    value=cst.SimpleString(f'"{id_value}"'),
                )
            ],
        )

    def _modify_call_decorator(
        self,
        decorator_node: cst.Decorator,
        call: cst.Call,
        decorator_name: str,
    ) -> cst.Decorator:
        """Modify an existing @decorator(...) call."""
        existing_kwargs = self._extract_kwargs(call)

        # Determine what needs to change
        new_args = list(call.args)
        modified = False

        # Handle ID injection
        if self.inject_id_if_missing and "id" not in existing_kwargs:
            new_id = str(uuid4())
            # Insert id as first argument for consistency
            id_arg = cst.Arg(
                keyword=cst.Name("id"),
                value=cst.SimpleString(f'"{new_id}"'),
                comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
                if new_args
                else cst.MaybeSentinel.DEFAULT,
            )
            new_args.insert(0, id_arg)
            self.changes_made.append(
                f"Added id='{new_id}' to @{decorator_name} on {self._current_function}"
            )
            modified = True

        # Handle explicit property updates
        for key, value in self.properties_to_set.items():
            if key in existing_kwargs:
                # Update existing arg
                new_args = self._update_arg(new_args, key, value)
                self.changes_made.append(
                    f"Updated {key}='{value}' on @{decorator_name} on {self._current_function}"
                )
                modified = True
            else:
                # Add new arg
                new_arg = self._create_arg(key, value)
                new_args.append(new_arg)
                self.changes_made.append(
                    f"Added {key}='{value}' to @{decorator_name} on {self._current_function}"
                )
                modified = True

        if not modified:
            return decorator_node

        # Fix trailing commas
        new_args = self._fix_trailing_commas(new_args)

        new_call = call.with_changes(args=new_args)
        return decorator_node.with_changes(decorator=new_call)

    def _extract_kwargs(self, call: cst.Call) -> dict[str, cst.Arg]:
        """Extract keyword arguments from a Call node."""
        kwargs = {}
        for arg in call.args:
            if arg.keyword:
                kwargs[arg.keyword.value] = arg
        return kwargs

    def _update_arg(self, args: list[cst.Arg], key: str, value: Any) -> list[cst.Arg]:
        """Update an existing argument's value."""
        result = []
        for arg in args:
            if arg.keyword and arg.keyword.value == key:
                new_value = self._python_value_to_cst(value)
                result.append(arg.with_changes(value=new_value))
            else:
                result.append(arg)
        return result

    def _create_arg(self, key: str, value: Any) -> cst.Arg:
        """Create a new keyword argument."""
        return cst.Arg(
            keyword=cst.Name(key),
            value=self._python_value_to_cst(value),
        )

    def _python_value_to_cst(self, value: Any) -> cst.BaseExpression:
        """Convert Python value to CST expression."""
        if isinstance(value, str):
            return cst.SimpleString(f'"{value}"')
        elif isinstance(value, bool):
            return cst.Name("True" if value else "False")
        elif isinstance(value, int):
            return cst.Integer(str(value))
        elif isinstance(value, float):
            return cst.Float(str(value))
        elif isinstance(value, list):
            elements = [cst.Element(self._python_value_to_cst(v)) for v in value]
            return cst.List(elements)
        elif value is None:
            return cst.Name("None")
        else:
            # Fallback to string representation
            return cst.SimpleString(f'"{value}"')

    def _fix_trailing_commas(self, args: list[cst.Arg]) -> list[cst.Arg]:
        """Ensure proper comma handling for arg list."""
        if not args:
            return args

        result = []
        for i, arg in enumerate(args):
            is_last = i == len(args) - 1
            if is_last:
                # Remove trailing comma from last arg
                result.append(arg.with_changes(comma=cst.MaybeSentinel.DEFAULT))
            else:
                # Ensure comma after non-last args
                if not isinstance(arg.comma, cst.Comma):
                    result.append(
                        arg.with_changes(
                            comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
                        )
                    )
                else:
                    result.append(arg)
        return result


class IdStripperTransformer(cst.CSTTransformer):
    """
    LibCST transformer that removes 'id' arguments from decorators.

    Used during git push to serialize clean Python code without DB IDs.
    IDs are DB-only and should not be serialized to files for git.
    """

    SUPPORTED_DECORATORS = {"workflow", "data_provider", "tool", "service"}

    def __init__(self) -> None:
        super().__init__()
        self.changes_made: list[str] = []
        self._current_function: str | None = None

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._current_function = node.name.value
        return True

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        self._current_function = None
        return updated_node

    def leave_Decorator(
        self, original_node: cst.Decorator, updated_node: cst.Decorator
    ) -> cst.Decorator:
        decorator = updated_node.decorator

        # Only process Call decorators (ones with parentheses)
        if not isinstance(decorator, cst.Call):
            return updated_node

        decorator_name = self._get_decorator_name(decorator)
        if decorator_name not in self.SUPPORTED_DECORATORS:
            return updated_node

        # Filter out 'id' argument
        new_args = [arg for arg in decorator.args if not (arg.keyword and arg.keyword.value == "id")]

        if len(new_args) == len(decorator.args):
            # No 'id' argument found, no change needed
            return updated_node

        self.changes_made.append(
            f"Removed id from @{decorator_name} on {self._current_function}"
        )

        # If no args left, convert to bare decorator
        if not new_args:
            return updated_node.with_changes(decorator=cst.Name(decorator_name))

        # Fix trailing commas
        new_args = self._fix_trailing_commas(new_args)
        new_call = decorator.with_changes(args=new_args)
        return updated_node.with_changes(decorator=new_call)

    def _get_decorator_name(self, decorator: cst.BaseExpression) -> str | None:
        name = get_full_name_for_node(decorator)
        if name:
            return name.split(".")[-1]
        return None

    def _fix_trailing_commas(self, args: list[cst.Arg]) -> list[cst.Arg]:
        """Ensure proper comma handling for arg list."""
        if not args:
            return args

        result = []
        for i, arg in enumerate(args):
            is_last = i == len(args) - 1
            if is_last:
                result.append(arg.with_changes(comma=cst.MaybeSentinel.DEFAULT))
            else:
                if not isinstance(arg.comma, cst.Comma):
                    result.append(
                        arg.with_changes(
                            comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
                        )
                    )
                else:
                    result.append(arg)
        return result


class SpecificIdTransformer(cst.CSTTransformer):
    """
    LibCST transformer that injects specific IDs into decorators.

    Unlike DecoratorPropertyTransformer which generates new UUIDs,
    this transformer injects pre-specified IDs for specific functions.
    Used when user wants to reuse existing workflow IDs.
    """

    SUPPORTED_DECORATORS = {"workflow", "data_provider", "tool", "service"}

    def __init__(self, function_ids: dict[str, str]):
        """
        Args:
            function_ids: Map of function_name -> ID to inject
        """
        super().__init__()
        self.function_ids = function_ids
        self.changes_made: list[str] = []
        self._current_function: str | None = None

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._current_function = node.name.value
        return True

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        self._current_function = None
        return updated_node

    def leave_Decorator(
        self, original_node: cst.Decorator, updated_node: cst.Decorator
    ) -> cst.Decorator:
        # Skip if this function doesn't have a specific ID to inject
        if self._current_function not in self.function_ids:
            return updated_node

        decorator = updated_node.decorator
        decorator_name = self._get_decorator_name(decorator)

        if decorator_name not in self.SUPPORTED_DECORATORS:
            return updated_node

        target_id = self.function_ids[self._current_function]

        # Handle @workflow (no parentheses)
        if isinstance(decorator, cst.Name):
            new_decorator = cst.Call(
                func=cst.Name(decorator_name),
                args=[
                    cst.Arg(
                        keyword=cst.Name("id"),
                        value=cst.SimpleString(f'"{target_id}"'),
                    )
                ],
            )
            self.changes_made.append(
                f"Injected id='{target_id}' into @{decorator_name} on {self._current_function}"
            )
            return updated_node.with_changes(decorator=new_decorator)

        # Handle @workflow(...) - add id if not present
        elif isinstance(decorator, cst.Call):
            existing_kwargs = {}
            for arg in decorator.args:
                if arg.keyword:
                    existing_kwargs[arg.keyword.value] = arg

            if "id" not in existing_kwargs:
                # Insert id as first argument
                new_args = list(decorator.args)
                id_arg = cst.Arg(
                    keyword=cst.Name("id"),
                    value=cst.SimpleString(f'"{target_id}"'),
                    comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
                    if new_args
                    else cst.MaybeSentinel.DEFAULT,
                )
                new_args.insert(0, id_arg)
                new_call = decorator.with_changes(args=new_args)
                self.changes_made.append(
                    f"Injected id='{target_id}' into @{decorator_name} on {self._current_function}"
                )
                return updated_node.with_changes(decorator=new_call)

        return updated_node

    def _get_decorator_name(self, decorator: cst.BaseExpression) -> str | None:
        name = get_full_name_for_node(decorator)
        if name:
            return name.split(".")[-1]
        return None


class DecoratorPropertyReader(cst.CSTVisitor):
    """
    LibCST visitor that reads decorator properties without modification.
    """

    SUPPORTED_DECORATORS = {"workflow", "data_provider", "tool", "service"}

    def __init__(self) -> None:
        self.decorators: list[DecoratorInfo] = []
        self._current_function: str | None = None

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._current_function = node.name.value
        return True

    def leave_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._current_function = None

    def visit_Decorator(self, node: cst.Decorator) -> bool:
        decorator = node.decorator
        decorator_name = self._get_decorator_name(decorator)

        if decorator_name not in self.SUPPORTED_DECORATORS:
            return False

        properties: dict[str, Any] = {}
        has_parentheses = False

        if isinstance(decorator, cst.Call):
            has_parentheses = True
            for arg in decorator.args:
                if arg.keyword:
                    key = arg.keyword.value
                    value = self._cst_to_python_value(arg.value)
                    properties[key] = value

        self.decorators.append(
            DecoratorInfo(
                decorator_type=decorator_name,  # type: ignore[arg-type]
                function_name=self._current_function or "",
                line_number=0,  # Position info not available without metadata wrapper
                properties=properties,
                has_parentheses=has_parentheses,
            )
        )

        return False

    def _get_decorator_name(self, decorator: cst.BaseExpression) -> str | None:
        name = get_full_name_for_node(decorator)
        if name:
            return name.split(".")[-1]
        return None

    def _cst_to_python_value(self, node: cst.BaseExpression) -> Any:
        """Convert CST expression to Python value."""
        if isinstance(node, cst.SimpleString):
            # Remove quotes - handle single, double, and triple quotes
            s = node.value
            if s.startswith('"""') or s.startswith("'''"):
                return s[3:-3]
            elif s.startswith('"') or s.startswith("'"):
                return s[1:-1]
            return s
        elif isinstance(node, cst.ConcatenatedString):
            # Handle multi-line strings
            parts = []
            for part in [node.left, node.right]:
                parts.append(str(self._cst_to_python_value(part)))
            return "".join(parts)
        elif isinstance(node, cst.Integer):
            return int(node.value)
        elif isinstance(node, cst.Float):
            return float(node.value)
        elif isinstance(node, cst.Name):
            if node.value == "True":
                return True
            elif node.value == "False":
                return False
            elif node.value == "None":
                return None
            return node.value
        elif isinstance(node, cst.List):
            return [self._cst_to_python_value(el.value) for el in node.elements]
        elif isinstance(node, cst.Dict):
            result = {}
            for el in node.elements:
                if isinstance(el, cst.DictElement):
                    key = self._cst_to_python_value(el.key)
                    value = self._cst_to_python_value(el.value)
                    result[key] = value
            return result
        return None


class DecoratorPropertyService:
    """
    Service for reading and writing decorator properties in Python files.

    Uses LibCST to parse and modify Python source while preserving
    all formatting, comments, and whitespace.
    """

    def read_decorators(self, content: str) -> list[DecoratorInfo]:
        """
        Read all supported decorators from Python source.

        Args:
            content: Python source code as string

        Returns:
            List of DecoratorInfo for each @workflow, @data_provider, @tool
        """
        try:
            module = cst.parse_module(content)
            reader = DecoratorPropertyReader()
            # Visit the module tree using the visitor - LibCST uses visit(), not walk()
            module.visit(reader)
            return reader.decorators
        except cst.ParserSyntaxError as e:
            logger.warning(f"Failed to parse Python source: {e}")
            return []

    def read_properties(
        self,
        content: str,
        function_name: str,
        decorator_type: DecoratorType = "workflow",
    ) -> dict[str, Any] | None:
        """
        Read properties from a specific decorator.

        Args:
            content: Python source code
            function_name: Target function name
            decorator_type: Type of decorator to read

        Returns:
            Dict of properties or None if not found
        """
        decorators = self.read_decorators(content)
        for dec in decorators:
            if (
                dec.function_name == function_name
                and dec.decorator_type == decorator_type
            ):
                return dec.properties
        return None

    def write_properties(
        self,
        content: str,
        function_name: str,
        properties: dict[str, Any],
    ) -> PropertyWriteResult:
        """
        Write/update properties on a decorator.

        Args:
            content: Python source code
            function_name: Target function name
            properties: Properties to set/update

        Returns:
            PropertyWriteResult with modified content and change list
        """
        try:
            module = cst.parse_module(content)
            transformer = DecoratorPropertyTransformer(
                target_function=function_name,
                properties_to_set=properties,
            )
            new_module = module.visit(transformer)

            return PropertyWriteResult(
                modified=bool(transformer.changes_made),
                new_content=new_module.code,
                changes=transformer.changes_made,
            )
        except cst.ParserSyntaxError as e:
            logger.error(f"Failed to parse Python source: {e}")
            return PropertyWriteResult(
                modified=False,
                new_content=content,
                changes=[f"Parse error: {e}"],
            )

    def inject_specific_ids(
        self, content: str, function_ids: dict[str, str]
    ) -> PropertyWriteResult:
        """
        Inject specific IDs into decorators for specified functions.

        This is used when the user chooses to reuse existing workflow IDs
        instead of generating new ones.

        Args:
            content: Python source code
            function_ids: Map of function_name -> ID to inject

        Returns:
            PropertyWriteResult with modified content
        """
        try:
            module = cst.parse_module(content)
            # Use transformer to inject specific IDs
            transformer = SpecificIdTransformer(function_ids)
            new_module = module.visit(transformer)

            return PropertyWriteResult(
                modified=bool(transformer.changes_made),
                new_content=new_module.code,
                changes=transformer.changes_made,
            )
        except cst.ParserSyntaxError as e:
            logger.error(f"Failed to parse Python source: {e}")
            return PropertyWriteResult(
                modified=False,
                new_content=content,
                changes=[f"Parse error: {e}"],
            )

    def strip_ids(self, content: str) -> PropertyWriteResult:
        """
        Remove 'id' arguments from all supported decorators.

        Used during git push to serialize clean Python code without DB IDs.
        IDs are DB-only and should not be serialized to files for git.

        Args:
            content: Python source code

        Returns:
            PropertyWriteResult with IDs stripped from decorators
        """
        try:
            module = cst.parse_module(content)
            transformer = IdStripperTransformer()
            new_module = module.visit(transformer)

            return PropertyWriteResult(
                modified=bool(transformer.changes_made),
                new_content=new_module.code,
                changes=transformer.changes_made,
            )
        except cst.ParserSyntaxError as e:
            logger.error(f"Failed to parse Python source: {e}")
            return PropertyWriteResult(
                modified=False,
                new_content=content,
                changes=[f"Parse error: {e}"],
            )
