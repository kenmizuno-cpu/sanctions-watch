"""公開照会は形式検証・公式情報を変更せず、失敗と観測範囲を残す。"""
import copy
import unittest
from datetime import datetime, timezone
from importlib import util

NOW = datetime(2026, 10, 7, 8, tzinfo=timezone.utc)
ADDRESS = '0x8d79c73daae8630c88de372ba8f57592fa987607'

def row(symbol='ETH', address=ADDRESS):
    return dict(relation_id='a'*64, address=address, symbol=symbol, listing_status='LISTED',
                review_category='LIMITATION', validation='FORMAT_ONLY', review_reason='checksumなし',
                network_candidates=['evm-family'])

class ChainTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(util.find_spec('src.crypto_chain'), 'chain observations not implemented')
        from src import crypto_chain
        self.chain = crypto_chain

    def test_targets_do_not_change_official_information_and_scan_multiple_token_chains(self):
        r = row('USDC'); before = copy.deepcopy(r)
        targets = self.chain.targets(r)
        self.assertEqual({t.chain for t in targets}, {'ethereum','arbitrum','base'})
        self.assertEqual(r, before)
        self.assertEqual(self.chain.targets(row('XBT')), [])
        invalid = {**r, 'validation':'INVALID','review_category':'INCONSISTENCY'}
        self.assertEqual(self.chain.targets(invalid), [])

    def test_zero_is_not_invalid_and_provider_presence_disagreement_is_visible(self):
        probe = lambda present: dict(chain='ethereum',status='SUCCESS',last_success={'positive':present})
        self.assertEqual(self.chain.summarize([probe(False),probe(False)]),'NO_EVIDENCE')
        self.assertEqual(self.chain.summarize([probe(True),probe(False)]),'CONFLICT')
        self.assertEqual(self.chain.summarize([probe(True),{'chain':'ethereum','status':'FAILED'}]),'PARTIAL')
        self.assertEqual(self.chain.summarize([{'chain':'ethereum','status':'FAILED'}]),'FAILED')
        self.assertEqual(self.chain.summarize([probe(True),{**probe(False),'chain':'arbitrum'}]),'POSITIVE')

    def test_failed_refresh_keeps_old_success_without_claiming_current_success(self):
        old={'schema_version':1,'rows':[dict(relation_id='a'*64,address=ADDRESS,symbol='ETH',probes=[
            dict(chain='ethereum',provider='PublicNode',status='SUCCESS',attempted_at='2026-10-07T00:00:00Z',
                 last_success={'checked_at':'2026-10-07T00:00:00Z','positive':True,'balance_raw':'123'})]) ]}
        def fail(*args): raise ValueError('RPC error')
        result=self.chain.collect({'rows':[row()], 'source':{'sha256':'b'*64}},old,NOW,observe=fail)
        p=result['rows'][0]['probes'][0]
        self.assertEqual(p['status'],'FAILED')
        self.assertEqual(p['last_success']['balance_raw'],'123')
        self.assertEqual(p['last_success']['checked_at'],'2026-10-07T00:00:00Z')
        self.assertEqual(result['rows'][0]['state'],'FAILED')

    def test_success_cache_is_not_refetched_and_address_change_invalidates_cache(self):
        calls=[]
        def observe(t,p,a): calls.append(a);return {'positive':True,'balance_raw':'900719925474099312345'}
        snapshot={'rows':[row()], 'source':{'sha256':'b'*64}}
        first=self.chain.collect(snapshot,{},NOW,observe=observe)
        calls.clear()
        second=self.chain.collect(snapshot,first,NOW,observe=observe)
        self.assertEqual(calls,[])
        self.assertEqual(second['rows'][0]['probes'][0]['last_success']['balance_raw'],'900719925474099312345')
        snapshot['rows'][0]['address']='0x'+'1'*40
        self.chain.collect(snapshot,first,NOW,observe=observe)
        self.assertTrue(calls)

    def test_decimal_and_hex_parsers_reject_bool_negative_and_malformed_values(self):
        from src.crypto_chain.values import integer, quantity
        self.assertEqual(integer(900719925474099312345),'900719925474099312345')
        self.assertEqual(quantity('0x20000000000001'),'9007199254740993')
        for bad in [True,-1,1.5,'3']:
            with self.assertRaises(ValueError):integer(bad)
        for bad in ['0x','0X1','0xgg',None]:
            with self.assertRaises(ValueError):quantity(bad)

    def test_solana_failed_references_do_not_count_as_successful_transactions(self):
        from src.crypto_chain.solana import signatures
        result=signatures([{'signature':'1'*64,'slot':12,'err':{'error':1},'confirmationStatus':'finalized'}])
        self.assertFalse(result['positive'])
        self.assertEqual(result['failed_references'],1)
        result=signatures([{'signature':'1'*64,'slot':12,'err':None,'confirmationStatus':'finalized'}])
        self.assertTrue(result['positive'])
        with self.assertRaises(ValueError): signatures([{'signature':'bad'}])

    def test_bitcoin_activity_is_not_a_usdt_asset_match(self):
        from src.crypto_chain.bitcoin import account
        result=account({'address':'1CF46Rfbp97absrs7zb7dFfZS6qBXUm9EP',
            'chain_stats':{'tx_count':171,'funded_txo_sum':100,'spent_txo_sum':50}},'1CF46Rfbp97absrs7zb7dFfZS6qBXUm9EP')
        self.assertEqual(result['tx_count'],'171')
        self.assertFalse(result['asset_match'])
        self.assertIn('Omni',result['scope'])
        with self.assertRaises(ValueError): account({'address':'different'},'address')

    def test_token_logs_must_be_from_exact_issuer_contract_and_address(self):
        from src.crypto_chain.evm import token_logs
        contract='0x'+'2'*40; topic='0x'+'0'*24+ADDRESS[2:]
        transfer='0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
        log={'address':contract,'topics':[transfer,topic,'0x'+'0'*64],
             'transactionHash':'0x'+'3'*64,'blockNumber':'0x10','data':'0x'+'0'*63+'1','removed':False}
        self.assertEqual(token_logs([log],contract,ADDRESS,1,20),['0x'+'3'*64])
        for change in [{'address':'0x'+'4'*40},{'removed':True},{'blockNumber':'0x40'},
                       {'topics':[transfer,'0x'+'0'*64,'0x'+'0'*64]}]:
            with self.assertRaises(ValueError):token_logs([{**log,**change}],contract,ADDRESS,1,20)

