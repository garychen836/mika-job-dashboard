"""Names a module uses at its top level or in functions that it never defines or imports (a quick stand-in for pyflakes,
for files copied out of a bigger module). Usage: python undefined_names.py FILE... ; exit 1 when something is missing."""
import ast, builtins, sys

bad = 0
for path in sys.argv[1:]:
    tree = ast.parse(open(path).read())
    defined = set(dir(builtins)) | {"__file__", "__name__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            defined |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
            a = node.args if not isinstance(node, ast.ClassDef) else None
            if a:
                defined |= {x.arg for x in a.args + a.kwonlyargs + a.posonlyargs} | ({a.vararg.arg} if a.vararg else set()) | ({a.kwarg.arg} if a.kwarg else set())
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            defined |= set(node.names)
    used = {(n.id, n.lineno) for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    missing = sorted({(n, l) for n, l in used if n not in defined}, key=lambda x: x[1])
    for n, l in missing:
        print(f"{path}:{l}: undefined name {n}")
    bad += len(missing)
sys.exit(1 if bad else 0)
