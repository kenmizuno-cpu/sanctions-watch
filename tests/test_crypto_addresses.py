import io
import unittest

from src.crypto_addresses import extract, normalize_address, SchemaError

NS = 'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML'
ETH = '0x00000000000000000000000000000000000000Ab'

def xml(symbol='ETH', address=ETH, type_id='9001', party='42'):
    return f'''<Sanctions xmlns="{NS}" Version="3"><ReferenceValueSets>
      <FeatureType ID="{type_id}">Digital Currency Address - {symbol}</FeatureType>
      </ReferenceValueSets><DistinctParties><DistinctParty FixedRef="{party}">
      <Profile ID="8"><Identity Primary="true" False="false"><Alias Primary="true">
      <DocumentedName><DocumentedNamePart><NamePartValue ScriptID="215">EXAMPLE</NamePartValue>
      </DocumentedNamePart></DocumentedName></Alias></Identity>
      <Feature ID="77" FeatureTypeID="{type_id}"><FeatureVersion ID="78">
      <VersionDetail>{address}</VersionDetail></FeatureVersion></Feature></Profile>
      </DistinctParty></DistinctParties><SanctionsEntries><SanctionsEntry ProfileID="8">
      <SanctionsMeasure><Comment>TEST</Comment></SanctionsMeasure></SanctionsEntry>
      </SanctionsEntries></Sanctions>'''.encode()

class ExtractionTests(unittest.TestCase):
    def test_resolves_reference_labels_without_fixed_numeric_id(self):
        rows, report = extract(io.BytesIO(xml()))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['party_id'], '42')
        self.assertEqual(rows[0]['feature_id'], '77')
        self.assertEqual(rows[0]['address'], ETH)
        self.assertEqual(rows[0]['normalized_address'], ETH.lower())
        self.assertEqual(rows[0]['program'], 'TEST')
        self.assertEqual(report['raw_count'], 1)

    def test_unknown_network_preserves_case_and_requires_review(self):
        result = normalize_address('USDT', ETH)
        self.assertEqual(result['network'], '')
        self.assertEqual(result['normalized_address'], ETH)
        self.assertEqual(result['review_reason'], 'ネットワーク未確定・検証未対応')

    def test_tron_checksum_and_altered_character(self):
        good = normalize_address('TRX', 'T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb')
        bad = normalize_address('TRX', 'T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwa')
        self.assertEqual(good['validation'], 'CHECKSUM_VALID')
        self.assertEqual(bad['validation'], 'INVALID')

    def test_empty_address_fails_instead_of_zero_success(self):
        with self.assertRaises(SchemaError):
            extract(io.BytesIO(xml(address='')))

    def test_unresolved_feature_type_blocks_extraction(self):
        body = xml().replace(b'FeatureTypeID="9001"', b'FeatureTypeID="9999"')
        with self.assertRaises(SchemaError):
            extract(io.BytesIO(body))

    def test_duplicate_party_id_blocks_extraction(self):
        body = xml().decode()
        start = body.index('<DistinctParty ')
        end = body.index('</DistinctParty>') + len('</DistinctParty>')
        body = body[:end] + body[start:end] + body[end:]
        with self.assertRaises(SchemaError):
            extract(io.BytesIO(body.encode()))

    def test_schema_change_is_not_unchanged(self):
        with self.assertRaises(SchemaError):
            extract(io.BytesIO(xml().replace(b'Version="3"', b'Version="4"')))
