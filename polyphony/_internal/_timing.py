@decorator
def timed() -> None:
    pass


@builtin
def clksleep(clk_cycles:int) -> None:
    pass


@inlinelib
def clkfence() -> None:
    clksleep(0)


@builtin
def clkrange(clk_cycles:int) -> None:
    pass


@builtin
def clktime() -> int:
    pass


from .io import Port


@builtin
def wait_edge(old:int, new:int, *ports:Port) -> None:
    pass


@builtin
def wait_rising(*ports:Port) -> None:
    pass


@builtin
def wait_falling(*ports:Port) -> None:
    pass


@builtin
def wait_value(value:int, *ports:Port) -> None:
    pass
