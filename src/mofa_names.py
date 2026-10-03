"""Phase 3B: offline, page-backed MOFA name candidates. Never writes master.

Only the two current terrorism list formats are supported. Press attachments
remain blocked until a format-specific parser is added and reviewed.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import unicodedata
from pathlib import Path

import pdfplumber

from . import mofa_documents as D
from .fetch import read_raw, sha256
from .persistence import FileWrite, atomic_replace_many

PARSER_VERSION = 'mofa-names-v1'
OUTPUT = 'data/mofa/extraction'
RECORD_COLS = ['source_record_id', 'document_key', 'document_number', 'section',
               'source_external_id', 'party_type', 'current', 'pages', 'source_hash',
               'source_url', 'source_page_url', 'raw_path', 'evidence_text',
               'parse_status', 'review_status', 'applied', 'parser_version']
NAME_COLS = ['name_id', 'source_record_id', 'document_key', 'document_number',
             'name', 'name_kind', 'language', 'pages', 'source_hash', 'source_url',
             'source_page_url', 'raw_path', 'evidence_text', 'evidence_lines',
             'parse_status', 'screening_eligible', 'review_status', 'applied', 'parser_version']
ISSUE_COLS = ['document_key', 'source_record_id', 'pages', 'code', 'detail', 'source_hash']
HEADER = re.compile(r'^\s*(?:\(重複\)\s*)?(\d{1,4})\s*\.\s*(.*)$')
SECTION = re.compile(r'^(I{1,3}|IV|V|VI{0,3}|IX|X)\s+.*(?:告示|公告)')
ALIAS = re.compile(r'(確定に十分でない別\s*名|別\s*名|別\s*称|旧\s*称|以前\s*の呼称|[af]\s*\.\s*k\s*\.\s*a\s*\.)(?:\s*:|(?<=\.)|\s+)', re.I)
SCRIPT = re.compile(r'\(original\s+script\s*:\s*(.*?)\)|\(original\s+script\)\s*:\s*(.*?)(?=\(\s*[af]\s*\.\s*k\s*\.\s*a\s*\.|$)', re.I | re.S)
RAW_SCRIPT = re.compile(r'[（(]original\s+script\s*[:：]\s*(.*?)[)）]|[（(]original\s+script[)）]\s*[:：]\s*(.*?)(?=[（(]\s*[af]\s*\.\s*k\s*\.\s*a\s*\.|$)', re.I | re.S)
INLINE_FIELD = re.compile(r'(?:国籍|生年月日|出生地|旅券番号|身分登録番号|住所等|所在地|称号|役職)\s*:')
LABEL = re.compile(r'^\s*(?:\([a-z0-9]+\)|[a-z]\))\s*', re.I)
ENUM = re.compile(r'\s*\(\s*[a-z0-9]{1,2}\s*\)\s*', re.I)
UNKNOWN = {'不明', 'なし', '無し', 'unknown', 'na', 'n/a', '-'}


def _norm(value):
    return unicodedata.normalize('NFKC', value)


def _field(line):
    # Explicit metadata starts delimit names; aliases and original script do not.
    line = line.strip()
    return bool(re.match(r'^(?:国連参照番号|称号|役職[^:]*|生年月日|出生地|国籍|旅券番号|ID\s*番号|住所[^:]*|所在地|リスト掲載日|国連制裁委員会による指定日|その他の情報|身分登録番号|性別)\s*(?::|$)', line))


def _join(value):
    # PDF soft wraps: Japanese joins directly; Latin words keep a space.
    value = re.sub(r'([\u3000-\u9fff])\s*\n\s*(?=[\u3000-\u9fff])', r'\1', value)
    return re.sub(r'\s+', ' ', value).strip()


def _latin_start(text):
    for match in re.finditer(r"(?m)^[‘’'\"`]?\s*[A-Za-z][^\n]*", text):
        if not re.search(r'[\u3040-\u30ff\u4e00-\u9fff]', match[0]):
            return match
    return None


def _trim(value):
    return _join(value).strip(' ;\t\n')


def _top_level_delimiters(value, pattern):
    depth, found = 0, []
    at = 0
    while at < len(value):
        match = pattern.match(value, at) if depth == 0 else None
        if match:
            found.append(match)
            at = match.end()
            continue
        if value[at] == '(':
            depth += 1
        elif value[at] == ')':
            depth = max(0, depth-1)
        at += 1
    return found


def _alias_end(text, start):
    """For an inline alias inside (...), stop at its enclosing close."""
    prefix = text[:start]
    if prefix.rstrip().endswith('('):
        depth = 1
        for i in range(start, len(text)):
            if text[i] == '(':
                depth += 1
            elif text[i] == ')':
                depth -= 1
                if depth == 0:
                    return i
    return len(text)


def parse_pages(pages, *, document_key, source_hash, source_url, raw_path):
    if document_key not in {'current_un', 'current_1373'}:
        raise ValueError('unsupported document format')
    if not re.fullmatch('[a-f0-9]{64}', source_hash):
        raise ValueError('invalid source hash')
    lines = []
    for page, text in enumerate(pages, 1):
        for line, raw in enumerate(text.splitlines(), 1):
            lines.append({'page': page, 'line': line, 'raw': raw, 'text': _norm(raw).strip()})
    records, names, issues = [], [], []
    blocks = []
    section = 'I' if document_key == 'current_1373' else 'UN'
    current = None

    def issue(code, detail, block=None):
        issues.append(dict(document_key=document_key, source_record_id=block.get('id', '') if block else '',
                           pages=';'.join(map(str, sorted({x['page'] for x in block['lines']}))) if block else '',
                           code=code, detail=detail, source_hash=source_hash))

    for row in lines:
        text = row['text']
        sec = SECTION.match(text)
        if sec and document_key == 'current_1373':
            section = sec[1]
            current = None
            continue
        if re.match(r'^【(?:以下)?(?:[0-9]|平成|令和|昭和)', text) or text in {'(了)', '(注)'}:
            current = None
            continue
        match = HEADER.match(text)
        if match and not re.match(r'^(?:に|の|と|及び|および|で|を|から|、|\))', match[2]):
            current = {'number': match[1], 'section': section, 'head': match[2], 'lines': [row]}
            current['id'] = f'{source_hash}:{section}:{match[1]}'
            blocks.append(current)
        elif current is not None:
            current['lines'].append(row)

    for number, page_text in enumerate(pages, 1):
        if len(page_text.strip()) < 20:
            issue('IMAGE_OR_EMPTY_PAGE', f'PDF page {number}: テキストがない・極端に短い。原本確認が必要。')
    if not blocks:
        issue('NO_RECORDS', '番号付き対象レコードが抽出できない。画像PDFまたは資料形式変更の可能性。')
    seen = set()
    previous = {}
    for block in blocks:
        rid, number = block['id'], block['number']
        if rid in seen:
            issue('DUPLICATE_NUMBER', '同じセクションの資料内番号が重複', block)
        seen.add(rid)
        expected = previous.get(block['section'], 0) + 1
        if int(number) != expected:
            issue('NUMBER_SEQUENCE', f'資料内番号の連続性: expected={expected} actual={number}', block)
        previous[block['section']] = int(number)
        rows = block['lines']
        # Remove blank lines, retaining page/line positions for evidence.
        rows = [r for r in rows if r['text']]
        text = '\n'.join(r['text'] for r in rows)
        all_pages = ';'.join(map(str, sorted({r['page'] for r in rows})))
        deleted = bool(re.match(r'^(?:\(\(重複\).*?\)\s*)?(?:※\s*)?(?:\d.*?解除|削除|解除)', block['head']))
        ext = re.search(r'国連参照番号\s*:\s*([A-Za-z]{2}[ie]\.\d+)', text)
        external = ext[1] if ext else ''
        person = bool(re.search(r'^生年月日\s*:', text, re.M)) or bool(re.search(r'\b[A-Z]{2}i\.\d+', external)) or 'View-UN-Notices-Individuals' in text
        entity = bool(re.search(r'^所在地\s*(?::|$)', text, re.M)) or bool(re.search(r'\b[A-Z]{2}e\.\d+', external)) or 'View-UN-Notices-Entities' in text
        kind = 'person' if person and not entity else 'entity' if entity and not person else 'unknown'
        if not deleted and kind == 'unknown':
            issue('PARTY_TYPE_UNKNOWN', '人物・団体の明示的根拠が不足または矛盾', block)
        record = dict(source_record_id=rid, document_key=document_key, document_number=number,
                      section=block['section'], source_external_id=external, party_type=kind,
                      current='false' if deleted else 'true', pages=all_pages, source_hash=source_hash,
                      source_url=source_url, source_page_url=source_url+'#page='+str(rows[0]['page']),
                      raw_path=raw_path, evidence_text='\n'.join(r['raw'] for r in rows),
                      parse_status='EXTRACTED', review_status='REVIEW_REQUIRED', applied='false', parser_version=PARSER_VERSION)
        records.append(record)
        if deleted:
            record['parse_status'] = 'HISTORICAL'
            continue
        # Character offsets in normalized text map back to original page lines.
        spans = []
        offset = 0
        for r in rows:
            spans.append((offset, offset+len(r['text']), r))
            offset += len(r['text'])+1

        def emit(value, name_kind, language, start, end, uncertain=False):
            value = _trim(value)
            if not value or value.casefold() in UNKNOWN:
                return
            evidence = [r for a,b,r in spans if a < end and b >= start]
            if not evidence:
                raise ValueError('name has no page evidence')
            page_numbers = sorted({r['page'] for r in evidence})
            page_set = ';'.join(map(str, page_numbers))
            identity = json.dumps([PARSER_VERSION, rid, name_kind, language, value, start, end], ensure_ascii=False)
            row = dict(name_id=sha256(identity.encode()), source_record_id=rid, document_key=document_key,
                       document_number=number, name=value, name_kind=name_kind, language=language,
                       pages=page_set, source_hash=source_hash, source_url=source_url,
                       source_page_url=source_url+'#page='+str(page_numbers[0]), raw_path=raw_path,
                       evidence_text='\n'.join(r['raw'] for r in evidence),
                       evidence_lines=json.dumps([{'page':r['page'],'line':r['line']} for r in evidence], separators=(',', ':')),
                       parse_status='REVIEW_REQUIRED' if uncertain else 'EXTRACTED',
                       screening_eligible='false', review_status='REVIEW_REQUIRED', applied='false', parser_version=PARSER_VERSION)
            names.append(row)
            if uncertain:
                issue('RTL_ORDER_REVIEW' if name_kind=='original_script' else 'NAME_AMBIGUOUS',
                      ('右から左の文字順を原本ページで確認: ' if name_kind=='original_script' else '注記・括弧・姓名境界の確認が必要: ')+value, block)

        # Heading area stops at the first metadata field, and is split by language.
        boundary = next((a for a,b,r in spans[1:] if _field(r['text'])), len(text))
        top = text[:boundary]
        head = HEADER.match(rows[0]['text'])
        start = head.start(2)
        duplicate = re.match(r'\(\(重複\).*?\)\s*', top[start:])
        if duplicate:
            start += duplicate.end()
        latin = _latin_start(top[start:])
        latin_start = start+latin.start() if latin else len(top)
        ja_end = latin_start
        ja_alias = ALIAS.search(top, start, ja_end)
        if ja_alias:
            ja_end = ja_alias.start()
            if top[:ja_end].rstrip().endswith('('):
                ja_end = len(top[:ja_end].rstrip())-1
            alias_close = _alias_end(top, ja_alias.start())
            if alias_close < latin_start or (latin and latin[0].count(')')>latin[0].count('(')):
                latin = _latin_start(top[alias_close+1:])
                latin_start = alias_close+1+latin.start() if latin else len(top)
            elif latin:
                issue('UNBALANCED_ALIAS_WRAPPER', '別名欄の閉じ括弧と英字主名称の境界を原本確認', block)
        emit(top[start:ja_end], 'primary', 'ja', start, ja_end,
             uncertain=bool(re.search(r'[:]|\b\d{4}\b', top[start:ja_end])))
        if latin:
            stop = len(top)
            markers = [m.start() for m in ALIAS.finditer(top, latin_start)]
            script = SCRIPT.search(top, latin_start)
            if script:
                markers.append(script.start())
            if markers:
                stop = min(markers)
                if top[:stop].rstrip().endswith('('):
                    stop = len(top[:stop].rstrip())-1
            emit(top[latin_start:stop], 'primary', 'en', latin_start, stop,
                 uncertain=bool(re.search(r'[:]|\n\s*(?:別|旧)', top[latin_start:stop])))
        scripts = list(SCRIPT.finditer(top))
        raw_top = '\n'.join(r['raw'] for a,b,r in spans if a < boundary)
        raw_scripts = list(RAW_SCRIPT.finditer(raw_top))
        for index,match in enumerate(scripts):
            # Pair occurrences across the whole heading, never the first match on a line.
            raw_match = raw_scripts[index] if len(raw_scripts)==len(scripts) else None
            value = (raw_match[1] or raw_match[2]) if raw_match else (match[1] or match[2])
            uncertain = raw_match is None or bool(re.search(r'[\u0590-\u08ff\ufb1d-\ufeff]', match[0]))
            emit(value, 'original_script', 'und', match.start(), match.end(), uncertain=uncertain)

        # Alias fields can occur inside the heading or as a standalone field later.
        ranges = [(0, boundary)]
        for i,(a,b,r) in enumerate(spans):
            if a >= boundary and ALIAS.match(r['text']):
                end = next((aa for aa,bb,rr in spans[i+1:] if _field(rr['text']) or ALIAS.match(rr['text'])), len(text))
                ranges.append((a,end))
        for lo,hi in ranges:
            area = text[lo:hi]
            inline_fields = _top_level_delimiters(area, INLINE_FIELD)
            if inline_fields:
                area = area[:inline_fields[0].start()]
            matches = list(ALIAS.finditer(area))
            for index,match in enumerate(matches):
                value_start = match.end()
                limit = matches[index+1].start() if index+1<len(matches) else len(area)
                # Japanese aliases end before English primary; script is its own name.
                if re.match(r'^[af]\s*\.',match[1],re.I):
                    language = 'en'
                else:
                    language = 'ja'
                    english = _latin_start(area[value_start:limit])
                    if english:
                        limit = value_start+english.start()
                end = min(_alias_end(area, match.start()), limit)
                value = area[value_start:end]
                label = re.sub(r'\s+', '', match[1])
                name_kind = 'weak_alias' if label=='確定に十分でない別名' else 'former_name' if label=='旧称' or label.startswith('以前') or match[1].lower().startswith('f.') else 'alias'
                # Semicolons and explicit (a)/(b) labels split aliases. Commas never do.
                delimiters = _top_level_delimiters(value, ENUM)
                cuts = [(m.start(), m.end()) for m in delimiters]
                if not cuts:
                    cuts = [(m.start(),m.end()) for m in _top_level_delimiters(value, re.compile('[;；]'))]
                cursor = 0
                pieces = []
                for a,b in cuts:
                    if value[cursor:a].strip():
                        pieces.append((cursor,a))
                    cursor = b
                if value[cursor:].strip():
                    pieces.append((cursor,len(value)))
                for a,b in pieces:
                    item = value[a:b].strip()
                    # An unmatched closing wrapper is not part of a name.
                    while item.endswith(')') and item.count(')') > item.count('('):
                        item = item[:-1].rstrip()
                    uncertain = bool(re.search(r'[(),、]|\d{4}|国籍|生年月日|出生地|番号|発行|issued|DOB|POB|national', item, re.I))
                    uncertain = uncertain or (language=='ja' and bool(re.search(r'[A-Za-z]{2,}', item)) and bool(re.search(r'[\u3040-\u30ff\u4e00-\u9fff]', item)))
                    emit(item, name_kind, language, lo+value_start+a, lo+value_start+b, uncertain)
                # English translation of standalone Japanese aliases, without aka marker.
                if lo>=boundary and language=='ja' and end==limit and limit<len(area) and _latin_start(area[limit:]) is not None:
                    english_end = matches[index+1].start() if index+1<len(matches) else len(area)
                    eng = area[limit:english_end]
                    for part in re.finditer(r'[^;]+', eng):
                        emit(part[0], name_kind, 'en', lo+limit+part.start(), lo+limit+part.end(),
                             bool(re.search(r'[():,、]|\b\d{4}\b', part[0])))
        if not any(n['source_record_id']==rid and n['name_kind']=='primary' for n in names):
            issue('PRIMARY_MISSING', '現行対象の主名称が抽出できない', block)
        if any(i['source_record_id']==rid for i in issues):
            record['parse_status']='REVIEW_REQUIRED'

    # Published counts are an independent guard when present in the current UN PDF.
    text = '\n'.join(r['text'] for r in lines)
    published = re.search(r'(\d+)個人(?:及び|および)(\d+)団体', re.sub(r'\s+', '', text))
    if document_key=='current_un' and published:
        actual = {k:sum(r['current']=='true' and r['party_type']==k for r in records) for k in ['person','entity']}
        if (actual['person'],actual['entity']) != (int(published[1]),int(published[2])):
            issue('COUNT_MISMATCH', f'公表件数={published[1]}/{published[2]} 抽出件数={actual}')
    fatal = {'NO_RECORDS','DUPLICATE_NUMBER','NUMBER_SEQUENCE','PRIMARY_MISSING','COUNT_MISMATCH','IMAGE_OR_EMPTY_PAGE'}
    return dict(status='BLOCKED' if any(i['code'] in fatal for i in issues) else 'REVIEW_REQUIRED',
                records=records, names=names, issues=issues, page_count=len(pages), parser_version=PARSER_VERSION)


def extract(root: Path, document_keys=None):
    state, _, _ = D.load_bundle(root)
    keys = document_keys or ['current_un','current_1373']
    records, names, issues, pages_out, documents = [], [], [], [], []
    for key in keys:
        resource = state['resources'].get(key, {})
        try:
            if key not in {'current_un','current_1373'}:
                raise ValueError('unsupported document format; attachment parser required')
            if not resource.get('available'):
                raise ValueError('document unavailable')
            raw = (root / resource['raw_path']).resolve()
            allowed = (root/'data/raw/mofa').resolve()
            if not raw.is_relative_to(allowed):
                raise ValueError('raw path outside MOFA evidence directory')
            body = read_raw(raw)
            if sha256(body) != resource['sha256']:
                raise ValueError('original PDF SHA256 mismatch')
            with pdfplumber.open(io.BytesIO(body)) as pdf:
                if len(pdf.pages)>2000:
                    raise ValueError('page limit exceeded')
                pages = [p.extract_text(x_tolerance=2, y_tolerance=3) or '' for p in pdf.pages]
            signature = re.sub(r'\s+', '', _norm(pages[0])) if pages else ''
            resolution = '1267' if key=='current_un' else '1373'
            if resolution not in signature or '資産凍結' not in signature:
                raise ValueError('current PDF format signature missing or changed')
            result = parse_pages(pages, document_key=key, source_hash=resource['sha256'],
                                 source_url=resource['url'], raw_path=resource['raw_path'])
            records.extend(result['records']); names.extend(result['names']); issues.extend(result['issues'])
            for number,text in enumerate(pages,1):
                pages_out.append(dict(document_key=key, source_hash=resource['sha256'], page=number,
                                      source_page_url=resource['url']+'#page='+str(number), text=text))
            documents.append(dict(document_key=key, source_hash=resource['sha256'], status=result['status'],
                                  page_count=len(pages), record_count=len(result['records']), name_count=len(result['names']),
                                  issue_count=len(result['issues'])))
        except Exception as error:
            # Failed extraction is observable and removes stale candidates from this projection.
            issues.append(dict(document_key=key, source_record_id='', pages='', code='EXTRACTION_FAILED',
                               detail=str(error), source_hash=resource.get('sha256','')))
            documents.append(dict(document_key=key, source_hash=resource.get('sha256',''), status='BLOCKED', error=str(error)))
    report = dict(schema_version=1, parser_version=PARSER_VERSION, documents=documents,
                  status='BLOCKED' if any(x['status']=='BLOCKED' for x in documents) else 'REVIEW_REQUIRED',
                  auto_import='BLOCKED', applied=False, record_count=len(records), name_count=len(names), issue_count=len(issues))
    dest = root/OUTPUT
    writes = [FileWrite(dest/'records.csv', lambda p:p.write_bytes(D._csv_bytes(records,RECORD_COLS))),
              FileWrite(dest/'names.csv', lambda p:p.write_bytes(D._csv_bytes(names,NAME_COLS))),
              FileWrite(dest/'issues.csv', lambda p:p.write_bytes(D._csv_bytes(issues,ISSUE_COLS))),
              FileWrite(dest/'pages.jsonl', lambda p:p.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in pages_out),encoding='utf-8')),
              FileWrite(dest/'report.json', lambda p:p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'))]
    atomic_replace_many(writes)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('.'))
    parser.add_argument('--document-key',action='append',choices=['current_un','current_1373'])
    args=parser.parse_args(argv)
    try:
        result=extract(args.root,args.document_key)
    except (ValueError,OSError,RuntimeError) as error:
        print(json.dumps({'status':'BLOCKED','error':str(error)},ensure_ascii=False)); return 1
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return int(result['status']=='BLOCKED')


if __name__=='__main__':
    raise SystemExit(main())
