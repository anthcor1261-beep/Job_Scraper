"""Small curated set of public employer boards, fetched without aggregator redirects."""
import html
import json
import re
import urllib.request
from datetime import datetime, timezone

BOARDS = {'codeforamerica': 'Code for America', 'democracyworks': 'Democracy Works', 'democracyforward': 'Democracy Forward'}

def normalize(raw, company):
    import policy_scan as p
    import scrape_jobs as s
    title = raw.get('title', '')
    body = html.unescape(html.unescape(raw.get('content', '')))
    body = re.sub('<[^>]+>', ' ', body)
    body = re.sub(r'\s+', ' ', body).strip()
    loc = (raw.get('location') or {}).get('name', '')
    if not s.is_target_location(loc) or not p.TOPIC.search(title + ' ' + body):
        return None
    if re.search(r'engineer|sales|account executive|finance|clinical|attorney|paralegal|legal fellowship|legal director', title, re.I):
        return None
    if not (p.EARLY.search(title) or p.TOPIC.search(title) or re.search('program|product|research', title, re.I)):
        return None
    # Retain only an explicit dollar range; ambiguous compensation remains review-only.
    salary = re.search(r'\$[\d,]+(?:\.\d{2})?\s*(?:-|–|—|to)\s*\$[\d,]+(?:\.\d{2})?', body)
    return {'title': title, 'company': company, 'location': loc, 'description': body,
            'url': raw.get('absolute_url', ''), 'direct_url': raw.get('absolute_url', ''),
            'salary': salary.group(0) if salary else '', 'ats': 'Greenhouse',
            'date_posted': '', 'source_updated_at': raw.get('updated_at', ''),
            'discovered_at': datetime.now(timezone.utc).isoformat()}

def scrape_board(slug):
    import policy_scan as p
    url = 'https://boards-api.greenhouse.io/v1/boards/' + slug + '/jobs?content=true'
    p.public_url(url)
    with urllib.request.build_opener(p.SafeRedirect()).open(url, timeout=20) as response:
        payload = json.load(response)
    return [job for raw in payload['jobs'] if (job := normalize(raw, BOARDS[slug]))]
