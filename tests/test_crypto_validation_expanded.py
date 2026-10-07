"""公式仕様例と掲載原文の回帰。ネットワーク候補でIDを変更しない。"""
import unittest
from unittest.mock import patch
from src.crypto_validation import normalize_address
from src.crypto_ledger import relation_id, reconcile

class ExpandedTests(unittest.TestCase):
    def test_cashaddr_spec_and_prefixless_preserve_identity(self):
        for address in ['bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a',
                        'qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a',
                        'BITCOINCASH:QPM2QSZNHKS23Z7629MMS6S4CWEF74VCWVY22GDX6A',
                        '1BpEi6DfDAUFd7GtittLSdBeYJvcoaVggu']:
            with self.subTest(address=address):
                r=normalize_address('BCH',address)
                self.assertEqual(r['validation'],'CHECKSUM_VALID')
                self.assertEqual(r['network'],'')
                self.assertEqual(r['normalized_address'],address)
                self.assertEqual(r['network_candidates'],['bitcoin-cash'])
                self.assertEqual(r['review_category'],'')

    def test_cashaddr_checksum_only_vectors_and_mutations_fail(self):
        for address in ['bitcoincash:qpzry9x8gf2tvdw0s3jn54khce6mua7lcw20ayyn',
                        'bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6q',
                        'bitcoincash:Qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a',
                        'bchtest:pr6m7j9njldwwzlg9v7v53unlr4jkmx6eyvwc0uz5t']:
            with self.subTest(address=address):
                self.assertEqual(normalize_address('BCH',address)['validation'],'INVALID')

    def test_monero_official_standard_integrated_and_subaddress(self):
        for address in [
            '4AdUndXHHZ6cfufTMvppY6JwXNouMBzSkbLYfpAV5Usx3skxNgYeYTRj5UzqtReoS44qo9mtmXCqY45DJ852K5Jv2684Rge',
            '4LL9oSLmtpccfufTMvppY6JwXNouMBzSkbLYfpAV5Usx3skxNgYeYTRj5UzqtReoS44qo9mtmXCqY45DJ852K5Jv2bYXZKKQePHES9khPK',
            '87XWTP9tBECHXCwbyMK1KENoy4GeXkRviNzmhNc5Pvo5ExZ1QXjtRRyFeXPuHf7fNiS7KzBZNJeGweJhMbFB647171y66id']:
            with self.subTest(address=address):
                self.assertEqual(normalize_address('XMR',address)['validation'],'CHECKSUM_VALID')
                self.assertEqual(normalize_address('XMR',address[:-1]+'1')['validation'],'INVALID')

    def test_monero_hex_and_block_overflow_are_inconsistent_without_rewriting(self):
        for address in ['5be5543ff73456ab9f2d207887e2af87322c651ea1a873c5b25b7ffae456c320','z'*95]:
            r=normalize_address('XMR',address)
            self.assertEqual(r['validation'],'INVALID')
            self.assertEqual(r['review_category'],'INCONSISTENCY')
            self.assertEqual(r['normalized_address'],address)

    def test_monero_missing_dependency_fails_closed(self):
        with patch.dict('sys.modules',{'Crypto.Hash':None}):
            with self.assertRaisesRegex(ValueError,'検証依存'):
                normalize_address('XMR','4AdUndXHHZ6cfufTMvppY6JwXNouMBzSkbLYfpAV5Usx3skxNgYeYTRj5UzqtReoS44qo9mtmXCqY45DJ852K5Jv2684Rge')

    def test_solana_no_checksum_and_no_on_curve_requirement(self):
        for address in ['11111111111111111111111111111111','Fc1EwQUZyTEagaDvA1utHXCcZNyG1x2PLt2DfNu1cJdH']:
            r=normalize_address('SOL',address)
            self.assertEqual(r['validation'],'FORMAT_ONLY')
            self.assertEqual(r['review_category'],'LIMITATION')
        for address in ['1'*31,'0'*32,'z'*44]:
            self.assertEqual(normalize_address('SOL',address)['validation'],'INVALID')

    def test_other_published_currency_formats_and_checksum_mutations(self):
        pairs=[('XRP','rnXyVQzgxZe7TR1EPzTkGj2jxH4LMJYh66'),
               ('BSV','12sjrrhoFEsedNRhtgwvvRqjFTh8fZTDX9'),
               ('BTG','GPwg61XoHqQPNmAucFACuQ5H9sGCDv9TpS'),
               ('XVG','DFFJhnQNZf8rf67tYnesPu7MuGUpYtzv7Z'),
               ('ZEC','t1MMXtBrSp1XG38Lx9cePcNUCJj5vdWfUWL'),
               ('LTC','ltc1qr8ntsedq8tv0svmxqhzvdcdl5k7kntdmnhwep7'),
               ('BNB','bnb136ns6lfw4zs5hg4n85vdthaad7hq5m4gtkgf23')]
        for symbol,address in pairs:
            with self.subTest(symbol=symbol):
                self.assertEqual(normalize_address(symbol,address)['validation'],'CHECKSUM_VALID')
                self.assertEqual(normalize_address(symbol,address[:-1]+'2')['validation'],'INVALID')

    def test_unimplemented_address_types_remain_unsupported(self):
        for symbol,address in [('ZEC','u1future'),('ZEC','zs1future'),('ZEC','tex1s2rt77ggv6q989lr49rkgzmh5slsksa9khdgte'),('XRP','Xfuture'),('BNB','0x'+'a'*40),('LTC','ltcmweb1future'),('UNKNOWN','abc')]:
            self.assertEqual(normalize_address(symbol,address)['validation'],'UNSUPPORTED')

    def test_evm_symbols_reuse_checksum_but_preserve_legacy_identity(self):
        address='0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed'
        for symbol in ['ETC','BSC','ARB']:
            r=normalize_address(symbol,address)
            self.assertEqual(r['validation'],'CHECKSUM_VALID')
            self.assertEqual(r['normalized_address'],address.lower() if symbol=='ETC' else address)
            lower=normalize_address(symbol,address.lower())
            self.assertEqual(lower['validation'],'FORMAT_ONLY')
            self.assertEqual(lower['review_category'],'LIMITATION')

    def test_tokens_validate_families_without_resolving_actual_network(self):
        pairs=[('T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb',['tron-family']),
               ('1BpEi6DfDAUFd7GtittLSdBeYJvcoaVggu',['bitcoin-family']),
               ('mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn',['bitcoin-family']),
               ('0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed',['evm-family'])]
        for symbol in ['USDT','USDC']:
            for address,candidates in pairs:
                with self.subTest(symbol=symbol,address=address):
                    r=normalize_address(symbol,address)
                    self.assertEqual(r['validation'],'CHECKSUM_VALID')
                    self.assertEqual(r['network'],'')
                    self.assertEqual(r['normalized_address'],address)
                    self.assertEqual(r['network_candidates'],candidates)
                    self.assertEqual(r['network_resolution'],'FAMILY_ONLY')
                    self.assertEqual(r['review_category'],'LIMITATION')
        self.assertEqual(normalize_address('USDT','future:abc')['validation'],'UNSUPPORTED')
        self.assertEqual(normalize_address('USDT',pairs[0][0][:-1]+'1')['validation'],'INVALID')

    def test_token_prefix_is_not_enough_to_reject_solana(self):
        for symbol in ['USDT','USDC']:
            r=normalize_address(symbol,'1'*32)
            self.assertEqual(r['validation'],'FORMAT_ONLY')
            self.assertEqual(r['network_candidates'],['solana-family'])
            self.assertEqual(r['review_category'],'LIMITATION')

    def test_token_bitcoin_testnet_witness_is_only_a_family_candidate(self):
        address='tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq47zagq'
        r=normalize_address('USDT',address)
        self.assertEqual(r['validation'],'CHECKSUM_VALID')
        self.assertEqual(r['network_candidates'],['bitcoin-family'])
        self.assertEqual(r['network_resolution'],'FAMILY_ONLY')
        self.assertEqual(r['network'],'')
        self.assertEqual(normalize_address('XBT',address)['validation'],'INVALID')

    def test_token_known_32_byte_hex_type_remains_unsupported(self):
        address='0xbc33e6e4818f9f2ef77d020b35c24be738213e64d9e58839ee7b4222029610de'
        for symbol in ['USDT','USDC']:
            r=normalize_address(symbol,address)
            self.assertEqual(r['validation'],'UNSUPPORTED')
            self.assertEqual(r['review_category'],'UNSUPPORTED')
            self.assertEqual(r['normalized_address'],address)
            self.assertEqual(r['network'],'')
            self.assertEqual(r['network_candidates'],['hex32-family'])

    def test_label_mismatch_is_not_silently_relabelled(self):
        address='TUCsTq7TofTCJRRoHk6RvhMoS2mJLm5Yzq'
        r=normalize_address('XBT',address)
        self.assertEqual(r['validation'],'INVALID')
        self.assertEqual(r['network'],'bitcoin')
        self.assertEqual(r['network_candidates'],['tron-family'])
        self.assertEqual(r['review_category'],'INCONSISTENCY')
        self.assertIn('通貨記号',r['review_reason'])

    def test_added_candidates_do_not_create_official_events_or_new_ids(self):
        address='0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed'
        old=dict(party_id='42',symbol='USDT',network='',normalized_address=address,address=address,
                 entity_name='NAME',program='TEST',validation='UNSUPPORTED',review_reason='未対応',validation_version='2')
        rows,_=reconcile([],[old],'2026-10-07T00:00:00Z','a'*64,'1')
        incoming=dict(old,**normalize_address('USDT',address))
        updated,events=reconcile(rows,[incoming],'2026-10-07T01:00:00Z','a'*64,'1')
        self.assertEqual(updated[0]['relation_id'],relation_id(old))
        self.assertEqual(updated[0]['first_seen'],rows[0]['first_seen'])
        self.assertEqual(updated[0]['last_event_id'],rows[0]['last_event_id'])
        self.assertEqual([e['kind'] for e in events],['REVALIDATED'])
        self.assertEqual(reconcile(updated,[incoming],'2026-10-07T02:00:00Z','a'*64,'1')[1],[])
