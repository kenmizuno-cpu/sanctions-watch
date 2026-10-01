"""MOFA-only URL and document source validation."""
from urllib.parse import urlsplit
from .fetch import NetworkPolicyError


def validate_mofa_url(url: str) -> str:
    try:
        p = urlsplit(url)
        allowed = (p.scheme == 'https' and p.hostname in {'www.mofa.go.jp', 'mofa.go.jp'}
                   and not p.username and not p.password and ':' not in p.netloc
                   and not any(c.isspace() for c in url) and not p.fragment)
    except ValueError:
        allowed = False
    if not allowed:
        raise NetworkPolicyError('MOFA URL forbidden: ' + str(url))
    return url

from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
import hashlib
import re
from urllib.parse import urljoin, urldefrag
from .fetch import Fetched

CATALOG_URL = 'https://www.mofa.go.jp/mofaj/gaiko/terro/kyoryoku_05.html'
PRESS_URL = 'https://www.mofa.go.jp/mofaj/press/release/index.html'

class MofaSchemaError(ValueError):
    pass

@dataclass
class DocumentLink:
    key: str
    role: str
    url: str
    title: str
    notice_url: str = ''
    publication_date: str = ''
    publication_precision: str = ''
    baseline: bool = False

@dataclass
class ReleaseListing:
    month: str
    notices: list[DocumentLink]
    archive_urls: dict[str, str]
    verified_empty: bool

@dataclass
class ReleaseBody:
    link: DocumentLink
    text: str
    reasons: list[str]
    attachments: list[DocumentLink]
    external_links: list[str]

@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    def text(self):
        return ' '.join(c.text() if isinstance(c, Node) else c for c in self.children)
    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.walk()

