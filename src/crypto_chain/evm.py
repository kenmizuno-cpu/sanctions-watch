"""EVM state pinned to a block; issuer token balance and bounded recent Transfer logs."""
import re
from .registry import EVM
from .values import quantity, hash32

TRANSFER='0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

def token_logs(logs,contract,address,start,end):
    if not isinstance(logs,list) or len(logs)>1000: raise ValueError('invalid log result')
    topic='0x'+address[2:].lower().zfill(64); found=set()
    for log in logs:
        topics=log.get('topics',[])
        if (log.get('address','').lower()!=contract.lower() or log.get('removed') is not False
            or len(topics)!=3 or topics[0].lower()!=TRANSFER
            or topic not in [t.lower() for t in topics[1:]]
            or not start<=int(quantity(log.get('blockNumber')))<=end):
            raise ValueError('unrelated/unconfirmed token log')
        if not re.fullmatch(r'0x[0-9a-fA-F]{64}',log.get('data','')): raise ValueError('invalid token amount')
        found.add(hash32(log.get('transactionHash')))
    return sorted(found)

def observe(t,base,address,http):
    expected,tag,_=EVM[t.chain]
    key=('evm',base)
    if key not in http.contexts:
        chain=int(quantity(http.rpc(base,'eth_chainId',[])))
        if chain!=expected: raise ValueError('wrong chainId')
        b=http.rpc(base,'eth_getBlockByNumber',[tag,False])
        number=quantity(b['number']); block_hash=hash32(b['hash']); stamp=quantity(b['timestamp'])
        http.contexts[key]=(hex(int(number)),number,block_hash,stamp)
    block,number,block_hash,stamp=http.contexts[key]
    out={'block_height':number,'block_hash':block_hash,'block_timestamp':stamp,'confirmation':tag,
         'scope':'固定ブロックの残高・nonce（送信回数指標）・コントラクト。総取引数ではない。'}
    out['balance_raw']=quantity(http.rpc(base,'eth_getBalance',[address,block]));out['decimals']=18
    out['nonce']=quantity(http.rpc(base,'eth_getTransactionCount',[address,block]))
    code=http.rpc(base,'eth_getCode',[address,block])
    if not isinstance(code,str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})*',code): raise ValueError('invalid bytecode')
    out['is_contract']=code!='0x'
    out['positive']=int(out['balance_raw'])>0 or int(out['nonce'])>0 or out['is_contract']
    out['asset_match']=False
    if t.contract:
        token_code=http.rpc(base,'eth_getCode',[t.contract,block])
        if not isinstance(token_code,str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})+',token_code):
            raise ValueError('issuer contract absent/invalid')
        raw=http.rpc(base,'eth_call',[{'to':t.contract,'data':'0x70a08231'+address[2:].lower().zfill(64)},block])
        if not isinstance(raw,str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}',raw): raise ValueError('invalid balanceOf response')
        out['token_balance_raw']=quantity(raw);out['token_decimals']=6
        out['asset_match']=int(out['token_balance_raw'])>0
        out['positive']|=out['asset_match']
        # Failure of optional history cannot erase successful pinned balance observations.
        start=max(0,int(number)-255);topic='0x'+address[2:].lower().zfill(64)
        try:
            logs=[]
            for topics in [[TRANSFER,topic],[TRANSFER,None,topic]]:
                logs+=http.rpc(base,'eth_getLogs',[{'address':t.contract,'fromBlock':hex(start),
                    'toBlock':block,'topics':topics}])
            txs=token_logs(logs,t.contract,address,start,int(number))
            out['token_tx_sample']=txs[:3];out['recent_token_tx_count']=str(len(txs))
            out['asset_match']|=bool(txs);out['positive']|=bool(txs)
        except Exception as e:
            out['history_error']=type(e).__name__+': recent Transfer lookup failed'
        out['scope']+=' 発行元契約の残高・直近256ブロックのTransfer。全履歴・他チェーンは未確認。'
        out['history_from_block']=str(start)
    return out
