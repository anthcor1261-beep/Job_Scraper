"""Weekly job discovery and conservative shortlist. No paid API or résumé required."""
from __future__ import annotations
import concurrent.futures
import html
import json
import re
import socket
import ipaddress
import urllib.request
import urllib.error
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGGREGATORS = ('linkedin.com','indeed.com','glassdoor.com','ziprecruiter.com','google.com','hiring.cafe')
TOPIC = re.compile(r'education|edtech|learning policy|AI governance|AI implementation|AI adoption|artificial intelligence|public.sector AI|democracy|election|voting|civic|policy|government affairs|public affairs|survey research|survey analyst|program evaluation|education grants', re.I)
WRONG = re.compile(r'clinical|nurs(?:e|ing)|physician|insurance|underwrit|actuar|financial|finance|sales|account executive|software engineer|data engineer|machine learning engineer|devops', re.I)
STRETCH = re.compile(r'\b(director|head|principal|chief|vice president|VP|senior fellow|professor)\b', re.I)
EARLY = re.compile(r'analyst|associate|assistant|coordinator|specialist|fellow|program manager|project manager|researcher', re.I)
CLOSED = re.compile(r'(?:this|the) (?:job|position|vacancy|posting) (?:is|has been) (?:closed|filled|removed|expired)|no longer accepting applications|job (?:is )?no longer available',re.I)

def canonical(url):
    p=urlsplit(url)
    q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('trackingid','trk','referrer')]
    return urlunsplit((p.scheme,p.netloc.lower(),p.path,urlencode(q),p.fragment))

def aggregator(url):
    host=(urlsplit(url).hostname or '').lower()
    return any(host==h or host.endswith('.'+h) for h in AGGREGATORS)

def public_url(url):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password: raise ValueError('Unsupported URL')
    if p.port not in (None,80,443): raise ValueError('Unsupported port')
    addresses=socket.getaddrinfo(p.hostname,p.port or 443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses): raise ValueError('Non-public destination')

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        public_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def inspect_page(body, status, final_url, title):
    if status in (404,410): return 'closed'
    if status != 200: return 'unknown'
    visible = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', ' ', body, flags=re.I|re.S)
    if CLOSED.search(re.sub('<[^>]+>',' ',visible)): return 'closed'
    expiry = re.search(r'"validThrough"\s*:\s*"([^"]+)"', body)
    if expiry:
        try:
            end = datetime.fromisoformat(expiry.group(1).replace('Z','+00:00'))
            if end.tzinfo is None: end = end.replace(tzinfo=timezone.utc)
            if end < datetime.now(timezone.utc): return 'closed'
        except ValueError: pass
    if re.search(r'captcha|verify you are human|access denied',body,re.I): return 'unknown'
    # A successful generic landing page is not evidence of a live vacancy.
    title_words=[w.lower() for w in re.findall(r'\w+',title) if len(w)>3]
    text=html.unescape(re.sub('<[^>]+>',' ',visible)).lower()
    matches=sum(w in text for w in title_words)
    has_job=re.search(r'JobPosting|apply (?:now|for this)|submit application',body,re.I)
    if has_job and title_words and matches>=max(1,len(title_words)*0.7) and not aggregator(final_url): return 'live'
    return 'unknown'

def validate(job):
    candidates=list(dict.fromkeys([job.get('direct_url',''),job.get('url','')]+(job.get('duplicate_urls') or [])))
    candidates=[canonical(u) for u in candidates if u and urlsplit(u).scheme in ('http','https')]
    candidates.sort(key=aggregator)
    results=[]
    for url in candidates[:3]:
        # Do not crawl aggregator pages looking for guessed employer links.
        if aggregator(url):
            results.append((url,'unknown')); continue
        try:
            public_url(url)
            opener=urllib.request.build_opener(SafeRedirect())
            with opener.open(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; PersonalJobScan/1.0)'}),timeout=12) as r:
                body=r.read(2_000_000).decode('utf-8','replace')
                final=canonical(r.url)
                status=inspect_page(body,r.status,final,job.get('title',''))
        except urllib.error.HTTPError as e: final,status=url,('closed' if e.code in (404,410) else 'unknown')
        except (OSError,ValueError): final,status=url,'unknown'
        results.append((final,status))
        if status=='live': break
    live=next((r for r in results if r[1]=='live'),None)
    chosen=live or next((r for r in results if r[1]=='unknown'),None) or (results[0] if results else ('','unknown'))
    # A dead official link with only aggregator alternatives is closed.
    official=[r for r in results if not aggregator(r[0])]
    if not live and official and all(r[1]=='closed' for r in official): chosen=official[0]
    return dict(job, preferred_url=chosen[0], link_status=chosen[1], checked_at=datetime.now(timezone.utc).isoformat())

def salary_status(value,floor=65000):
    s=str(value or '').lower().replace(',','')
    if not s or re.search(r'\b(?:cad|aud|eur|gbp)\b|[£€]',s): return 'review'
    if re.search(r'week|day|hour|stipend|semester',s): return 'review'
    nums=[float(n)* (1000 if k else 1) for n,k in re.findall(r'(\d+(?:\.\d+)?)\s*(k?)',s)]
    if not nums: return 'review'
    if re.search(r'hour|/hr',s): return 'review' # hours/FTE not guaranteed
    if re.search(r'month|/mo',s): nums=[n*12 for n in nums]
    elif not re.search(r'year|annual|/yr|\bk\b',s) and min(nums)<10000: return 'review'
    if max(nums)<floor: return 'below floor'
    return 'meets floor' if min(nums)>=floor else 'review'