class Tree(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.root=Node('root'); self.stack=[self.root]
        self.feed(Fetched('',body=body).text)
    def handle_starttag(self, tag, attrs):
        n=Node(tag,dict(attrs)); self.stack[-1].children.append(n)
        if tag not in {'img','br','hr','input','meta','link','source','wbr','area','base'}:
            self.stack.append(n)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag:
                del self.stack[i:]; break
    def handle_data(self, s):
        if self.stack[-1].tag not in {'script','style'}:
            self.stack[-1].children.append(s)

def _main(body):
    if not body:
        raise MofaSchemaError('empty HTML')
    tree=Tree(body).root
    main=next((n for n in tree.walk() if n.attrs.get('id')=='maincontents'),None)
    if main is None:
        raise MofaSchemaError('maincontents missing')
    return tree, main

def _url(base, href):
    return urldefrag(urljoin(base,href))[0]

def _key(role, *values):
    return role+':'+hashlib.sha256('\n'.join(values).encode()).hexdigest()

def parse_catalog(body: bytes, *, url: str) -> list[DocumentLink]:
    _, main=_main(body)
    found={}
    for n in main.walk():
        if n.tag not in {'li','p','dd'} or '資産凍結措置対象リスト' not in n.text():
            continue
        for a in n.walk():
            if a.tag!='a' or not re.search(r'\.pdf(?:\?|$)',a.attrs.get('href',''),re.I):
                continue
            text=a.text()
            role='current_1373' if '1373' in text else 'current_un' if '1267' in text else ''
            if not role: continue
            u=validate_mofa_url(_url(url,a.attrs['href']))
            if role in found and found[role].url!=u:
                raise MofaSchemaError('duplicate current list: '+role)
            found[role]=DocumentLink(role,'current_pdf',u,text,notice_url=url,baseline=True)
    if set(found)!={'current_un','current_1373'}:
        raise MofaSchemaError('current list roles missing')
    return [found['current_un'],found['current_1373']]

def parse_release_listing(body: bytes, *, url: str, month: str) -> ReleaseListing:
    year, mon=map(int,month.split('-')); date(year,mon,1)
    tree, main=_main(body)
    if '報道発表' not in tree.text(): raise MofaSchemaError('release heading missing')
    archives={}
    for a in main.walk():
        if a.tag!='a': continue
        href=a.attrs.get('href','')
        match=re.search(r'/release/(\d+)_(\d+)_index\.html',href)
        modern=re.search(r'/release/(\d{4})/(\d+)\.html',href)
        if match or modern:
            y,m=map(int,(match or modern).groups())
            if match: y+=2018
            try: date(y,m,1)
            except ValueError: raise MofaSchemaError('bad archive month')
            archives[f'{y:04}-{m:02}']=validate_mofa_url(_url(url,href))
    listing=next((n for n in main.walk() if n.attrs.get('id')=='pressrelease'),main)
    notices={}; current_date=''; detected_months=set(); populated_entries=0
    for n in listing.walk():
        if n.tag in {'dt','h2','h3'}:
            d=re.search(r'(\d{1,2})月\s*(\d{1,2})日',n.text())
            if d:
                m,day=map(int,d.groups()); detected_months.add(m)
                try: current_date=date(year,m,day).isoformat()
                except ValueError: raise MofaSchemaError('bad release date')
        if n.tag!='a': continue
        href=n.attrs.get('href','')
        if not current_date:
            continue
        # Date-bearing listing entries are official notices regardless of MOFA path.
        if re.search(r'(?:_index\.html|/\d{4}/\d+\.html|#archives)',href):
            continue
        populated_entries += 1
        if not re.search(r'\.html(?:\?|$)',href):
            raise MofaSchemaError('unknown populated release link structure')
        u=validate_mofa_url(_url(url,href))
        if int(current_date[5:7])!=mon:
            if url == PRESS_URL:
                continue  # rolling index may include preceding-month entries
            raise MofaSchemaError('listing dates contradict requested month')
        notices[u]=DocumentLink(_key('notice',u),'notice',u,n.text().strip(),u,current_date,'date')
    explicit=(f'{year}年{mon}月' in main.text() or f'令和{year-2018}年{mon}月' in main.text())
    archive_match=re.search(r'/release/(\d+)_(\d+)_index\.html',url)
    if archive_match:
        if int(archive_match[1])+2018!=year or int(archive_match[2])!=mon:
            raise MofaSchemaError('archive URL contradicts requested month')
        # Filename alone is insufficient: require month-bearing content for empty archives.
    declared_months = []
    for n in main.walk():
        if n.tag in {'h1','h2','h3'}:
            for m in re.finditer(r'(令和)?(\d+)年\s*(\d+)月',n.text()):
                y=int(m[2]) + (2018 if m[1] else 0)
                declared_months.append((y,int(m[3])))
    if declared_months and (year,mon) not in declared_months:
        raise MofaSchemaError('archive heading contradicts requested month')
    has_list=any(n.tag in {'ul','dl'} for n in listing.walk())
    verified_empty=not notices and not populated_entries and not detected_months and explicit and has_list and bool(archives)
    if not notices and not verified_empty:
        raise MofaSchemaError('month not verified or release structure missing: '+month)
    return ReleaseListing(month,list(notices.values()),archives,verified_empty)

def parse_release_body(body: bytes, *, link: DocumentLink) -> ReleaseBody:
    tree, main=_main(body)
    if not any(n.tag=='h1' for n in tree.walk()): raise MofaSchemaError('release title missing')
    text=main.text()
    words=['資産凍結','制裁','措置対象者','追加','削除','解除','外為法','タリバーン','テロリスト','ウクライナ']
    reasons=[w for w in words if w in text or w in link.title]
    attachments=[]; external=[]; seen=set()
    for a in main.walk():
        if a.tag!='a': continue
        u=_url(link.url,a.attrs.get('href',''))
        if not re.search(r'\.pdf(?:\?|$)',u,re.I) or u in seen: continue
        seen.add(u)
        try: validate_mofa_url(u)
        except NetworkPolicyError: external.append(u); continue
        attachments.append(DocumentLink(_key('attachment',link.url,u),'attachment_pdf',u,a.text().strip(),link.url,link.publication_date,link.publication_precision,link.baseline))
    return ReleaseBody(link,text,reasons,attachments,external)
