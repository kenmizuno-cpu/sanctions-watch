"""OFACの明示アドレスを参照表から抽出する。制限判断は行わない。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

PARSER_VERSION = '1'
NAMESPACE = 'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML'
from .crypto_validation import normalize_address

class SchemaError(ValueError):
    pass

def local(element):
    return element.tag.rsplit('}', 1)[-1]

def _primary_name(party):
    for identity in party.iter():
        if local(identity) != 'Identity' or identity.get('False', 'false') == 'true':
            continue
        for alias in identity:
            if local(alias) != 'Alias' or alias.get('Primary', 'false') != 'true':
                continue
            for documented in alias:
                if local(documented) != 'DocumentedName': continue
                parts = [x.text or '' for x in documented.iter()
                         if local(x) == 'NamePartValue' and x.get('ScriptID') == '215']
                if parts: return ' '.join(parts)
    return ''

def extract(stream) -> tuple[list[dict], dict]:
    types, party_ids, profile_parties, programs = {}, set(), {}, {}
    rows, raw_count, feature_count = [], 0, 0
    checked = False
    try:
        for event, element in ET.iterparse(stream, events=('start', 'end')):
            tag = local(element)
            if not checked and event == 'start':
                checked = True
                if element.tag != '{'+NAMESPACE+'}Sanctions' or element.get('Version') != '3':
                    raise SchemaError('Advanced XMLの名前空間・版・構造変更')
            if event != 'end': continue
            if tag == 'FeatureType':
                ident = element.get('ID', '')
                label = ''.join(element.itertext()).strip()
                if not ident or ident in types or not label:
                    raise SchemaError('FeatureType参照表の欠損・重複')
                types[ident] = label
            elif tag == 'DistinctParty':
                party = element.get('FixedRef', '')
                if not party or party in party_ids: raise SchemaError('対象者IDの欠損・重複')
                party_ids.add(party)
                name = _primary_name(element)
                for profile in element:
                    if local(profile) != 'Profile': continue
                    profile_id = profile.get('ID', '')
                    if not profile_id or profile_id in profile_parties: raise SchemaError('Profile IDの欠損・重複')
                    profile_parties[profile_id] = party
                    for feature in profile:
                        if local(feature) != 'Feature': continue
                        type_id = feature.get('FeatureTypeID', '')
                        if type_id not in types: raise SchemaError('FeatureTypeID参照不能: '+type_id)
                        label = types[type_id]
                        if not label.startswith('Digital Currency Address'): continue
                        match = re.fullmatch(r'Digital Currency Address - ([A-Z0-9]+)', label)
                        if not match: raise SchemaError('暗号資産アドレス種別の構造変更: '+label)
                        symbol = match[1]
                        feature_id = feature.get('ID', '')
                        if not feature_id: raise SchemaError('Feature ID欠損')
                        feature_count += 1
                        found = 0
                        for version in feature:
                            if local(version) != 'FeatureVersion': continue
                            version_id = version.get('ID', '')
                            if not version_id: raise SchemaError('FeatureVersion ID欠損')
                            details = [x for x in version if local(x) == 'VersionDetail']
                            if not details: raise SchemaError('アドレス値欠損')
                            for index, detail in enumerate(details):
                                value = detail.text or ''
                                if list(detail) or not value: raise SchemaError('アドレス値の欠損・構造変更')
                                raw_count += 1; found += 1
                                rows.append(dict(party_id=party, profile_id=profile_id,
                                    feature_id=feature_id, version_id=version_id,
                                    source_record_id=f'{party}/{feature_id}/{version_id}/{index}',
                                    evidence_locator=f'DistinctParty[@FixedRef="{party}"]/Profile[@ID="{profile_id}"]/Feature[@ID="{feature_id}"]/FeatureVersion[@ID="{version_id}"]/VersionDetail[{index+1}]',
                                    entity_name=name, symbol=symbol, address=value,
                                    source='OFAC', list_name='SDN', source_type='OFFICIAL_SANCTIONS',
                                    **normalize_address(symbol,value)))
                        if not found: raise SchemaError('FeatureVersion欠損')
                element.clear()
            elif tag == 'SanctionsEntry':
                profile = element.get('ProfileID', '')
                if profile not in profile_parties: raise SchemaError('SanctionsEntryのProfileID参照不能')
                labels = [x.text.strip() for measure in element if local(measure)=='SanctionsMeasure'
                          for x in measure if local(x)=='Comment' and x.text and x.text.strip()]
                programs.setdefault(profile, set()).update(labels)
                element.clear()
    except ET.ParseError as exc:
        raise SchemaError('XML解析不能') from exc
    if not checked or not types or not rows: raise SchemaError('アドレス抽出0件・参照表欠損')
    for row in rows:
        row['program'] = ' / '.join(sorted(programs.get(row['profile_id'],set())))
    return rows, dict(raw_count=raw_count, feature_count=feature_count, party_count=len(party_ids),
                      parser_version=PARSER_VERSION)
