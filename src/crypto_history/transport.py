"""Reuse bounded nonredirecting transport; add only the reviewed history index."""
from ..crypto_chain.transport import Transport

class HistoryTransport(Transport):
    def __init__(self, max_calls=600, seconds=300, timeout=6):
        super().__init__(max_calls=max_calls,seconds=seconds,timeout=timeout)
        self.allowed.add('https://eth.blockscout.com')
