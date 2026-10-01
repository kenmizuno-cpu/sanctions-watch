"""Compatibility guard for the retired automatic MOFA monitor."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .fetch import NetworkPolicyError
from .mofa_documents import stamp

MESSAGE = 'MOFA automatic acquisition is disabled. Save official files in a browser, then use python -m src.mofa_manual.'


def fetch_document(root: Path, *, link, previous, session=None):
    raise NetworkPolicyError(MESSAGE)


def run(root: Path, *, now: datetime, session=None, new_notice_limit: int = 30,
        dry_run: bool = False, from_month: str | None = None, report: Path | None = None) -> int:
    checked_at = stamp(now)
    if report:
        # Only the explicitly requested report is written; no monitoring bundle is modified.
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({'checked_at': checked_at, 'exit_code': 1,
                                     'mode': 'manual_required', 'error': MESSAGE},
                                    ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--from-month')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    print(MESSAGE, file=sys.stderr)
    return run(args.root, now=datetime.now(timezone.utc), dry_run=args.dry_run,
               from_month=args.from_month, report=args.report)


if __name__ == '__main__':
    raise SystemExit(main())
