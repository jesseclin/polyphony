﻿from collections import deque
from .typeeval import TypeEvaluator
from ..irvisitor import IRVisitor
from ..ir import *
from ..scope import Scope
from ..type import Type
from ..symbol import Symbol
from ...common.env import env
from ...common.common import fail
from ...common.errors import Errors
from ...frontend.python.pure import PureFuncTypeInferrer
import logging
logger = logging.getLogger(__name__)


def type_error(ir, err_id, args=None):
    fail(ir, err_id, args)


class RejectPropagation(Exception):
    pass


class TypePropagation(IRVisitor):
    def __init__(self, is_strict=False):
        self.is_strict = is_strict

    def process_all(self):
        worklist = deque([Scope.global_scope()])
        return self._process_all(worklist)

    def _process_all(self, worklist):
        self.typed = []
        self.pure_type_inferrer = PureFuncTypeInferrer()
        self.worklist = worklist
        while self.worklist:
            scope = self.worklist.popleft()
            if scope.is_lib():
                if scope.is_specialized():
                    self.typed.append(scope)
                continue
            elif scope.is_directory():
                continue
            if scope.is_function() and scope.return_type is None:
                scope.return_type = Type.undef()
            try:
                self.process(scope)
            except RejectPropagation as r:
                self.worklist.append(scope)
                continue
            #print(scope.name)
            assert scope not in self.typed
            self.typed.append(scope)
        return self.typed

    def _add_scope(self, scope):
        if scope is not self.scope and scope not in self.typed and scope not in self.worklist:
            self.worklist.append(scope)

    def visit_UNOP(self, ir):
        return self.visit(ir.exp)

    def visit_BINOP(self, ir):
        l_t = self.visit(ir.left)
        r_t = self.visit(ir.right)
        if l_t.is_bool() and r_t.is_bool() and not ir.op.startswith('Bit'):
            return Type.int(2)
        return l_t

    def visit_RELOP(self, ir):
        self.visit(ir.left)
        self.visit(ir.right)
        return Type.bool()

    def visit_CONDOP(self, ir):
        self.visit(ir.cond)
        ltype = self.visit(ir.left)
        self.visit(ir.right)
        return ltype

    def _convert_call(self, ir):
        clazz = ir.func.symbol().typ.get_scope()
        if clazz:
            if clazz.is_port():
                fun_name = 'wr' if ir.args else 'rd'
            else:
                fun_name = env.callop_name
            func_sym = clazz.find_sym(fun_name)
            if not func_sym:
                fail(self.current_stm, Errors.IS_NOT_CALLABLE, [clazz.name])
            assert func_sym.typ.is_function()
            ir.func = ATTR(ir.func, clazz.symbols[fun_name], Ctx.LOAD)
            ir.func.funcall = True
            ir.func.attr_scope = clazz

    def visit_CALL(self, ir):
        self.visit(ir.func)
        func_scope = ir.func_scope()
        if func_scope.is_method():
            params = func_scope.params[1:]
        else:
            params = func_scope.params[:]
        arg_types = [self.visit(arg) for _, arg in ir.args]
        if params:
            assert func_scope.is_specialized()
        for p, arg_t in zip(params, arg_types):
            self._propagate(p.sym, arg_t)
        self._add_scope(func_scope)
        return func_scope.return_type

    def visit_NEW(self, ir):
        func_scope = ir.func_scope()
        ret_t = Type.object(func_scope)
        func_scope.return_type = ret_t
        ctor = func_scope.find_ctor()
        params = ctor.params[1:]
        arg_types = [self.visit(arg) for _, arg in ir.args]
        if params:
            assert func_scope.is_specialized()
        for p, arg_t in zip(params, arg_types):
            self._propagate(p.sym, arg_t)
        self._add_scope(func_scope)
        self._add_scope(ctor)
        return ret_t

    def visit_SYSCALL(self, ir):
        ir.args = self._normalize_syscall_args(ir.sym.name, ir.args, ir.kwargs)
        for _, arg in ir.args:
            self.visit(arg)
        if ir.sym.name == 'polyphony.io.flipped':
            temp = ir.args[0][1]
            if temp.symbol().typ.is_undef():
                raise RejectPropagation(ir)
            arg_scope = temp.symbol().typ.get_scope()
            return Type.object(arg_scope)
        elif ir.sym.name == '$new':
            _, typ = ir.args[0]
            assert typ.symbol().typ.is_class()
            return Type.object(typ.symbol().typ.get_scope())
        else:
            assert ir.sym.typ.is_function()
            return ir.sym.typ.get_return_type()

    def visit_CONST(self, ir):
        if isinstance(ir.value, bool):
            return Type.bool()
        elif isinstance(ir.value, int):
            return Type.int()
        elif isinstance(ir.value, str):
            return Type.str()
        elif ir.value is None:
            return Type.int()
        else:
            type_error(self.current_stm, Errors.UNSUPPORTED_LETERAL_TYPE,
                       [repr(ir)])

    def visit_TEMP(self, ir):
        if ir.sym.typ.is_class() and ir.sym.typ.get_scope().is_typeclass():
            t = Type.from_ir(ir)
            if t.is_object():
                ir.sym.typ = ir.sym.typ.with_scope(t.get_scope())
            else:
                type_scope, args = Type.to_scope(t)
                ir.sym.typ = ir.sym.typ.with_scope(type_scope).with_typeargs(args)
        elif ir.funcall is False and ir.sym.typ.is_function():
            func_scope = ir.sym.typ.get_scope()
            self._add_scope(func_scope)
        return ir.sym.typ

    def visit_ATTR(self, ir):
        exptyp = self.visit(ir.exp)
        if exptyp.is_undef():
            raise RejectPropagation(ir)
        if exptyp.is_object() or exptyp.is_class() or exptyp.is_namespace() or exptyp.is_port():
            attr_scope = exptyp.get_scope()
            ir.attr_scope = attr_scope
            self._add_scope(attr_scope)

        if ir.attr_scope:
            assert ir.attr_scope.is_containable()
            if isinstance(ir.attr, str):
                if not ir.attr_scope.has_sym(ir.attr):
                    type_error(self.current_stm, Errors.UNKNOWN_ATTRIBUTE,
                               [ir.attr])
                ir.attr = ir.attr_scope.find_sym(ir.attr)
            elif ir.attr_scope is not ir.attr.scope:
                ir.attr = ir.attr_scope.find_sym(ir.attr.name)
            assert ir.attr
            if ir.attr.typ.is_object():
                ir.attr.add_tag('subobject')
            if ir.exp.symbol().typ.is_object() and ir.exp.symbol().name != env.self_name and self.scope.is_worker():
                ir.exp.symbol().add_tag('subobject')
            if ir.funcall is False and ir.attr.typ.is_function():
                func_scope = ir.attr.typ.get_scope()
                self._add_scope(func_scope)
            return ir.attr.typ

        type_error(self.current_stm, Errors.UNKNOWN_ATTRIBUTE, [ir.attr])

    def visit_MREF(self, ir):
        mem_t = self.visit(ir.mem)
        if mem_t.is_undef():
            raise RejectPropagation(ir)
        offs_t = self.visit(ir.offset)
        if offs_t.is_undef():
            raise RejectPropagation(ir)

        if mem_t.is_class() and mem_t.get_scope().is_typeclass():
            t = Type.from_ir(ir)
            if t.is_object():
                mem_t = mem_t.with_scope(t.get_scope())
            else:
                type_scope, args = Type.to_scope(t)
                mem_t = mem_t.with_scope(type_scope).with_typeargs(args)
            return mem_t
        elif not mem_t.is_seq():
            type_error(self.current_stm, Errors.IS_NOT_SUBSCRIPTABLE,
                       [ir.mem])
        elif mem_t.is_tuple():
            # TODO: Return union type if the offset is variable
            return mem_t.get_element()
        else:
            assert mem_t.is_list()
            if not offs_t.is_int():
                type_error(self.current_stm, Errors.MUST_BE_X_TYPE,
                           [ir.offset, 'int', offs_t])
        return mem_t.get_element()

    def visit_MSTORE(self, ir):
        mem_t = self.visit(ir.mem)
        if mem_t.is_undef():
            raise RejectPropagation(ir)
        ir.mem.symbol().typ = mem_t.with_ro(False)
        offs_t = self.visit(ir.offset)
        if not offs_t.is_int():
            type_error(self.current_stm, Errors.MUST_BE_X_TYPE,
                       [ir.offset, 'int', offs_t])
        if not mem_t.is_seq():
            type_error(self.current_stm, Errors.IS_NOT_SUBSCRIPTABLE,
                       [ir.mem])
        return mem_t

    def visit_ARRAY(self, ir):
        if not ir.repeat.is_a(CONST):
            self.visit(ir.repeat)
        if not ir.sym:
            ir.sym = self.scope.add_temp('@array')
        item_t = None
        if self.current_stm.dst.is_a([TEMP, ATTR]):
            dsttyp = self.current_stm.dst.symbol().typ
            if dsttyp.is_seq() and dsttyp.get_element().is_explicit():
                item_t = dsttyp.get_element()
        if item_t is None:
            item_typs = [self.visit(item) for item in ir.items]
            if self.current_stm.src == ir:
                if any([t.is_undef() for t in item_typs]):
                    raise RejectPropagation(ir)

            if item_typs and all([Type.is_assignable(item_typs[0], item_t) for item_t in item_typs]):
                if item_typs[0].is_scalar() and item_typs[0].is_int():
                    maxwidth = max([item_t.get_width() for item_t in item_typs])
                    signed = any([item_t.has_signed() and item_t.get_signed() for item_t in item_typs])
                    item_t = Type.int(maxwidth, signed)
                else:
                    item_t = item_typs[0]
            else:
                assert False  # TODO:
        if self.is_strict and ir.repeat.is_a(CONST):
            length = len(ir.items) * ir.repeat.value
        else:
            length = Type.ANY_LENGTH
        if ir.is_mutable:
            t = Type.list(item_t, length)
        else:
            t = Type.tuple(item_t, length)
        if all(item.is_a(CONST) for item in ir.items):
            t = t.with_ro(True)
        ir.sym.typ = t
        return t

    def visit_EXPR(self, ir):
        self.visit(ir.exp)

    def visit_CJUMP(self, ir):
        self.visit(ir.exp)

    def visit_MCJUMP(self, ir):
        for cond in ir.conds:
            self.visit(cond)

    def visit_JUMP(self, ir):
        pass

    def visit_RET(self, ir):
        typ = self.visit(ir.exp)
        self.scope.return_type = typ
        sym = self.scope.parent.find_sym(self.scope.base_name)
        sym.typ = sym.typ.with_return_type(typ)

    def visit_MOVE(self, ir):
        src_typ = self.visit(ir.src)
        if src_typ.is_undef():
            raise RejectPropagation(ir)
        dst_typ = self.visit(ir.dst)

        if ir.dst.is_a([TEMP, ATTR]):
            if not isinstance(ir.dst.symbol(), Symbol):
                # the type of object has not inferenced yet
                raise RejectPropagation(ir)
            self._propagate(ir.dst.symbol(), src_typ)
        elif ir.dst.is_a(ARRAY):
            if src_typ.is_undef():
                # the type of object has not inferenced yet
                raise RejectPropagation(ir)
            if not src_typ.is_tuple() or not dst_typ.is_tuple():
                raise RejectPropagation(ir)
            elem_t = src_typ.get_element()
            for item in ir.dst.items:
                assert item.is_a([TEMP, ATTR, MREF])
                if item.is_a([TEMP, ATTR]):
                    self._propagate(item.symbol(), elem_t)
                elif item.is_a(MREF):
                    item.mem.symbol().typ = item.mem.symbol().typ.with_element(elem_t)
        elif ir.dst.is_a(MREF):
            pass
        else:
            assert False
        # check mutable method
        if (self.scope.is_method() and ir.dst.is_a(ATTR) and
                ir.dst.head().name == env.self_name and
                not self.scope.is_mutable()):
            self.scope.add_tag('mutable')

    def visit_PHI(self, ir):
        arg_types = [self.visit(arg) for arg in ir.args]
        for arg_t in arg_types:
            self._propagate(ir.var.symbol(), arg_t)

    def visit_UPHI(self, ir):
        self.visit_PHI(ir)

    def visit_LPHI(self, ir):
        self.visit_PHI(ir)

    def _normalize_args(self, func_name, params, args, kwargs):
        nargs = []
        if len(params) < len(args):
            nargs = args[:]
            for name, arg in kwargs.items():
                nargs.append((name, arg))
            kwargs.clear()
            return nargs
        for i, param in enumerate(params):
            name = param.copy.name
            if i < len(args):
                nargs.append((name, args[i][1]))
            elif name in kwargs:
                nargs.append((name, kwargs[name]))
            elif param.defval:
                nargs.append((name, param.defval))
            else:
                type_error(self.current_stm, Errors.MISSING_REQUIRED_ARG_N,
                           [func_name, param.copy.name])
        kwargs.clear()
        return nargs

    def _normalize_syscall_args(self, func_name, args, kwargs):
        return args

    def _propagate(self, sym, typ):
        if sym.typ.is_undef() and typ.is_undef():
            return
        assert not typ.is_undef()
        if not Type.can_propagate(sym.typ, typ):
            return
        if sym.typ.is_explicit():
            typ = Type.propagate(sym.typ, typ)
        else:
            if sym.typ != typ:
                logger.debug(f'typeprop {sym.name}: {sym.typ} -> {typ}')
        sym.typ = typ


