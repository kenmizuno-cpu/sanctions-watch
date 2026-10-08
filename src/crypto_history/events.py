"""Decode Transfer events, preserving exact integers and ignoring token names."""
import hashlib
import re
from ..crypto_validation.common import decode_base58, BASE58

TRANSFER='ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

class Rejected(ValueError):
    """A returned receipt/block cannot establish the required proof."""

def tron_hex(address):
    return decode_base58(address)[1:-4].hex()

def tron_address(hex20):
    raw=bytes.fromhex('41'+hex20)
    raw+=hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    n=int.from_bytes(raw,'big');out=''
    while n:n,r=divmod(n,58);out=BASE58[r]+out
    return out

def transfer(log,contract,subject,*,tron=False):
    if not isinstance(log,dict):return None
    prefix='' if tron else '0x'
    c=tron_hex(contract) if tron else contract[2:].lower()
    a=tron_hex(subject) if tron else subject[2:].lower()
    if str(log.get('address','')).lower()!=prefix+c:return None
    topics=log.get('topics')
    if not isinstance(topics,list) or len(topics)!=3 or topics[0]!=prefix+TRANSFER:return None
    if any(not isinstance(t,str) or not re.fullmatch(prefix+r'0{24}[0-9a-fA-F]{40}',t) for t in topics[1:]):return None
    source,dest=[t[-40:].lower() for t in topics[1:]]
    if a not in (source,dest):return None
    data=log.get('data')
    if not isinstance(data,str) or not re.fullmatch(prefix+r'[0-9a-fA-F]{64}',data):return None
    amount=int(data[len(prefix):],16)
    if amount==0:return None
    address=tron_address if tron else lambda s:'0x'+s
    return {'amount_raw':str(amount),'from_address':address(source),'to_address':address(dest)}

def verify_candidates(candidates,has_more,providers,prove,scope):
    """Try at most three distinct candidate transactions; retain at most one proof."""
    checked=0;unavailable=0;rejected=0;proofs=[]
    for tx in dict.fromkeys(candidates):
        if checked>=3:break
        checked+=1;failed=False;proof=None
        for provider,base in providers:
            try:
                proof=prove(tx,base)
                proof.update(receipt_provider=provider,receipt_endpoint=base)
                break
            except Rejected:pass
            except Exception:failed=True
        if proof:
            proofs.append(proof);break
        if failed:unavailable+=1
        else:rejected+=1
    return {'state':'VERIFIED' if proofs else 'PARTIAL' if unavailable else 'NO_PROOF',
            'candidates_count':str(len(set(candidates))),'checked_candidates':str(checked),
            'unavailable_candidates':str(unavailable),'rejected_candidates':str(rejected),
            'has_more':bool(has_more),'proofs':proofs,'scope':scope,
            **({'error':'取引結果の取得不足。空履歴・失敗取引とは区別。'} if unavailable and not proofs else {})}
