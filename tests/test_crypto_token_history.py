"""Historical token evidence must survive adversarial index/receipt responses."""
import copy
import unittest
from importlib.util import find_spec
from src.crypto_chain.registry import TOKENS, TRON_USDT

ETH_ADDRESS='0xb6f5ec1a0a9cd1526536d3f0426c429529471f40'
TRON_ADDRESS='TTct1DezYvriNWU7Wi3mygLoskkaw61mra'
TX='1'*64
BLOCK='2'*64
TRANSFER='ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

def eth_log():
    return {'address':TOKENS['USDT'][0].contract,'topics':['0x'+TRANSFER,'0x'+'0'*24+ETH_ADDRESS[2:],'0x'+'0'*64],
            'data':'0x'+'0'*63+'1','transactionHash':'0x'+TX,'blockHash':'0x'+BLOCK,
            'blockNumber':'0x10','logIndex':'0x0','removed':False}

def tron_log():
    from src.crypto_validation.common import decode_base58
    return {'address':decode_base58(TRON_USDT.contract)[1:-4].hex(),
            'topics':[TRANSFER,'0'*24+decode_base58(TRON_ADDRESS)[1:-4].hex(),'0'*64],
            'data':'0'*63+'1'}

class FixtureHTTP:
    def __init__(self,chain='ethereum'):
        self.chain=chain; self.contexts={};self.calls=0;self.paths=[]
        self.index={'items':[{'transaction_hash':'0x'+TX,'token':{'address_hash':TOKENS['USDT'][0].contract}}], 'next_page_params':None}
        self.receipt={'transactionHash':'0x'+TX,'blockHash':'0x'+BLOCK,'blockNumber':'0x10','status':'0x1','logs':[eth_log()]}
        self.block={'number':'0x10','hash':'0x'+BLOCK,'timestamp':'0x100','transactions':['0x'+TX]}
        if chain=='tron':
            self.index={'success':True,'data':[{'transaction_id':TX,'type':'Transfer','token_info':{'address':TRON_USDT.contract},'from':TRON_ADDRESS,'to':TRON_ADDRESS,'value':'1'}],'meta':{'fingerprint':'opaque','page_size':1}}
            self.receipt={'id':TX,'blockNumber':16,'blockTimeStamp':256000,'receipt':{'result':'SUCCESS'},'log':[tron_log()]}
            self.block={'blockID':BLOCK,'block_header':{'raw_data':{'number':16,'timestamp':256000}},'transactions':[{'txID':TX}]}
        self.fail_receipt=False;self.wrong_network=False
    def request(self,base,path='',payload=None,validate=None):
        self.calls+=1;self.paths.append(base+path)
        if path.startswith('/v1/') or path.startswith('/api/v2/'):return copy.deepcopy(self.index)
        if path.endswith('getblockbynum'):
            if payload['num']==0:
                from src.crypto_chain.tron import GENESIS
                return {'blockID':'bad' if self.wrong_network else GENESIS}
            return copy.deepcopy(self.block)
        if path.endswith('getnowblock'):return {'blockID':'3'*64,'block_header':{'raw_data':{'number':100,'timestamp':512000}}}
        if path.endswith('gettransactioninfobyid'):
            if self.fail_receipt:raise OSError('secret remote failure')
            return copy.deepcopy(self.receipt)
        raise AssertionError(path)
    def rpc(self,base,method,params):
        self.calls+=1
        if method=='eth_chainId':return '0x2' if self.wrong_network else '0x1'
        if method=='eth_getBlockByNumber':
            return {'number':'0x64','hash':'0x'+'3'*64,'timestamp':'0x200'} if params[0]=='finalized' else copy.deepcopy(self.block)
        if method=='eth_getTransactionReceipt':
            if self.fail_receipt:raise OSError('secret remote failure')
            return copy.deepcopy(self.receipt)
        raise AssertionError(method)

class HistoryAdapterTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(find_spec('src.crypto_history'),'historical verification missing')
    def observe(self,h):
        from src.crypto_history import evm,tron
        return (tron if h.chain=='tron' else evm).observe(TRON_USDT if h.chain=='tron' else TOKENS['USDT'][0],TRON_ADDRESS if h.chain=='tron' else ETH_ADDRESS,h)
    def test_success_receipt_proves_exact_issuer_transfer_in_confirmed_block(self):
        for chain in ['ethereum','tron']:
            v=self.observe(FixtureHTTP(chain))
            self.assertEqual(v['state'],'VERIFIED');self.assertEqual(len(v['proofs']),1)
            self.assertEqual(v['proofs'][0]['amount_raw'],'1');self.assertEqual(v['proofs'][0]['block_height'],'16')
    def test_wrong_contract_unrelated_address_zero_and_bad_padding_are_not_proofs(self):
        for chain in ['ethereum','tron']:
            for change in ['contract','address','zero','padding']:
                h=FixtureHTTP(chain);log=h.receipt['log' if chain=='tron' else 'logs'][0];prefix='' if chain=='tron' else '0x'
                if change=='contract':log['address']=prefix+'4'*40
                if change=='address':log['topics'][1]=prefix+'0'*64
                if change=='zero':log['data']=prefix+'0'*64
                if change=='padding':log['topics'][1]=prefix+'f'*24+log['topics'][1][-40:]
                v=self.observe(h);self.assertNotEqual(v['state'],'VERIFIED');self.assertEqual(v['proofs'],[])
    def test_failed_or_unconfirmed_receipts_and_missing_tx_block_are_rejected(self):
        for chain in ['ethereum','tron']:
            for change in ['failed','height','id','membership']:
                h=FixtureHTTP(chain)
                if change=='failed':
                    if chain=='tron':h.receipt['receipt']['result']='REVERT'
                    else:h.receipt['status']='0x0'
                if change=='height':h.receipt['blockNumber']=101 if chain=='tron' else '0x65'
                if change=='id':h.receipt['id' if chain=='tron' else 'transactionHash']=('' if chain=='tron' else '0x')+'4'*64
                if change=='membership':h.block['transactions']=[]
                self.assertNotEqual(self.observe(h)['state'],'VERIFIED')
    def test_evm_reorg_and_log_receipt_mismatch_are_rejected(self):
        for change in [{'blockHash':'0x'+'4'*64},{'removed':True},{'transactionHash':'0x'+'4'*64},{'blockNumber':'0x11'}]:
            h=FixtureHTTP();h.receipt['logs'][0].update(change)
            self.assertNotEqual(self.observe(h)['state'],'VERIFIED')
        h=FixtureHTTP();h.block['hash']='0x'+'4'*64
        self.assertNotEqual(self.observe(h)['state'],'VERIFIED')
    def test_mainnet_identity_required_and_index_name_is_not_trusted(self):
        for chain in ['ethereum','tron']:
            h=FixtureHTTP(chain);h.wrong_network=True
            with self.assertRaises(ValueError):self.observe(h)
            h=FixtureHTTP(chain)
            if chain=='tron':h.index['data'][0]['token_info']['address']='wrong'
            else:h.index['items'][0]['token']['address_hash']='0x'+'4'*40
            self.assertNotEqual(self.observe(h)['state'],'VERIFIED')
    def test_receipt_unavailable_is_partial_not_absence_and_no_remote_error_leaks(self):
        for chain in ['ethereum','tron']:
            h=FixtureHTTP(chain);h.fail_receipt=True;v=self.observe(h)
            self.assertEqual(v['state'],'PARTIAL');self.assertEqual(v['proofs'],[])
            self.assertNotIn('secret',str(v));self.assertEqual(v['checked_candidates'],'1')
    def test_empty_index_is_no_proof_and_api_error_is_not_empty_index(self):
        for chain in ['ethereum','tron']:
            h=FixtureHTTP(chain);h.index['data' if chain=='tron' else 'items']=[]
            self.assertEqual(self.observe(h)['state'],'NO_PROOF')
            h.index={}
            with self.assertRaises(ValueError):self.observe(h)
    def test_candidate_limit_and_provider_urls_not_followed(self):
        h=FixtureHTTP('tron');item=h.index['data'][0]
        h.index['data']=[{**item,'transaction_id':str(i)*64} for i in range(1,5)]
        h.index['meta']['links']={'next':'https://evil.invalid'};h.fail_receipt=True
        v=self.observe(h);self.assertEqual(v['checked_candidates'],'3');self.assertTrue(v['has_more'])
        self.assertFalse(any('evil' in x for x in h.paths))
    def test_transport_budget_and_external_endpoint_rejected(self):
        from src.crypto_history.transport import HistoryTransport
        from src.crypto_chain.transport import Deferred
        h=HistoryTransport(max_calls=0)
        with self.assertRaises(Deferred):h.request('https://eth.blockscout.com','/api/v2/')
        with self.assertRaises(ValueError):h.request('https://evil.invalid')
    def test_malformed_finalized_block_is_not_cached_for_later_proofs(self):
        from src.crypto_history.evm import context
        h=FixtureHTTP();rpc=h.rpc
        def bad(base,method,params):
            value=rpc(base,method,params)
            if method=='eth_getBlockByNumber' and params[0]=='finalized':value['hash']='bad'
            return value
        h.rpc=bad
        with self.assertRaises(ValueError):context(h,'endpoint')
        self.assertNotIn(('history-eth','endpoint'),h.contexts)

class HistoryRunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(find_spec('src.crypto_history.runner'),'history publication missing')
        from src.crypto_history.runner import collect
        self.collect=collect
        from datetime import datetime,timezone
        self.now=datetime(2026,10,8,1,tzinfo=timezone.utc)
        self.snapshot={'status':'SUCCESS','source':{'sha256':'a'*64},'rows':[{
            'relation_id':'b'*64,'address':ETH_ADDRESS,'symbol':'USDT','listing_status':'LISTED',
            'review_category':'LIMITATION','validation':'FORMAT_ONLY'}]}
        self.calls=[]
    def observe(self,t,a,h):
        self.calls.append(a)
        return {'state':'NO_PROOF','proofs':[],'candidates_count':'0','checked_candidates':'0',
                'unavailable_candidates':'0','rejected_candidates':'0','has_more':False,'scope':'bounded'}
    def test_success_cached_and_registry_contract_or_identity_change_invalidates(self):
        p=self.collect(self.snapshot,{},self.now,observe=self.observe);self.calls.clear()
        self.collect(self.snapshot,p,self.now,observe=self.observe);self.assertEqual(self.calls,[])
        p['rows'][0]['contract']='wrong'
        self.collect(self.snapshot,p,self.now,observe=self.observe);self.assertEqual(len(self.calls),1)
        p=self.collect(self.snapshot,{},self.now,observe=self.observe);self.calls.clear()
        self.snapshot['rows'][0]['address']='0x'+'3'*40
        self.collect(self.snapshot,p,self.now,observe=self.observe);self.assertEqual(len(self.calls),1)
    def test_failed_refresh_preserves_old_success_and_failure_is_not_fresh_evidence(self):
        from datetime import timedelta
        p=self.collect(self.snapshot,{},self.now,observe=self.observe)
        def fail(*a):raise OSError('remote secret text')
        q=self.collect(self.snapshot,p,self.now+timedelta(hours=7),observe=fail)
        self.assertEqual(q['rows'][0]['status'],'FAILED')
        self.assertEqual(q['rows'][0]['last_success'],p['rows'][0]['last_success'])
        self.assertEqual(q['counts']['FAILED'],1);self.assertEqual(q['counts'].get('NO_PROOF',0),0)
        self.assertNotIn('secret',str(q))
        self.calls.clear();self.collect(self.snapshot,q,self.now+timedelta(hours=7,minutes=5),observe=self.observe)
        self.assertEqual(self.calls,[])
    def test_partial_receipt_results_retry_after_one_hour_not_six(self):
        from datetime import timedelta
        def partial(*a):return {**self.observe(*a),'state':'PARTIAL','unavailable_candidates':'1'}
        p=self.collect(self.snapshot,{},self.now,observe=partial);self.calls.clear()
        self.collect(self.snapshot,p,self.now+timedelta(minutes=30),observe=self.observe);self.assertEqual(self.calls,[])
        self.collect(self.snapshot,p,self.now+timedelta(hours=1,seconds=1),observe=self.observe);self.assertEqual(len(self.calls),1)
    def test_budget_deferred_and_unsupported_rows_do_not_touch_official_data(self):
        from src.crypto_history.transport import HistoryTransport
        original=copy.deepcopy(self.snapshot)
        p=self.collect(self.snapshot,{},self.now,http=HistoryTransport(max_calls=0))
        self.assertEqual(p['rows'][0]['status'],'DEFERRED');self.assertEqual(self.snapshot,original)
        self.snapshot['rows'][0]['symbol']='ETH'
        self.assertEqual(self.collect(self.snapshot,{},self.now,observe=self.observe)['rows'],[])
    def test_cli_skips_failed_extraction_and_atomically_writes_independent_json(self):
        import tempfile,json
        from pathlib import Path
        from unittest.mock import patch
        from src.crypto_token_history_watch import run
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'data/crypto';folder.mkdir(parents=True)
            source=folder/'dashboard.json';target=folder/'token_history.json'
            source.write_text(json.dumps({**self.snapshot,'status':'FAILED'}));target.write_text('old')
            with self.assertRaises(ValueError):run(root)
            self.assertEqual(target.read_text(),'old')
            source.write_text(json.dumps(self.snapshot));target.unlink()
            result=self.collect(self.snapshot,{},self.now,observe=self.observe)
            with patch('src.crypto_token_history_watch.collect',return_value=result):run(root)
            self.assertEqual(json.loads(target.read_text())['counts']['NO_PROOF'],1)
            self.assertEqual(json.loads(source.read_text()),self.snapshot)