class TypeSpecializer(TypePropagation):
    def process_all(self):
        scopes = Scope.get_scopes(bottom_up=False,
                                  with_global=True,
                                  with_class=True,
                                  with_lib=True)
        scopes = [s for s in scopes if (s.is_namespace() or s.is_class())]
        worklist = deque(scopes)
        return self._process_all(worklist)

    def visit_CALL(self, ir):
        self.visit(ir.func)
        if ir.func.is_a(TEMP):
            func_name = ir.func.symbol().orig_name()
            t = ir.func.symbol().typ
            if t.is_object() or t.is_port():
                self._convert_call(ir)
            elif t.is_function():
                assert t.has_scope()
            else:
                type_error(self.current_stm, Errors.IS_NOT_CALLABLE,
                           [func_name])
        elif ir.func.is_a(ATTR):
            if not ir.func.attr_scope:
                assert False
                raise RejectPropagation(ir)
            func_name = ir.func.symbol().orig_name()
            t = ir.func.symbol().typ
            if t.is_object() or t.is_port():
                self._convert_call(ir)
            if t.is_undef():
                assert False
                raise RejectPropagation(ir)
            if ir.func_scope().is_mutable():
                pass  # ir.func.exp.ctx |= Ctx.STORE
        else:
            assert False

        if not ir.func_scope():
            assert False
            # we cannot specify the callee because it has not been evaluated yet.
            raise RejectPropagation(ir)

        assert not ir.func_scope().is_class()
        if (self.scope.is_testbench() and
                ir.func_scope().is_function() and not ir.func_scope().is_inlinelib()):
            ir.func_scope().add_tag('function_module')

        if ir.func_scope().is_pure():
            if not env.config.enable_pure:
                fail(self.current_stm, Errors.PURE_IS_DISABLED)
            if not ir.func_scope().parent.is_global():
                fail(self.current_stm, Errors.PURE_MUST_BE_GLOBAL)
            if (ir.func_scope().return_type and
                    not ir.func_scope().return_type.is_undef() and
                    not ir.func_scope().return_type.is_any()):
                return ir.func_scope().return_type
            ret, type_or_error = self.pure_type_inferrer.infer_type(self.current_stm, ir, self.scope)
            if ret:
                return type_or_error
            else:
                fail(self.current_stm, type_or_error)
        elif ir.func_scope().is_method():
            params = ir.func_scope().params[1:]
        else:
            params = ir.func_scope().params[:]
        ir.args = self._normalize_args(ir.func_scope().base_name, params, ir.args, ir.kwargs)
        if ir.func_scope().is_lib():
            return self.visit_CALL_lib(ir)

        arg_types = [self.visit(arg) for _, arg in ir.args]
        if any([atype.is_undef() for atype in arg_types]):
            assert False
            raise RejectPropagation(ir)
        if ir.func_scope().is_specialized():
            # Must return after ir.args are visited
            return ir.func_scope().return_type
        ret_t = ir.func_scope().return_type
        param_types = [p.sym.typ for p in params]
        if param_types:
            new_param_types = self._get_new_param_types(param_types, arg_types)
            new_scope, is_new = self._specialize_function_with_types(ir.func_scope(), new_param_types)
            if is_new:
                new_scope_sym = ir.func_scope().parent.find_sym(new_scope.base_name)
                self._add_scope(new_scope)
            else:
                new_scope_sym = ir.func_scope().parent.find_sym(new_scope.base_name)
            ret_t = new_scope.return_type
            if ir.func.is_a(TEMP):
                ir.func = TEMP(new_scope_sym, Ctx.LOAD)
            elif ir.func.is_a(ATTR):
                ir.func = ATTR(ir.func.exp, new_scope_sym, Ctx.LOAD)
            else:
                assert False
            ir.func.funcall = True
        else:
            self._add_scope(ir.func_scope())
        return ret_t

    def visit_CALL_lib(self, ir):
        if ir.func_scope().base_name == 'append_worker':
            if not ir.args[0][1].symbol().typ.is_function():
                assert False
            worker = ir.args[0][1].symbol().typ.get_scope()
            if not worker.is_worker():
                worker.add_tag('worker')
            if worker.is_specialized():
                return ir.func_scope().return_type
            arg_types = [self.visit(arg) for _, arg in ir.args[1:]]
            if any([atype.is_undef() for atype in arg_types]):
                raise RejectPropagation(ir)
            if worker.is_method():
                params = worker.params[1:]
            else:
                params = worker.params[:]
            param_types = [p.sym.typ for p in params]
            if param_types:
                new_param_types = self._get_new_param_types(param_types, arg_types)
                new_scope, is_new = self._specialize_worker_with_types(worker, new_param_types)
                if is_new:
                    new_scope_sym = worker.parent.find_sym(new_scope.base_name)
                    self._add_scope(new_scope)
                else:
                    new_scope_sym = worker.parent.find_sym(new_scope.base_name)
                if ir.args[0][1].is_a(TEMP):
                    ir.args[0] = (ir.args[0][0], TEMP(new_scope_sym, Ctx.LOAD))
                elif ir.args[0][1].is_a(ATTR):
                    ir.args[0] = (ir.args[0][0], ATTR(ir.args[0][1].exp, new_scope_sym, Ctx.LOAD))
                else:
                    assert False
            else:
                self._add_scope(worker)
        elif ir.func_scope().base_name == 'assign':
            assert ir.func_scope().parent.is_port()
            _, arg = ir.args[0]
            self.visit(arg)

        assert ir.func_scope().return_type is not None
        assert not ir.func_scope().return_type.is_undef()
        return ir.func_scope().return_type

    def visit_NEW(self, ir):
        self._add_scope(ir.func_scope().parent)
        ret_t = Type.object(ir.func_scope())
        ir.func_scope().return_type = ret_t
        ctor = ir.func_scope().find_ctor()
        ir.args = self._normalize_args(ir.func_scope().base_name, ctor.params[1:], ir.args, ir.kwargs)
        arg_types = [self.visit(arg) for _, arg in ir.args]
        if ir.func_scope().is_specialized():
            return ir.func_scope().return_type
        params = ctor.params[1:]
        param_types = [p.sym.typ for p in params]
        if param_types:
            new_param_types = self._get_new_param_types(param_types, arg_types)
            new_scope, is_new = self._specialize_class_with_types(ir.func_scope(), new_param_types)
            if is_new:
                new_ctor = new_scope.find_ctor()
                new_scope_sym = ir.func_scope().parent.gen_sym(new_scope.base_name)
                new_scope_sym.typ = Type.klass(new_scope)
                ctor_t = Type.function(new_ctor,
                                       new_scope.return_type,
                                       tuple([new_ctor.params[0].sym.typ] + new_param_types))
                new_ctor_sym = new_scope.find_sym(new_ctor.base_name)
                new_ctor_sym.typ = ctor_t
                self._add_scope(new_scope)
                self._add_scope(new_ctor)
            else:
                new_scope_sym = ir.func_scope().parent.find_sym(new_scope.base_name)
            ret_t = new_scope.return_type
            ir.sym = new_scope_sym
        else:
            self._add_scope(ir.func_scope())
            self._add_scope(ctor)
        return ret_t

    def _get_new_param_types(self, param_types, arg_types):
        new_param_types = []
        for param_t, arg_t in zip(param_types, arg_types):
            if param_t.is_explicit():
                new_param_t = Type.propagate(param_t, arg_t)
                new_param_types.append(new_param_t)
            else:
                arg_t = arg_t.with_explicit(False)
                new_param_types.append(arg_t)
        return new_param_types

    def _specialize_function_with_types(self, scope, types):
        assert not scope.is_specialized()
        postfix = Type.mangled_names(types)
        assert postfix
        name = f'{scope.base_name}_{postfix}'
        qualified_name = (scope.parent.name + '.' + name) if scope.parent else name
        if qualified_name in env.scopes:
            return env.scopes[qualified_name], False
        new_scope = scope.instantiate(postfix, scope.children, with_tag=False)
        assert qualified_name == new_scope.name
        assert new_scope.return_type is not None
        if new_scope.is_method():
            params = new_scope.params[1:]
        else:
            params = new_scope.params[:]
        new_types = []
        for p, new_t in zip(params, types):
            new_t = new_t.with_perfect_explicit()
            p.sym.typ = new_t
            new_types.append(new_t)
        new_scope.add_tag('specialized')
        sym = new_scope.parent.find_sym(new_scope.base_name)
        sym.typ = sym.typ.with_param_types(new_types).with_return_type(new_scope.return_type)
        return new_scope, True

    def _specialize_class_with_types(self, scope, types):
        assert not scope.is_specialized()
        if scope.is_port():
            return self._specialize_port_with_types(scope, types)
        postfix = Type.mangled_names(types)
        assert postfix
        name = f'{scope.base_name}_{postfix}'
        qualified_name = (scope.parent.name + '.' + name) if scope.parent else name
        if qualified_name in env.scopes:
            return env.scopes[qualified_name], False

        children = [s for s in scope.children]
        closures = [c for c in scope.collect_scope() if c.is_closure()]
        new_scope = scope.instantiate(postfix, children + closures, with_tag=False)
        assert qualified_name == new_scope.name
        assert new_scope.return_type is not None
        new_scope.return_type = Type.object(new_scope)
        new_ctor = new_scope.find_ctor()
        new_ctor.return_type = Type.object(new_ctor)

        for p, new_t in zip(new_ctor.params[1:], types):
            new_t = new_t.with_perfect_explicit()
            p.sym.typ = new_t
        new_scope.add_tag('specialized')
        return new_scope, True

    def _specialize_port_with_types(self, scope, types):
        typ = types[0]
        if typ.is_class():
            typscope = typ.get_scope()
            if typscope.is_typeclass():
                dtype = Type.from_typeclass(typscope)
                if typ.has_typeargs():
                    args = typ.get_typeargs()
                    dtype.attrs.update(args)  # FIXME
            else:
                dtype = typ
        else:
            dtype = typ
        postfix = Type.mangled_names([dtype])
        name = f'{scope.base_name}_{postfix}'
        qualified_name = (scope.parent.name + '.' + name) if scope.parent else name
        if qualified_name in env.scopes:
            return env.scopes[qualified_name], False
        new_scope = scope.instantiate(postfix, scope.children, with_tag=False)
        assert qualified_name == new_scope.name
        assert new_scope.return_type is not None
        new_scope.return_type = Type.object(new_scope)
        new_ctor = new_scope.find_ctor()
        new_ctor.return_type = Type.object(new_ctor)
        dtype_param = new_ctor.params[1]
        dtype_param.copy.typ = dtype_param.sym.typ = typ.with_explicit(True)
        init_param = new_ctor.params[3]
        init_param.copy.typ = init_param.sym.typ = dtype.with_explicit(True)

        new_scope.add_tag('specialized')
        for child in new_scope.children:
            for sym, copy, _ in child.params:
                if sym.typ.is_generic():
                    copy.typ = sym.typ = dtype
            if child.return_type.is_generic():
                child.return_type = dtype
            child.add_tag('specialized')
        return new_scope, True

    def _specialize_worker_with_types(self, scope, types):
        assert not scope.is_specialized()
        postfix = Type.mangled_names(types)
        assert postfix
        name = f'{scope.base_name}_{postfix}'
        qualified_name = (scope.parent.name + '.' + name) if scope.parent else name
        if qualified_name in env.scopes:
            return env.scopes[qualified_name], False
        new_scope = scope.instantiate(postfix, scope.children, with_tag=False)
        assert qualified_name == new_scope.name
        assert new_scope.return_type is not None
        if new_scope.is_method():
            params = new_scope.params[1:]
        else:
            params = new_scope.params[:]
        for p, new_t in zip(params, types):
            new_t = new_t.with_perfect_explicit()
            p.sym.typ = new_t
        new_scope.add_tag('specialized')
        return new_scope, True


