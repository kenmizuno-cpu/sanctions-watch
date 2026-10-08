"""Ethereum index candidates verified against finalized canonical receipts/blocks."""
import re
from ..crypto_chain.values import quantity, hash32
from .registry import INDEX, RECEIPTS
from .events import transfer, verify_candidates, Rejected

def context(http,base):
    key=('history-eth',base)
    if key not in http.contexts:
        if quantity(http.rpc(base,'eth_chainId',[]))!='1':raise ValueError('wrong Ethereum chainId')
        b=http.rpc(base,'eth_getBlockByNumber',['finalized',False])
        hash32(b['hash'])
        http.contexts[key]=(int(quantity(b['number'])),int(quantity(b['timestamp'])))
    return http.contexts[key]

def prove(t,address,tx,http,base):
    final,stamp=context(http,base)
    r=http.rpc(base,'eth_getTransactionReceipt',[tx])
    if not isinstance(r,dict) or r.get('transactionHash','').lower()!=tx or r.get('status')!='0x1':raise Rejected('receipt not successful')
    height=quantity(r['blockNumber']);block_hash=hash32(r['blockHash'])
    if int(height)>final:raise Rejected('receipt not finalized')
    b=http.rpc(base,'eth_getBlockByNumber',[hex(int(height)),False])
    if (quantity(b['number'])!=height or hash32(b['hash'])!=block_hash or
        int(quantity(b['timestamp']))>stamp or not isinstance(b.get('transactions'),list) or
        tx not in [str(x).lower() for x in b['transactions']]):raise Rejected('canonical block mismatch')
    logs=r.get('logs')
    if not isinstance(logs,list) or len(logs)>1000:raise Rejected('invalid receipt logs')
    for log in logs:
        event=transfer(log,t.contract,address)
        if event is None:continue
        if (log.get('removed') is not False or log.get('transactionHash','').lower()!=tx or
            hash32(log.get('blockHash'))!=block_hash or quantity(log.get('blockNumber'))!=height):continue
        return {**event,'tx_hash':tx,'block_height':height,'block_hash':block_hash,
                'block_timestamp':quantity(b['timestamp']),'log_index':quantity(log['logIndex']),
                'confirmation':'finalized'}
    raise Rejected('issuer Transfer not found')

def observe(t,address,http):
    # Identity is validated even if the index is empty.
    errors=[]
    for _,base in RECEIPTS['ethereum']:
        try:context(http,base);break
        except Exception as e:errors.append(e)
    else:raise errors[-1]
    _,base=INDEX['ethereum']
    path='/api/v2/addresses/'+address+'/token-transfers?type=ERC-20&token='+t.contract
    data=http.request(base,path)
    if not isinstance(data,dict) or not isinstance(data.get('items'),list) or len(data['items'])>50 or 'next_page_params' not in data:
        raise ValueError('invalid Blockscout index')
    candidates=[]
    for item in data['items']:
        if not isinstance(item,dict):raise ValueError('invalid index item')
        if str(item.get('token',{}).get('address_hash','')).lower()!=t.contract.lower():continue
        tx=item.get('transaction_hash','')
        if not isinstance(tx,str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}',tx):raise ValueError('invalid transaction hash')
        candidates.append(tx.lower())
    return verify_candidates(candidates,data['next_page_params'] is not None,RECEIPTS['ethereum'],
        lambda tx,endpoint:prove(t,address,tx,http,endpoint),
        'Ethereumの履歴索引先頭最大50件・最大3取引を照会、最初の1証拠を保存。成功receipt・finalized高さ以下・正規ブロック収録・発行元Transferを照合。全履歴・他チェーン・所有者は未確認。')
