import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
assert sys.version_info >= (3, 12)  # nosec

EXTENDED_ARG = dis.EXTENDED_ARG
IMPORT_NAME = dis.opmap["IMPORT_NAME"]
IMPORT_FROM = dis.opmap["IMPORT_FROM"]
RESUME = dis.opmap["RESUME"]
RETURN_CONST = dis.opmap["RETURN_CONST"]
EMPTY_MODULE_BYTES = bytes([RESUME, 0, RETURN_CONST, 0])

_CODE_HOOKS: t.Dict[CodeType, t.Tuple[HookType, str, t.Dict[int, t.Tuple[str, t.Optional[t.Tuple[str]]]]]] = {}


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    coverage_tool = sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID)
    if coverage_tool is not None and coverage_tool != "datadog":
        log.debug("Coverage tool '%s' already registered, not gathering coverage", coverage_tool)
        return code, CoverageLines()

    if coverage_tool is None:
        log.debug("Registering code coverage tool")
        _register_monitoring()

    return _instrument_all_lines_with_monitoring(code, hook, path, package)


def _line_event_handler(code: CodeType, line: int) -> t.Any:
    hook, path, import_names = _CODE_HOOKS[code]
    import_name = import_names.get(line, None)
    return hook((line, path, import_name))


def _register_monitoring():
    """
    Register the coverage tool with the low-impact monitoring system.
    """
    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "datadog")

    # Register the line callback
    sys.monitoring.register_callback(
        sys.monitoring.COVERAGE_ID, sys.monitoring.events.LINE, _line_event_handler
    )  # noqa


def _instrument_all_lines_with_monitoring(
    code: CodeType, hook: HookType, path: str, package: str
) -> t.Tuple[CodeType, CoverageLines]:
    # Enable local line events for the code object
    sys.monitoring.set_local_events(sys.monitoring.COVERAGE_ID, code, sys.monitoring.events.LINE)  # noqa

    # Collect all the line numbers in the code object
    linestarts = dict(dis.findlinestarts(code))

    lines = CoverageLines()
    import_names: t.Dict[int, t.Tuple[str, t.Optional[t.Tuple[str, ...]]]] = {}

    # The previous two arguments are kept in order to track the depth of the IMPORT_NAME
    # For example, from ...package import module
    current_arg: int = 0
    previous_arg: int = 0
    _previous_previous_arg: int = 0
    current_import_name: t.Optional[str] = None
    current_import_package: t.Optional[str] = None

    # Precompute package split if needed
    _package_split = package.split(".") if package is not None else None

    line = 0

    # Improve perf: use local variables for speed in tight loop
    # (Keep code style and names per policy)
    ext: list[bytes] = []
    co_code = code.co_code
    co_names = code.co_names
    co_consts = code.co_consts
    co_name = code.co_name

    # Loop over code.co_code two bytes at a time for opcode/arg
    code_len = len(co_code)
    offset = 0
    while offset < code_len:
        opcode = co_code[offset]
        arg = co_code[offset + 1]
        this_offset = offset
        offset += 2

        if opcode == RESUME:
            continue

        if this_offset in linestarts:
            line = linestarts[this_offset]
            lines.add(line)

            # Make sure that the current module is marked as depending on its own package by instrumenting the
            # first executable line
            if co_name == "<module>" and len(lines) == 1 and package is not None:
                import_names[line] = (package, ("",))

        if opcode is EXTENDED_ARG:
            ext.append(arg)
            continue
        else:
            _previous_previous_arg = previous_arg
            previous_arg = current_arg
            if ext:
                # Fast-path without allocation for common case (single EXTENDED_ARG)
                ext_bytes = bytes(ext) + bytes([arg])
                current_arg = int.from_bytes(ext_bytes, "big", signed=False)
                ext.clear()
            else:
                current_arg = arg

        if opcode == IMPORT_NAME:
            import_depth: int = co_consts[_previous_previous_arg]
            current_import_name: str = co_names[current_arg]
            # Adjust package name if the import is relative and a parent (ie: if depth is more than 1)
            if import_depth > 1:
                # It is much faster to slice precomputed list than split each time.
                current_import_package = ".".join(_package_split[: -import_depth + 1])
            else:
                current_import_package = package

            if line in import_names:
                import_names[line] = (
                    current_import_package,
                    tuple(list(import_names[line][1]) + [current_import_name]),
                )
            else:
                import_names[line] = (current_import_package, (current_import_name,))

        if opcode == IMPORT_FROM:
            import_from_name = f"{current_import_name}.{co_names[current_arg]}"
            if line in import_names:
                import_names[line] = (
                    current_import_package,
                    tuple(list(import_names[line][1]) + [import_from_name]),
                )
            else:
                import_names[line] = (current_import_package, (import_from_name,))

    # Recursively instrument nested code objects
    # Reduce overhead by using list comprehension and updating lines in one pass
    nested_code_objs = [c for c in co_consts if isinstance(c, CodeType)]
    for nested_code in nested_code_objs:
        _, nested_lines = instrument_all_lines(nested_code, hook, path, package)
        lines.update(nested_lines)

    # Register the hook and argument for the code object
    _CODE_HOOKS[code] = (hook, path, import_names)

    # Special case for empty modules (eg: __init__.py ):
    # Make sure line 0 is marked as executable, and add package dependency
    if not lines and co_name == "<module>" and co_code == EMPTY_MODULE_BYTES:
        lines.add(0)
        if package is not None:
            import_names[0] = (package, ("",))

    return code, lines
