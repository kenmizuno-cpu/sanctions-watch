"""Offline acquisition of MOFA documents saved by a human in a browser."""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import tempfile
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pdfplumber

from . import mofa_documents as D, source_audit as A
from .fetch import Fetched, sha256
from .mofa_sources import CATALOG_URL, DocumentLink, _key, parse_release_body, validate_mofa_url
from .persistence import FileWrite
from .state import append_heartbeat

PDF_LIMIT = 20 * 1024 * 1024
HTML_LIMIT = 5 * 1024 * 1024
OPERATIONS_PATH = 'data/mofa/manual_operations.csv'
OPERATION_COLS = ['operation_id', 'at', 'operation', 'family', 'document_key',
                  'operator', 'note', 'result', 'url', 'source_hash', 'raw_path',
                  'event_id', 'local_filename', 'error']
FAMILIES = {'catalog': 'mofa_catalog', 'press': 'mofa_press'}


def _metadata(operator, when, note):
    if not isinstance(operator, str) or not operator.strip() or not isinstance(note, str) or not note.strip():
        raise ValueError('operator and note are required')
    return D.stamp(when)


def _operation_id(row):
    return sha256(json.dumps({k: v for k, v in row.items() if k != 'operation_id'},
                            sort_keys=True, ensure_ascii=False).encode())


