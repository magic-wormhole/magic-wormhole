import zope.interface
from attr import Attribute
from attr._make import NOTHING
from wormhole.util import provides

import pytest

class IFoo(zope.interface.Interface):
    """
    An interface.
    """

    def f():
        """
        A function called f.
        """


def simple_attr(name):
    return Attribute(
        name=name,
        default=NOTHING,
        validator=None,
        repr=True,
        cmp=None,
        eq=True,
        hash=None,
        init=True,
        converter=None,
        kw_only=False,
        inherited=False,
    )


def test_success():
    """
    Nothing happens if value provides requested interface.
    """

    @zope.interface.implementer(IFoo)
    class C:
        def f(self):
            pass

    v = provides(IFoo)
    v(None, simple_attr("x"), C())

def test_fail():
    """
    Raises `TypeError` if interfaces isn't provided by value.
    """
    value = object()
    a = simple_attr("x")

    v = provides(IFoo)
    with pytest.raises(TypeError):
        v(None, a, value)

def test_repr():
    """
    Returned validator has a useful `__repr__`.
    """
    v = provides(IFoo)
    assert (
        f"<provides validator for interface {IFoo!r}>"
    ) == repr(v)
