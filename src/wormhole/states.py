
from automat import MethodicalMachine

class WormholeState(object):
    _machine = MethodicalMachine()

    @_machine.state(initial=True)
    def start(self):
        pass

    