class StaticTypePropagation(TypePropagation):
    def __init__(self, is_strict):
        super().__init__(is_strict=is_strict)

    def process_all(self):
        scopes = Scope.get_scopes(bottom_up=True,
                                  with_global=True,
                                  with_class=True,
                                  with_lib=True)
        scopes = [s for s in scopes if (s.is_namespace() or s.is_class())]
        self._process_scopes(scopes)

    def _process_scopes(self, scopes):
        stms = []
        #dtrees = {}
        worklist = deque(scopes)
        while worklist:
            s = worklist.popleft()
            stms = self.collect_stms(s)
            # FIXME: Since lineno is not essential information for IR,
            #        It should not be used as sort key
            stms = sorted(stms, key=lambda s: s.loc.lineno)
            for stm in stms:
                self.current_stm = stm
                self.scope = stm.block.scope
                try:
                    self.visit(stm)
                except RejectPropagation as r:
                    worklist.append(s)
                    break

    def collect_stms(self, scope):
        stms = []
        for blk in scope.traverse_blocks():
            stms.extend(blk.stms)
        return stms

    def _add_scope(self, scope):
        pass

    def visit_CALL(self, ir):
        if self.is_strict:
            return super().visit_CALL(ir)
        else:
            self.visit(ir.func)
            func_scope = ir.func_scope()
            return func_scope.return_type

    def visit_NEW(self, ir):
        if self.is_strict:
            return super().visit_NEW(ir)
        else:
            func_scope = ir.func_scope()
            return Type.object(func_scope)


