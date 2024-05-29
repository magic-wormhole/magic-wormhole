import zope.interface
from twisted.trial import unittest
from attr import Attribute
from attr._make import NOTHING
from wormhole.util import provides


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


class TestProvides(unittest.TestCase):
    """
    Tests for `provides`.
    """

    def test_success(self):
        """
        Nothing happens if value provides requested interface.
        """

        @zope.interface.implementer(IFoo)
        class C(object):
            def f(self):
                pass

        v = provides(IFoo)
        v(None, simple_attr("x"), C())

    def test_fail(self):
        """
        Raises `TypeError` if interfaces isn't provided by value.
        """
        value = object()
        a = simple_attr("x")

        v = provides(IFoo)
        with self.assertRaises(TypeError):
            v(None, a, value)

    def test_repr(self):
        """
        Returned validator has a useful `__repr__`.
        """
        v = provides(IFoo)
        assert (
            "<provides validator for interface {interface!r}>".format(
                interface=IFoo
            )
        ) == repr(v)
