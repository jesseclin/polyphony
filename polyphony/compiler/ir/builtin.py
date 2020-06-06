﻿from .symbol import Symbol
from .type import Type


builtin_mappings = {
    '__builtin__._assert': 'assert',
    '__builtin__._new': '$new',
}

builtin_symbols = {}


def append_builtin(namespace, scope):
    if namespace.name == '__builtin__':
        if scope.name in builtin_mappings:
            name = builtin_mappings[scope.name]
        else:
            name = scope.base_name
    else:
        name = scope.name

    if scope.is_function():
        param_types = [sym.typ for sym, _, _ in scope.params]
        t = Type.function(scope, scope.return_type, param_types)
    elif scope.is_class():
        t = Type.klass(scope)
    else:
        assert False
    sym = Symbol(name, namespace, {'builtin'}, t)
    builtin_symbols[name] = sym