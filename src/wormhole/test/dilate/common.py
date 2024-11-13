from unittest import mock
from zope.interface import alsoProvides
from ..._interfaces import IDilationManager, IWormhole


def mock_manager():
    m = mock.Mock()
    alsoProvides(m, IDilationManager)
    return m


def mock_wormhole():
    m = mock.Mock()
    alsoProvides(m, IWormhole)
    return m


def clear_mock_calls(*args):
    for a in args:
        a.mock_calls[:] = []
