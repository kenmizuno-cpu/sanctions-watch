"""掲載関係の履歴。原本の消失は解除として処理しない。"""
import hashlib
import json

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def relation_id(row):
    return digest(['OFAC', 'SDN', row['party_id'], row['network'] or 'symbol:'+row['symbol'],
                   row['normalized_address']])

def reconcile(previous, incoming, now, source_hash, parser_version, *, previous_parser_version=None):
    old = {r['relation_id']:r for r in previous}
    if len(old) != len(previous): raise ValueError('旧台帳の掲載関係ID重複')
    new, events = {}, []
    # 消失した掲載関係は旧証跡の版を保持するため、処理済み版の判断に使わない。
    processed_version = previous_parser_version if previous_parser_version is not None else next(
        (r.get('parser_version') for r in previous if r.get('listing_status') == 'LISTED'), parser_version)
    upgrade = bool(previous) and processed_version != parser_version
    for raw in incoming:
        ident = relation_id(raw)
        if ident in new:
            new[ident]['evidence'].append({k:raw.get(k,'') for k in
                ('source_record_id','feature_id','version_id','evidence_locator','address')})
            continue
        before = old.get(ident)
        item = dict(raw, relation_id=ident, source_hash=source_hash, parser_version=parser_version,
            first_seen=before['first_seen'] if before else now, last_seen=now, listing_status='LISTED',
            last_event_id=before.get('last_event_id','') if before else '',
            evidence=[{k:raw.get(k,'') for k in ('source_record_id','feature_id','version_id','evidence_locator','address')}])
        if not before: kind = 'BASELINED' if not previous else ('BACKFILLED' if upgrade else 'ADDED')
        elif before['listing_status'] != 'LISTED': kind = 'RELISTED'
        elif any(before.get(k) != item.get(k) for k in ('entity_name','program','validation','review_reason')): kind='CHANGED'
        else: kind = ''
        if kind:
            event_id = digest([ident,item['last_event_id'],kind,source_hash,parser_version,
                               item['entity_name'], item['program'], item['validation']])
            events.append(dict(event_id=event_id, relation_id=ident, kind=kind, detected_at=now,
                party_id=item['party_id'], symbol=item['symbol'], network=item['network'],
                address=item['address'], entity_name=item['entity_name'], source_hash=source_hash,
                before_status=before['listing_status'] if before else '', after_status='LISTED'))
            item['last_event_id'] = event_id
        new[ident] = item
    for ident, before in old.items():
        if ident in new: continue
        item = dict(before)
        if before['listing_status'] == 'LISTED':
            event_id=digest([ident,before.get('last_event_id',''),'REMOVAL_CANDIDATE',source_hash])
            item.update(listing_status='REMOVAL_REVIEW', removal_seen=now, last_event_id=event_id)
            events.append(dict(event_id=event_id, relation_id=ident, kind='REMOVAL_CANDIDATE',
                detected_at=now, party_id=item['party_id'], symbol=item['symbol'], network=item['network'],
                address=item['address'], entity_name=item['entity_name'], source_hash=source_hash,
                before_status='LISTED', after_status='REMOVAL_REVIEW'))
        new[ident] = item
    return sorted(new.values(),key=lambda r:r['relation_id']),events
