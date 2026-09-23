import unittest
from unittest.mock import patch
from postanalysis.core import clinical_summary, paired_values, stability, bootstrap, scored
from analysis.paired import holm_adjust, paired_sign_flip_pvalue

def case(s='s1', gold=None):
    return {'scenario_id':s,'dataset_id':'nm_main','label_type':'clear','ai_source_review_flag':False,'acceptable_labels':gold or ['D']}

def result(p='D',status='success'):
    return {'status':status,'parsed':{'triage':p}}

class ThreeRoundTests(unittest.TestCase):
    def test_set_labels_and_d_to_c(self):
        self.assertTrue(scored(case(gold=['C','D']),result('C'))['correct'])
        self.assertTrue(scored(case(),result('C'))['d_to_non_d'])
        self.assertFalse(scored(case(),result('D','parse_error'))['valid_decision'])

    def test_equal_scenario_not_request_weighting(self):
        cases={'a':case('s1'),'b':case('s1'),'c':case('s2')}
        results={(n,'jev',v):result('D' if v!='c' else 'A') for n in (1,2,3) for v in cases}
        summary=clinical_summary(cases,results,[1,2,3],'jev','main_clear')
        self.assertEqual(summary['correct_rate_full_plan'],2/3)
        self.assertEqual(summary['scenario_equal_full_plan']['estimate'],.5)

    def test_average_within_input_before_scenario(self):
        cases={'a':case(),'b':case()}
        results={(n,'jev',v):result() for n in (1,2,3) for v in cases}
        results[1,'jev','a']=result('A')
        results[2,'jev','a']=result(status='timeout')
        results[3,'jev','a']=result(status='timeout')
        s=clinical_summary(cases,results,[1,2,3],'jev','main_clear')
        self.assertEqual(s['scenario_equal_valid']['estimate'],.5)
        self.assertEqual(s['valid_decisions'],4)
        self.assertEqual(s['invalid_or_missing_decisions'],2)

    def test_failed_rounds_not_agreement(self):
        cases={'a':case()}
        results={(n,r,'a'):result(status='timeout') for n in (1,2,3) for r in ('jev','luna','gemini','deepseek')}
        rows=stability(cases,results)
        x=next(x for x in rows if x['layer']=='main_clear' and x['role']=='jev' and x['rounds']=='1-2-3')
        self.assertEqual(x['valid_inputs'],0)
        self.assertEqual(x['identical'],0)
        self.assertIsNone(x['agreement_rate_valid'])

    def test_pair_invalid_full_plan_and_round_key(self):
        cases={'a':case()}
        results={(n,r,'a'):result() for n in (1,2,3) for r in ('jev','luna')}
        results[2,'luna','a']=result(status='timeout')
        common,den=paired_values(cases,results,[1,2,3],'jev','luna','main_clear',True)
        full,_=paired_values(cases,results,[1,2,3],'jev','luna','main_clear',False)
        self.assertEqual(common,[0])
        self.assertEqual(full,[1/3])
        self.assertEqual(den['common_valid_request_pairs'],2)

    def test_holm_and_exact_sign_flip(self):
        self.assertEqual(holm_adjust([.04,.01]),[.04,.02])
        self.assertEqual(paired_sign_flip_pvalue([1,1,1]),.25)
        self.assertEqual(bootstrap([.1,.4,.8]),bootstrap([.1,.4,.8]))

if __name__=='__main__': unittest.main()