class TypeReplacer(IRVisitor):
    def __init__(self, old_t, new_t, comparator):
        self.old_t = old_t
        self.new_t = new_t
        self.comparator = comparator

    def visit_TEMP(self, ir):
        if self.comparator(ir.sym.typ, self.old_t):
            ir.sym.typ = self.new_t

    def visit_ATTR(self, ir):
        self.visit(ir.exp)
        if self.comparator(ir.attr.typ, self.old_t):
            ir.attr.typ = self.new_t


class InstanceTypePropagation(TypePropagation):
    def _propagate(self, sym, typ):
        if sym.typ.is_object() and sym.typ.get_scope().is_module():
            sym.typ = typ


class TypeEvalVisitor(IRVisitor):
    def process(self, scope):
        self.type_evaluator = TypeEvaluator(scope)
        for sym, copy, _ in scope.params:
            pt = self._eval(sym.typ)
            sym.typ = pt
            pt = self._eval(copy.typ)
            copy.typ = pt
        if scope.return_type:
            scope.return_type = self._eval(scope.return_type)
        for sym in scope.constants.keys():
            pt = self._eval(sym.typ)
            sym.typ = pt
        super().process(scope)

    def _eval(self, typ):
        return self.type_evaluator.visit(typ)

    def visit_TEMP(self, ir):
        ir.sym.typ = self._eval(ir.sym.typ)

    def visit_ATTR(self, ir):
        if not isinstance(ir.attr, str):
            ir.attr.typ = self._eval(ir.attr.typ)

    def visit_SYSCALL(self, ir):
        ir.sym.typ = self._eval(ir.sym.typ)

    def visit_ARRAY(self, ir):
        if ir.sym:
            ir.sym.typ = self._eval(ir.sym.typ)