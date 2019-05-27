from polyphony import testbench
from polyphony.timing import timed, clktime, clkrange, clkfence


@timed
@testbench
def test():
    N = 10
    assert clktime() == 0
    clkfence()
    for i in clkrange(N):
        assert clktime() == (i * 2) + 2
        clkfence()
    clkfence()
    assert clktime() == N * 2 + 3


test()