def classify(job,floor=65000):
    title=job.get('title',''); body=job.get('description',''); topic=bool(TOPIC.search(title+' '+body))
    if not topic: return 'Excluded', 'No priority-area evidence'
    # Clear domain-specific titles can retain unusual job families for manual review.
    if WRONG.search(title) and not TOPIC.search(title): return 'Excluded','Off-target job family'
    sal=salary_status(job.get('salary'),floor)
    if sal=='below floor': return 'Excluded','Salary below $65,000'
    if job.get('link_status')=='closed': return 'Excluded','Posting closed'
    reasons=[]
    if sal!='meets floor': reasons.append('Salary requires confirmation')
    if job.get('link_status')!='live': reasons.append('Live employer/ATS link unconfirmed')
    if WRONG.search(title): reasons.append('Unusual job family; review duties')
    if reasons: return 'Needs review','; '.join(reasons)
    years=[int(n) for n in re.findall(r'(\d+)\+?\s*(?:years|yrs)(?:\s+of)?\s+(?:relevant |professional |related )?experience',body,re.I)]
    if STRETCH.search(title) or re.search(r'\bsenior\b',title,re.I) or (years and max(years)>=5): return 'Stretch','Senior title or experience requirement'
    if re.search(r'\bAI\b|artificial intelligence',title,re.I):
        return 'Needs review','Adjacent AI opportunity; check required AI governance or implementation experience'
    if re.search(r'\b(?:Ph\.?D\.?|doctorate)\b.{0,30}required|required.{0,30}\b(?:Ph\.?D\.?|doctorate)\b',body,re.I):
        return 'Stretch','Doctoral qualification appears required; verify eligibility'
    if EARLY.search(title) and TOPIC.search(title): return 'Strong match','Priority-area title and early/mid-career role; qualifications still need review'
    return 'Needs review','Relevant area; career level or duties need review'

def publish(jobs,health):
    floor=json.loads((ROOT/'config.json').read_text())['policy_scan']['salary_floor_usd']
    for j in jobs: j['match_group'],j['match_reason']=classify(j,floor)
    out=ROOT/'policy_data'; out.mkdir(exist_ok=True)
    (out/'policy_jobs.json').write_text(json.dumps({'updated_at':datetime.now(timezone.utc).isoformat(),'source_health':health,'jobs':jobs},indent=2))
    esc=lambda x:html.escape(str(x))
    parts=['<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Weekly Policy Job Scan</title><style>body{font:17px system-ui;max-width:1000px;margin:40px auto;padding:0 20px;color:#183329;background:#f5f7f4}article{background:white;padding:20px;margin:12px 0;border-radius:12px}a{color:#156651}small{color:#52655a}</style><h1>Weekly Policy Job Scan</h1><p>Atlanta / Georgia · U.S.-wide / remote · $65,000+<br>Match labels describe job requirements, not verified résumé fit. Remote eligibility and location restrictions must be checked.</p><p>Updated '+esc(datetime.now(timezone.utc).isoformat())+'</p>']
    parts.append('<details><summary>Source health</summary><pre>'+esc(json.dumps(health,indent=2))+'</pre></details>')
    for group in ['Strong match','Stretch','Needs review']:
        selected=[j for j in jobs if j['match_group']==group]
        selected.sort(key=lambda j: (not bool(re.search(r'Atlanta|Georgia|, GA\b',j.get('location',''),re.I)),j.get('title','')))
        parts.append('<h2>'+group+' ('+str(len(selected))+')</h2>')
        for j in selected:
            u=j.get('preferred_url','')
            link=('<a target="_blank" rel="noopener noreferrer" href="'+esc(u)+'">'+esc(j.get('title'))+'</a>') if urlsplit(u).scheme in ('http','https') else esc(j.get('title'))
            parts.append('<article><h3>'+link+'</h3><p>'+esc(j.get('company'))+' · '+esc(j.get('location'))+'</p><p>'+esc(j.get('salary') or 'Salary unlisted')+'</p><small>'+esc(j['match_reason'])+' · Link: '+esc(j['link_status'])+'</small></article>')
    parts.append('<p>Closed, below-floor and off-target results are omitted; audit records remain in policy_data/policy_jobs.json.</p>')
    (ROOT/'index.html').write_text('\n'.join(parts))

def main():
    import scrape_jobs as s
    s.OUTPUT_DIR = str(ROOT / "policy_data")
    Path(s.OUTPUT_DIR).mkdir(exist_ok=True)
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('--recheck-only',action='store_true'); args=p.parse_args()
    health={}; discovered=[]
    if not args.recheck_only:
        sources=[('HiringCafe',lambda:s.scrape_hiringcafe_recent(days=8)),('USAJOBS',s.scrape_usajobs_recent),('NEOGOV',lambda:s.scrape_governmentjobs_recent(days=8)),('LinkedIn',lambda:s._linkedin_search(list(s.LINKEDIN_SEARCH_TERMS),8*86400)[0])]
        for name,fn in sources:
            try:
                found=fn(); discovered.extend(found); health[name]={'count':len(found),'status':'returned results' if found else 'zero results; may be blocked or no matches'}
            except Exception as e: health[name]={'count':0,'status':'failed: '+type(e).__name__}
        s._merge_into_all_jobs(discovered)
    path=ROOT/'policy_data/all_jobs.json'
    jobs=json.loads(path.read_text()).get('jobs',[]) if path.exists() else []
    jobs=s._dedupe_master_jobs(jobs)[0]
    # Skip irrelevant titles before any network verification, then recheck every retained job weekly.
    candidates=[j for j in jobs if TOPIC.search(j.get('title','')+' '+j.get('description',''))]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool: checked=list(pool.map(validate,candidates))
    publish(checked,health)
    if not args.recheck_only and not discovered: raise SystemExit('No source returned jobs. Dashboard preserves prior results; inspect source health.')

if __name__=='__main__': main()
