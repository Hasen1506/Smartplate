"""Grill #57: swiggy_live.py was split mechanically. The pure reply-parsing and money
helpers live in swiggy_schema / swiggy_money; swiggy_live re-exports the very same
objects, so every existing caller and test keeps working."""
import ast
import inspect

from smartplate.integrations import swiggy_live, swiggy_money, swiggy_schema


def test_moved_names_are_reexported_as_the_same_objects():
    for module in (swiggy_schema, swiggy_money):
        tree = ast.parse(inspect.getsource(module))
        names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))] + \
                [t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)]
        assert names
        for name in names:
            assert getattr(swiggy_live, name) is getattr(module, name), name


def test_split_modules_stay_pure():
    """No network, no database, no swiggy_live import: the helpers can't reach anything
    the tests monkeypatch on swiggy_live."""
    for module in (swiggy_schema, swiggy_money):
        tree = ast.parse(inspect.getsource(module))
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
                   {f"{n.module}:{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert not {i for i in imported if "swiggy_live" in i or i.endswith(":db") or "urllib" in i
                    or i.startswith("None:db")}, (module.__name__, imported)
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not calls & {"call", "_conn", "_tool", "_address", "remember_photos", "_checkout_state"}
