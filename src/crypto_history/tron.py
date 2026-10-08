"""TRON history candidates verified by successful SolidityNode receipts and blocks."""
import re
from ..crypto_chain.tron import GENESIS
from ..crypto_chain.values import integer
from .registry import INDEX, RECEIPTS
from .events import transfer, verify_candidates, Rejected

def context(http,base):
    key=('history-tron',base)
    if key not in http.contexts:
        if http.request(base,'/walletsolidity/getblockbynum',{'num':0}).get('blockID')!=GENESIS:
            raise ValueError('wrong TRON genesis')
        b=http.request(base,'/walletsolidity/getnowblock',{});raw=b['block_header']['raw_data']
        if not re.fullmatch(r'[0-9a-f]{64}',b.get('blockID','')):raise ValueError('invalid solid block')
        http.contexts[key]=(int(integer(raw['number'])),int(integer(raw['timestamp'])))
    return http.contexts[key]

def prove(t,address,tx,http,base):
    solid,stamp=context(http,base)
    r=http.request(base,'/walletsolidity/gettransactioninfobyid',{'value':tx})
    if not isinstance(r,dict) or r.get('id')!=tx or r.get('receipt',{}).get('result')!='SUCCESS':raise Rejected('receipt not successful')
    height=integer(r['blockNumber']);timestamp=integer(r['blockTimeStamp'])
    if int(height)>solid or int(timestamp)>stamp:raise Rejected('receipt not solidified')
    b=http.request(base,'/walletsolidity/getblockbynum',{'num':int(height)})
    raw=b['block_header']['raw_data'];block_hash=b.get('blockID','')
    if (integer(raw['number'])!=height or integer(raw['timestamp'])!=timestamp or
        not re.fullmatch(r'[0-9a-f]{64}',block_hash) or not isinstance(b.get('transactions'),list) or
        tx not in [x.get('txID') for x in b['transactions'] if isinstance(x,dict)]):raise Rejected('solid block mismatch')
    logs=r.get('log')
    if not isinstance(logs,list) or len(logs)>1000:raise Rejected('invalid receipt logs')
    for i,log in enumerate(logs):
        event=transfer(log,t.contract,address,tron=True)
        if event:
            return {**event,'tx_hash':tx,'block_height':height,'block_hash':block_hash,
                    'block_timestamp':timestamp,'log_index':str(i),'confirmation':'solidified'}
    raise Rejected('issuer Transfer not found')

def observe(t,address,http):
    errors=[]
    for _,base in RECEIPTS['tron']:
        try:_,solid_timestamp=context(http,base);break
        except Exception as e:errors.append(e)
    else:raise errors[-1]
    _,base=INDEX['tron']
    path=('/v1/accounts/'+address+'/transactions/trc20?only_confirmed=true&limit=20&contract_address='+
          t.contract+'&order_by=block_timestamp,desc&max_timestamp='+str(solid_timestamp))
    data=http.request(base,path)
    if (not isinstance(data,dict) or data.get('success') is not True or not isinstance(data.get('data'),list)
        or len(data['data'])>20 or not isinstance(data.get('meta'),dict)):raise ValueError('invalid TronGrid index')
    candidates=[]
    for item in data['data']:
        if not isinstance(item,dict):raise ValueError('invalid index item')
        if item.get('type')!='Transfer' or item.get('token_info',{}).get('address')!=t.contract:continue
        if address not in (item.get('from'),item.get('to')):continue
        tx=item.get('transaction_id','')
        if not isinstance(tx,str) or not re.fullmatch(r'[0-9a-f]{64}',tx):raise ValueError('invalid transaction hash')
        candidates.append(tx)
    return verify_candidates(candidates,bool(data['meta'].get('fingerprint')),RECEIPTS['tron'],
        lambda tx,endpoint:prove(t,address,tx,http,endpoint),
        'TRONの確認済み履歴索引先頭最大20件・最大3取引を照会、最初の1証拠を保存。固化済み成功receipt・ブロック収録・発行元Transferを照合。全履歴・所有者は未確認。')
