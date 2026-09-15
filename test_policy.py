import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import policy_scan as p
class PolicyTests(unittest.TestCase):
 def job(self,**kw):
  return dict({'title':'Education Policy Analyst','description':'Research education policy','salary':'$65,000 - $85,000 / year','link_status':'live'},**kw)
 def test_floor(self):
  self.assertEqual(p.salary_status('$60,000 - $64,999 / year'),'below floor')
  self.assertEqual(p.salary_status('$60,000 - $85,000 / year'),'review')
  self.assertEqual(p.salary_status('$65k - $85k'),'meets floor')
  self.assertEqual(p.salary_status('$40 / hour'),'review')
  self.assertEqual(p.salary_status('CAD 90000/year'),'review')
 def test_matches(self):
  self.assertEqual(p.classify(self.job())[0],'Strong match')
  j=self.job();j['title']='Director of Education Policy';self.assertEqual(p.classify(j)[0],'Stretch')
  j=self.job();j['title']='Clinical Research Associate';self.assertEqual(p.classify(j)[0],'Excluded')
  j=self.job();j['title']='AI Governance Fellow';self.assertEqual(p.classify(j)[0],'Needs review')
 def test_career_calibration(self):
  j=self.job();j['title']='Senior Education Policy Analyst';self.assertEqual(p.classify(j)[0],'Stretch')
  j=self.job();j['description']='Requires 5 years of professional experience';self.assertEqual(p.classify(j)[0],'Stretch')
  j=self.job();j['title']='Survey Research Analyst';self.assertEqual(p.classify(j)[0],'Strong match')
 def test_unknown_not_live(self):
  j=self.job();j['link_status']='unknown';self.assertEqual(p.classify(j)[0],'Needs review')
 def test_page_signals(self):
  self.assertEqual(p.inspect_page('Careers homepage',200,'https://example.com/careers','Policy Analyst'),'unknown')
  self.assertEqual(p.inspect_page('Policy Analyst apply now',200,'https://example.com/job/12','Policy Analyst'),'live')
  self.assertEqual(p.inspect_page('This position has been filled',200,'https://example.com/job/12','Policy Analyst'),'closed')
  self.assertEqual(p.inspect_page('',403,'https://example.com/job/12','Policy Analyst'),'unknown')
 def test_transient_failure(self):
  with patch.object(p,'public_url'),patch.object(p.urllib.request,'build_opener') as op:
   op.return_value.open.side_effect=TimeoutError()
   self.assertEqual(p.validate({'url':'https://example.com/job/12'})['link_status'],'unknown')
 def test_dead_official_with_aggregator(self):
  with patch.object(p,'public_url'),patch.object(p.urllib.request,'build_opener') as op:
   op.return_value.open.side_effect=HTTPError('https://example.com/job/12',410,'gone',{},None)
   self.assertEqual(p.validate({'url':'https://linkedin.com/jobs/12','direct_url':'https://example.com/job/12'})['link_status'],'closed')
 def test_canonical_preserves_id(self):
  self.assertEqual(p.canonical('https://example.com/job?id=12&utm_source=test'),'https://example.com/job?id=12')
 def test_direct_url_dedupe(self):
  import scrape_jobs as scraper
  a={'url':'https://linkedin.com/jobs/view/123', 'direct_url':'https://boards.greenhouse.io/example/jobs/456','title':'Policy Analyst','company':'Example'}
  b={'url':'https://boards.greenhouse.io/example/jobs/456','title':'Different title','company':'Example'}
  self.assertTrue(scraper._same_job(a,b))
 def test_expired_metadata(self):
  self.assertEqual(p.inspect_page('Policy Analyst apply now <script>{"validThrough":"2020-01-01"}</script>',200,'https://example.com/jobs/1','Policy Analyst'),'closed')
 def test_public_url(self):
  with self.assertRaises(ValueError): p.public_url('file:///etc/passwd')
if __name__=='__main__': unittest.main()
