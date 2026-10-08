"""Primary-source reviewed history index and receipt providers; no returned URLs."""
from ..crypto_chain.registry import targets as chain_targets, PROVIDERS

VERSION='2026-10-08.1'
INDEX={'tron':('TronGrid','https://api.trongrid.io'),
       'ethereum':('Blockscout','https://eth.blockscout.com')}
RECEIPTS={k:PROVIDERS[k] for k in INDEX}

def targets(row):
    return [t for t in chain_targets(row) if t.contract and t.chain in INDEX]