def _load(root, when):
    state, events, queue = D.load_bundle(root)
    operations = []
    path = root / OPERATIONS_PATH
    if path.exists():
        with path.open(encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != OPERATION_COLS:
                raise D.DocumentStateError('invalid manual operation columns')
            operations = list(reader)
        for row in operations:
            if set(row) != set(OPERATION_COLS) or row['operation_id'] != _operation_id(row):
                raise D.DocumentStateError('manual operation evidence is corrupt')
            D._valid_time(row['at'])
    if state.get('mode') == 'manual' and not path.exists():
        raise D.DocumentStateError('manual operation history is missing')
    manual = state.setdefault('manual', {'last_operation_at': '', 'import_errors': {}, 'linked_documents': {}})
    if not isinstance(manual, dict) or any(not isinstance(manual.get(k), dict) for k in ['import_errors', 'linked_documents']):
        raise D.DocumentStateError('invalid manual state')
    last = manual.get('last_operation_at', '')
    D._valid_time(last)
    if bool(last) != bool(operations) or (operations and last != operations[-1]['at']):
        raise D.DocumentStateError('manual operation history does not match state')
    if D.stamp(when) < last:
        raise ValueError('operation time is older than the last recorded operation')
    state['mode'] = 'manual'
    return state, events, queue, operations


def refresh_status(state, queue):
    """Recompute manual lifecycle, including after a document review."""
    if state.get('mode') != 'manual':
        return
    manual = state['manual']
    for family in FAMILIES.values():
        value = state['families'].setdefault(family, {})
        value.setdefault('last_manual_check_at', '')
        resources = {k: r for k, r in state['resources'].items()
                     if (r['role'] == 'current_pdf') == (family == 'mofa_catalog')
                     and r['role'] in {'current_pdf', 'notice', 'attachment_pdf'}}
        unresolved = [r for r in queue if r['review_status'] not in D.RESOLVED
                      and (r['role'] == 'current_pdf') == (family == 'mofa_catalog')]
        missing = 0
        if family == 'mofa_catalog':
            missing = sum(not resources.get(k, {}).get('available') for k in ['current_un', 'current_1373'])
        else:
            pending_keys = {k for k,r in resources.items() if not r.get('available')}
            for links in manual['linked_documents'].values():
                for link in links:
                    resource = resources.get(link['key']) or next(
                        (r for r in resources.values() if r['role'] == 'attachment_pdf'
                         and r['url'] == link['url'] and r.get('notice_url') == link['notice_url']), {})
                    if (not resource.get('available') or resource.get('url') != link['url']
                            or resource.get('last_success_at', '') < link['observed_at']):
                        pending_keys.add(resource.get('key',link['key']))
            missing = len(pending_keys) + len(state['pending_notices'])
        errors = [x for x in manual['import_errors'].values() if x['family'] == family]
        pending = bool(value.get('declared_pending')) or bool(missing)
        if family == 'mofa_press' and state['coverage_gap']:
            status = 'COVERAGE_GAP'
        elif errors:
            status = 'manual_error'
        elif pending:
            status = 'manual_pending' if resources or value.get('declared_pending') or value['last_manual_check_at'] else 'manual_required'
        elif unresolved:
            status = 'manual_review'
        elif value['last_manual_check_at']:
            status = 'manual_checked'
        else:
            status = 'manual_required'
        value.update(status=status, pending_count=missing, review_count=len(unresolved),
                     sha256=sha256(json.dumps({k: r['sha256'] for k, r in resources.items()}, sort_keys=True).encode()))


def _save(root, state, events, queue, operations, *, when, operation, family='',
          document_key='', operator, note, result='recorded', url='', fetched=None,
          event_id='', local_filename='', error=''):
    at = D.stamp(when)
    row = {k: '' for k in OPERATION_COLS}
    row.update(at=at, operation=operation, family=family, document_key=document_key,
               operator=operator.strip(), note=note.strip(), result=result, url=url,
               source_hash=fetched.sha256 if fetched else '', raw_path=fetched.raw_path if fetched else '',
               event_id=event_id or '', local_filename=local_filename, error=error)
    row['operation_id'] = _operation_id(row)
    if not any(x['operation_id'] == row['operation_id'] for x in operations):
        operations.append(row)
    state['manual']['last_operation_at'] = at
    refresh_status(state, queue)
    writes = D._projection_writes(root, state, queue)
    writes.append(FileWrite(root / OPERATIONS_PATH, lambda p: p.write_bytes(D._csv_bytes(operations, OPERATION_COLS))))
    audit = A.entry(family or 'mofa_manual', document_key or operation, 'manual_' + result,
                    fetched=fetched, url=url, error=error or None)
    utc = when.astimezone(timezone.utc)
    writes.append(FileWrite(root / 'data/source_audit' / f'{utc:%Y-%m}.csv',
                            lambda p: A.append_rows(p, [audit], now=utc), seed_existing=True))
    hb = [{'source': k, 'status': v['status'], 'record_count': '',
           'content_hash': v['sha256'], 'source_updated': v.get('last_document_change_at', '')}
          for k, v in state['families'].items() if k in FAMILIES.values()]
    writes.append(FileWrite(root / 'data/heartbeat' / f'{utc:%Y-%m}.csv',
                            lambda p: append_heartbeat(p, hb, now=utc), seed_existing=True))
    D.save_bundle(root, state, events, queue, writes)
    return {'exit_code': int(result == 'failed'), 'operation_id': row['operation_id'],
            'event_id': row['event_id'], 'source_hash': row['source_hash'], 'raw_path': row['raw_path'],
            'families': state['families'], 'error': error}


def initialize(root: Path, *, operator: str, when: datetime, note: str):
    _metadata(operator, when, note)
    state, events, queue, operations = _load(root, when)
    return _save(root, state, events, queue, operations, when=when, operation='init', operator=operator, note=note)


def record_check(root: Path, *, family: str, result: str, operator: str, when: datetime, note: str):
    at = _metadata(operator, when, note)
    if family not in FAMILIES or result not in {'checked', 'pending'}:
        raise ValueError('family must be catalog/press; result must be checked/pending')
    state, events, queue, operations = _load(root, when)
    key = FAMILIES[family]
    state['families'].setdefault(key, {}).update(last_manual_check_at=at,
                                               manual_operator=operator.strip(), declared_pending=result == 'pending')
    return _save(root, state, events, queue, operations, when=when, operation='check', family=key,
                 operator=operator, note=note, result=result)


def _link(role, source_url, title, notice_url, publication_date, document_key, baseline):
    validate_mofa_url(source_url)
    if publication_date:
        if date.fromisoformat(publication_date).isoformat() != publication_date:
            raise ValueError('publication-date must be YYYY-MM-DD')
    if role in {'current_un', 'current_1373'}:
        key, kind, parent = role, 'current_pdf', CATALOG_URL
        title = title or ('国連決議1267号等' if role == 'current_un' else '国連決議1373号')
        baseline = True
    elif role == 'notice':
        key, kind, parent = _key('notice', source_url), role, source_url
    elif role == 'attachment_pdf':
        if not notice_url:
            raise ValueError('attachment_pdf requires notice-url')
        validate_mofa_url(notice_url)
        key, kind, parent = _key('attachment', notice_url, source_url), role, notice_url
    else:
        raise ValueError('unknown document role')
    if kind.endswith('pdf') and not urlsplit(source_url).path.lower().endswith('.pdf'):
        raise ValueError('PDF source-url must identify a PDF')
    if document_key:
        if kind == 'current_pdf' and document_key != key:
            raise ValueError('current PDF has a fixed document key')
        if kind != 'current_pdf' and document_key in {'current_un', 'current_1373', 'catalog', 'press_index'}:
            raise ValueError('reserved document key')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', document_key):
            raise ValueError('invalid document key')
        key = document_key
    return DocumentLink(key, kind, source_url, title or Path(urlsplit(source_url).path).name,
                        parent, publication_date, 'date' if publication_date else '', baseline)


def _read_file(file, link):
    limit = PDF_LIMIT if link.role.endswith('pdf') else HTML_LIMIT
    with file.open('rb') as f:
        body = f.read(limit + 1)
    if not body or len(body) > limit:
        raise ValueError('file is empty or exceeds size limit')
    if link.role.endswith('pdf'):
        if not body.startswith(b'%PDF-') or b'%%EOF' not in body[-1024:]:
            raise ValueError('PDF header or end marker missing')
        try:
            with pdfplumber.open(io.BytesIO(body)) as document:
                if not document.pages:
                    raise ValueError('PDF has no pages')
        except Exception as error:
            raise ValueError('PDF structure is invalid: ' + str(error)) from error
        return body, None
    return body, parse_release_body(body, link=link)


def import_file(root: Path, *, file: Path, role: str, source_url: str, operator: str,
                when: datetime, note: str, title: str = '', notice_url: str = '',
                publication_date: str = '', document_key: str = '', baseline: bool = False,
                expected_sha256: str = '', dry_run: bool = False):
    at = _metadata(operator, when, note)
    link = _link(role, source_url, title, notice_url, publication_date, document_key, baseline)
    if expected_sha256 and not re.fullmatch('[a-f0-9]{64}', expected_sha256):
        raise ValueError('expected-sha256 must be 64 lowercase hex characters')
    if dry_run:
        with tempfile.TemporaryDirectory(prefix='mofa-manual-preview-') as temp:
            clone = Path(temp)
            for rel in ['data/mofa', 'data/review/mofa_document_queue.csv', 'data/raw/mofa',
                        'data/heartbeat', 'data/source_audit', 'data/state.json', 'data/manual/meti',
                        'data/dashboard/status.csv']:
                p = root / rel
                if p.exists():
                    target = clone / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if p.is_dir():
                        shutil.copytree(p, target)
                    else:
                        shutil.copy2(p, target)
            return import_file(clone, file=file.resolve(), role=role, source_url=source_url, operator=operator,
                               when=when, note=note, title=title, notice_url=notice_url,
                               publication_date=publication_date, document_key=document_key, baseline=baseline,
                               expected_sha256=expected_sha256)
    state, events, queue, operations = _load(root, when)
    inherited_pending = [x for x in state['pending_notices'] if
                         x.get('key') == link.key or x.get('url') == link.url] if link.role == 'notice' else []
    if any(x.get('baseline') for x in inherited_pending):
        link.baseline = True
    previous = state['resources'].get(link.key, {})
    previous_parent = previous.get('notice_url') or next(
        (x['notice_url'] for x in reversed(events) if x['document_key'] == link.key), '')
    if previous and (previous['role'] != link.role or
                     (link.role == 'attachment_pdf' and previous_parent != link.notice_url)):
        raise ValueError('document-key belongs to another role or notice')
    if at < previous.get('last_success_at', ''):
        raise ValueError('import time is older than the last valid document')
    family = 'mofa_catalog' if link.role == 'current_pdf' else 'mofa_press'
    try:
        body, parsed = _read_file(Path(file), link)
        content_hash = sha256(body)
        if expected_sha256 and content_hash != expected_sha256:
            raise ValueError('expected SHA-256 does not match the file')
    except (ValueError, OSError) as error:
        message = str(error)[:4000]
        state['manual']['import_errors'][link.key] = {'family': family, 'at': at, 'url': link.url, 'error': message}
        return _save(root, state, events, queue, operations, when=when, operation='import', family=family,
                     document_key=link.key, operator=operator, note=note, result='failed', url=link.url,
                     local_filename=Path(file).name, error=message)
    fetched = Fetched(link.url, body=body, sha256=content_hash, filename=Path(file).name)
    D.save_raw(root, fetched, link.role)
    event_id = D.observe_document(state, events, queue, link=link, fetched=fetched, when=when,
                                  reason='手動取得 / ' + operator.strip() + ' / ' + note.strip())
    resource = state['resources'][link.key]
    resource.update(acquisition_method='manual_browser', manual_operator=operator.strip(), manual_note=note.strip())
    state['manual']['import_errors'].pop(link.key, None)
    if parsed is not None:
        state['manual']['linked_documents'][link.key] = [dict(asdict(x), observed_at=at) for x in parsed.attachments]
        resource['external_links'] = parsed.external_links
        if inherited_pending:
            history = state['manual'].setdefault('pending_notice_history',[])
            history.extend({'notice': x, 'resolved_at': at, 'source_hash': content_hash,
                            'event_id': event_id or resource['observation_head'],
                            'operator': operator.strip(), 'note': note.strip()} for x in inherited_pending)
            state['pending_notices'] = [x for x in state['pending_notices'] if x not in inherited_pending]
    if event_id:
        state['families'].setdefault(family, {})['last_document_change_at'] = at
    return _save(root, state, events, queue, operations, when=when, operation='import', family=family,
                 document_key=link.key, operator=operator, note=note, result='validated', url=link.url,
                 fetched=fetched, event_id=event_id, local_filename=Path(file).name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    sub = parser.add_subparsers(dest='operation', required=True)
    for name in ['init', 'check', 'import']:
        command = sub.add_parser(name)
        command.add_argument('--operator', required=True)
        command.add_argument('--at', required=True, help='ISO timestamp with timezone')
        command.add_argument('--note', required=True)
        if name == 'check':
            command.add_argument('--family', choices=FAMILIES, required=True)
            command.add_argument('--result', choices=['checked', 'pending'], required=True)
        if name == 'import':
            command.add_argument('file', type=Path)
            command.add_argument('--role', choices=['current_un', 'current_1373', 'notice', 'attachment_pdf'], required=True)
            command.add_argument('--source-url', required=True)
            for field in ['title', 'notice-url', 'publication-date', 'document-key', 'expected-sha256']:
                command.add_argument('--' + field, default='')
            command.add_argument('--baseline', action='store_true')
            command.add_argument('--dry-run', action='store_true')
    args = vars(parser.parse_args(argv))
    root = args.pop('root')
    operation = args.pop('operation')
    try:
        args['when'] = datetime.fromisoformat(args.pop('at').replace('Z', '+00:00'))
        result = {'init': initialize, 'check': record_check, 'import': import_file}[operation](root, **args)
    except (ValueError, OSError, RuntimeError) as error:
        result = {'exit_code': 1, 'error': str(error)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
