﻿from .type import Type
from ..common.common import Tagged
from ..common.env import env
from logging import getLogger
logger = getLogger(__name__)


class Symbol(Tagged):
    __slots__ = ['_id', '_name', '_scope', '_typ', '_ancestor']
    all_symbols = []

    TAGS = {
        'temp', 'param', 'return', 'condition', 'induction', 'alias', 'free',
        'self', 'static', 'subobject', 'field',
        'builtin', 'inlined', 'flattened', 'pipelined', 'predefined',
        'loop_counter', 'register', 'inherited'
    }

    @classmethod
    def unique_name(cls, prefix=None):
        if not prefix:
            prefix = cls.temp_prefix
        return '{}{}'.format(prefix, len(cls.all_symbols))

    @classmethod
    def dump(cls):
        logger.debug('All symbol instances ----------------')
        for sym in cls.all_symbols:
            s = str(sym) + '\n'
            s += '  defs\n'
            for d in sym.defs:
                s += '    ' + str(d) + '\n'
            s += '  uses\n'
            for u in sym.uses:
                s += '    ' + str(u) + '\n'
            logger.debug(s)

    return_prefix = '@function_return'
    condition_prefix = '@c'
    temp_prefix = '@t'
    param_prefix = '@in'

    def __init__(self, name, scope, tags, typ=None):
        super().__init__(tags)
        if typ is None:
            typ = Type.undef()
        self._id = len(Symbol.all_symbols)
        self._name = name
        self._scope = scope
        self._typ = typ
        self._ancestor = None
        Symbol.all_symbols.append(self)

    @property
    def id(self):
        return self._id

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, name):
        self._name = name

    @property
    def scope(self):
        return self._scope

    @property
    def typ(self):
        return self._typ

    @typ.setter
    def typ(self, typ):
        self._typ = typ
        if self._ancestor:
            self._ancestor.typ = typ

    @property
    def ancestor(self):
        return self._ancestor

    @ancestor.setter
    def ancestor(self, a):
        self._ancestor = a

    def __str__(self):
        #return '{}:{}({}:{})'.format(self.name, self.typ, self.id, self.scope.base_name)
        #return '{}:{}({})'.format(self.name, repr(self.typ), self.tags)
        if env.dev_debug_mode:
            return '{}:{}'.format(self._name, self._typ)
        return self._name

    def __repr__(self):
        #return '{}({})'.format(self.name, hex(self.__hash__()))
        return self._name

    def __lt__(self, other):
        return self._name < other._name

    def orig_name(self):
        if self._ancestor:
            return self._ancestor.orig_name()
        else:
            return self._name

    def root_sym(self):
        if self._ancestor:
            return self._ancestor.root_sym()
        else:
            return self

    def hdl_name(self):
        if self._typ.is_port():
            name = self._name[:]
        elif self._typ.is_object() and self._typ.get_scope().is_module() and self._ancestor:
            return self._ancestor.hdl_name()
        elif self._name[0] == '@' or self._name[0] == '!':
            name = self._name[1:]
        else:
            name = self._name[:]
        name = name.replace('#', '')
        return name

    def clone(self, scope, postfix=''):
        newsym = Symbol(self._name + postfix,
                        scope,
                        set(self.tags),
                        self._typ)
        newsym.ancestor = self._ancestor
        return newsym