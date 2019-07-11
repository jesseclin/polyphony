class Port:
    def __init__(self, dtype:generic, direction:str, init=None,
                 protocol:str='none',
                 rewritable:bool=False) -> object:
        pass

    def rd(self) -> generic:
        pass

    @mutable
    def wr(self, v:generic) -> None:
        pass

    def __call__(self, v=None) -> generic:
        pass

    def assign(self, fn:generic) -> None:
        pass

    def edge(self, old, new) -> bool:
        pass


class Queue:
    def __init__(self, dtype:generic, direction:str, maxsize:int=1) -> object:
        pass

    def rd(self) -> generic:
        pass

    def wr(self, v:generic) -> None:
        pass

    def __call__(self, v=None) -> generic:
        pass

    @predicate
    def empty(self) -> bool:
        pass

    @predicate
    def full(self) -> bool:
        pass


from . import timing


@timing.timed
@inlinelib
class Handshake:
    def __init__(self, dtype, direction, init=None):
        self.data = Port(dtype, direction, init)
        if direction == 'in':
            self.ready = Port(bool, 'out', 0, rewritable=True)
            self.valid = Port(bool, 'in', rewritable=True)
        else:
            self.ready = Port(bool, 'in', rewritable=True)
            self.valid = Port(bool, 'out', 0, rewritable=True)

    def rd(self):
        '''
        Read the current value from the port.
        '''
        self.ready.wr(True)
        timing.clkfence()
        while self.valid.rd() is not True:
            timing.clkfence()
        self.ready.wr(False)
        return self.data.rd()

    def wr(self, v):
        '''
        Write the value to the port.
        '''
        self.data.wr(v)
        self.valid.wr(True)
        timing.clkfence()
        while self.ready.rd() is not True:
            timing.clkfence()
        self.valid.wr(False)


@builtin
def flipped(obj:object) -> object:
    pass


@builtin
def connect(p0:object, p1:object):
    pass


@builtin
def thru(parent, child):
    pass