if __name__=='__main__': unittest.main()

class AdapterTests(unittest.TestCase):
    def test_solana_mainnet_genesis_and_exact_large_balance(self):
        from src.crypto_chain.solana import observe
        from src.crypto_chain.registry import Target
        class HTTP:
            contexts={}
            def rpc(self,base,method,params):
                return {'getGenesisHash':'5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d',
                        'getBalance':{'value':900719925474099312345,'context':{'slot':123}},
                        'getSignaturesForAddress':[]}[method]
        v=observe(Target('solana'),'endpoint','address',HTTP())
        self.assertEqual(v['balance_raw'],'900719925474099312345')
        self.assertTrue(v['positive'])

    def test_budget_stops_before_network_and_rejects_external_endpoint(self):
        from src.crypto_chain.transport import Transport,Deferred
        http=Transport(max_calls=0)
        with self.assertRaises(Deferred):http.rpc('https://api.mainnet-beta.solana.com','getGenesisHash',[])
        with self.assertRaises(ValueError):http.request('https://attacker.invalid')

    def test_wrong_evm_chain_is_rejected_before_balance_observation(self):
        from src.crypto_chain.evm import observe
        from src.crypto_chain.registry import Target
        class HTTP:
            contexts={}
            def rpc(self,*args):return '0x38'
        with self.assertRaisesRegex(ValueError,'chainId'):
            observe(Target('ethereum'),'endpoint',ADDRESS,HTTP())

    def test_optional_history_failure_means_partial_observation(self):
        from src.crypto_chain import summarize
        self.assertEqual(summarize([{'chain':'ethereum','status':'SUCCESS',
            'last_success':{'positive':True,'history_error':'history unavailable'}}]),'PARTIAL')

    def test_tron_mainnet_and_abi_parameter_excludes_network_prefix(self):
        from src.crypto_chain.tron import observe
        from src.crypto_chain.registry import TRON_USDT
        class HTTP:
            contexts={}
            def request(self,base,path,payload):
                if path.endswith('getblockbynum'):
                    return {'blockID':'00000000000000001ebf88508a03865c71d452e25f4d51194196a1d22b6653dc'}
                if path.endswith('getnowblock'):
                    return {'blockID':'1'*64,'block_header':{'raw_data':{'number':1,'timestamp':2}}}
                if path.endswith('getaccount'):return {'address':payload['address'],'balance':0}
                assert len(payload['parameter'])==64
                assert payload['parameter'].startswith('0'*24)
                return {'result':{'result':True},'constant_result':['0'*63+'1']}
        v=observe(TRON_USDT,'endpoint','TJ812KESWjzJZGEWBPFCu74Js5zQS7jN5A',HTTP())
        self.assertTrue(v['asset_match']);self.assertEqual(v['token_balance_raw'],'1')
