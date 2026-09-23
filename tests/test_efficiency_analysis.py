import unittest
from scripts.analyze_efficiency import costs, metrics

class EfficiencyAnalysisTests(unittest.TestCase):
    def test_failures_stay_in_planned_and_costs(self):
        rs=[{'status':'success','transport_ms':100,'cost_usd':'0.01'},
            {'status':'parse_error','transport_ms':500,'cost_usd':'0.02'},
            {'status':'request_error','transport_ms':900,'cost_usd':None}]
        m=metrics(rs,2000)
        self.assertEqual(m['planned'],3)
        self.assertEqual(m['valid_outputs'],1)
        self.assertEqual(m['effective_outputs_per_second'],.5)
        self.assertEqual(m['valid_http_p50_ms'],100)
        self.assertEqual(m['all_attempt_http_p50_ms'],500)
        self.assertEqual(m['known_usd'],'0.03')
        self.assertEqual(m['unknown_requests'],1)
        self.assertEqual(m['known_usd_per_1000_planned'],10)
    def test_no_valid_answers(self):
        m=metrics([{'status':'parse_error','transport_ms':100,'cost_usd':'0.01'}],1000)
        self.assertIsNone(m['valid_http_p50_ms'])
        self.assertEqual(m['effective_outputs_per_second'],0)
    def test_unknown_not_zero(self):
        self.assertEqual(costs([{'cost_usd':None}])['unknown_requests'],1)

if __name__=='__main__':unittest.main()
