from polyphony import pure
from polyphony import testbench


@pure
def rand(seed, x, y):
    import random
    random.seed(seed)
    return random.randint(x, y)


@testbench
def test():
    assert rand(0, 1, 1000) == rand(0, 1, 1000)
    assert rand(0, -1000, 1000) == rand(0, -1000, 1000)


test()
