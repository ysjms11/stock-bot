"""Guard against the refactor-regression class: a name that used to be a
module-global but, after a package split, is defined in a *different* module
and not re-imported. `import module` still succeeds, so import-only tests miss
it — the NameError only fires when the function runs (often swallowed by a
surrounding try/except, hiding it from logs).

Method (ground truth for the NameError class): for every function/lambda/
comprehension code object, read the bytecode LOAD_GLOBAL names — exactly what
Python resolves against module globals at runtime — and assert each resolves in
the module's real __dict__ or builtins. LOAD_GLOBAL never covers
comprehension/lambda locals (those are LOAD_FAST/LOAD_DEREF), so there are no
false positives from those.

Known limitation: `from x import *` populates module.__dict__, so a dropped
global whose name coincidentally matches a star-exported symbol is satisfied by
the wrong definition — a semantic bug, not a NameError, out of scope here.
"""
import dis
import types
import builtins
import importlib
import pkgutil

PACKAGES = ["main_pkg", "mcp_tools", "kis_api", "db_collector", "dashboard_home"]
MODULES = ["report_crawler", "dashboard"]
_BUILTINS = set(dir(builtins))


def _iter_load_globals(code):
    for instr in dis.get_instructions(code):
        if instr.opname == "LOAD_GLOBAL":
            lineno = instr.positions.lineno if instr.positions else code.co_firstlineno
            yield instr.argval, lineno
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _iter_load_globals(const)


def _module_undefined(mod):
    with open(mod.__file__, encoding="utf-8") as f:
        code = compile(f.read(), mod.__file__, "exec")
    modns = set(vars(mod).keys())
    out = {}
    for name, lineno in _iter_load_globals(code):
        if name in modns or name in _BUILTINS:
            continue
        out.setdefault(name, lineno)
    return out


def _all_modules():
    names = list(MODULES)
    for pkg_name in PACKAGES:
        pkg = importlib.import_module(pkg_name)
        names.append(pkg_name)
        if hasattr(pkg, "__path__"):
            names += [i.name for i in pkgutil.walk_packages(pkg.__path__, pkg_name + ".")]
    return names


def test_no_undefined_globals():
    findings = []
    for name in _all_modules():
        mod = importlib.import_module(name)
        if not getattr(mod, "__file__", "").endswith(".py"):
            continue
        for nm, ln in _module_undefined(mod).items():
            findings.append(f"{name}:{ln}: {nm}")
    assert not findings, (
        "Undefined global-name references (refactor regression — would NameError "
        "at call time):\n  " + "\n  ".join(sorted(findings))
    )
