"""公式仕様の固定例で検証。誤記を正常へ変換せず、原文とIDを保持する。"""
import unittest
from unittest.mock import patch
from src.crypto_addresses import normalize_address

class AddressValidationTests(unittest.TestCase):
    def test_bip173_and_bip350_mainnet_addresses(self):
        vectors = [
            ('BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4', 'BECH32'),
            ('bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0', 'BECH32M'),
            ('BC1SW50QGDZ25J', 'BECH32M'),
        ]
        for address, method in vectors:
            with self.subTest(address=address):
                result = normalize_address('XBT', address)
                self.assertEqual(result['validation'], 'CHECKSUM_VALID')
                self.assertEqual(result['validation_method'], method)
                self.assertEqual(result['review_reason'], '')
                self.assertEqual(result['normalized_address'], address)

    def test_wrong_checksum_algorithm_and_program_and_padding_are_invalid(self):
        vectors = [
            'bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqh2y7hd',
            'bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh',
            'BC130XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ7ZWS8R',
            'bc1pw5dgrnzv',
            'BC1QR508D6QEJXTDG4Y5R3ZARVARYV98GJ9P',
            'bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v07qwwzcrf',
            'bc1gmk9yu',
        ]
        for address in vectors:
            with self.subTest(address=address):
                self.assertEqual(normalize_address('XBT', address)['validation'], 'INVALID')

    def test_bitcoin_testnet_and_mixed_case_are_not_mainnet_valid(self):
        for address in [
            'tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq47zagq',
            'bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5Jj0',
        ]:
            with self.subTest(address=address):
                self.assertEqual(normalize_address('XBT', address)['validation'], 'INVALID')

    def test_eip55_mixed_case_vectors(self):
        for address in [
            '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed',
            '0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359',
            '0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB',
            '0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb',
        ]:
            with self.subTest(address=address):
                result = normalize_address('ETH', address)
                self.assertEqual(result['validation'], 'CHECKSUM_VALID')
                self.assertEqual(result['validation_method'], 'EIP55')
                self.assertEqual(result['review_reason'], '')
                self.assertEqual(result['normalized_address'], address.lower())

    def test_wrong_mixed_case_is_invalid_without_rewriting_original(self):
        address = '0x5aaeb6053F3E94C9b9A09f33669435E7Ef1BeAed'
        result = normalize_address('ETH', address)
        self.assertEqual(result['validation'], 'INVALID')
        self.assertIn('チェックサム不一致', result['review_reason'])
        self.assertEqual(result['normalized_address'], address.lower())

    def test_uniform_case_does_not_claim_checksum_protection(self):
        for address in ['0xde709f2102306220921060314715629080e2fb77',
                        '0x52908400098527886E0F7030069857D2E4169EE7', '0x'+'0'*40]:
            with self.subTest(address=address):
                result = normalize_address('ETH', address)
                self.assertEqual(result['validation'], 'FORMAT_ONLY')
                self.assertIn('チェックサム情報なし', result['review_reason'])
                self.assertEqual(result['validation_method'], 'HEX20')

    def test_token_network_is_never_resolved_from_evm_shape(self):
        address='0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed'
        result=normalize_address('USDT', address)
        self.assertEqual(result['network'], '')
        self.assertEqual(result['validation'], 'CHECKSUM_VALID')
        self.assertEqual(result['normalized_address'], address)
        self.assertEqual(result['validation_method'], 'EIP55')
        self.assertEqual(result['network_resolution'], 'FAMILY_ONLY')

    def test_whitespace_and_wrong_length_fail_before_checksums(self):
        for address in ['', ' 0x'+'0'*40, '0x'+'0'*39, '0x'+'0'*39+'z']:
            with self.subTest(address=address):
                self.assertEqual(normalize_address('ETH',address)['validation'], 'INVALID')

    def test_legacy_base58_checksum_is_preserved_with_method(self):
        result=normalize_address('TRX','T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb')
        self.assertEqual(result['validation'], 'CHECKSUM_VALID')
        self.assertEqual(result['validation_method'], 'BASE58CHECK')
        self.assertTrue(result['validation_detail'])
        self.assertEqual(result['validation_version'], '3')

    def test_missing_keccak_dependency_fails_instead_of_claiming_validation(self):
        with patch.dict('sys.modules',{'Crypto.Hash':None}):
            with self.assertRaisesRegex(ValueError,'Ethereum検証依存が未設定'):
                normalize_address('ETH','0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed')
