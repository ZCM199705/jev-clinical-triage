import unittest
from scripts.manuscript_figure_data import classify,stability_class,selected_risk,risk_curve

class FigureDataTests(unittest.TestCase):
    def setUp(self):
        self.rows=[{'confidence':.9,'correct':True,'scenario_id':'s1'},
                   {'confidence':.9,'correct':False,'scenario_id':'s1'},
                   {'confidence':.7,'correct':True,'scenario_id':'s2'}]
    def test_ties_are_not_split(self):
        c=risk_curve(self.rows,4)
        self.assertEqual([r['accepted'] for r in c],[2,3])
        self.assertEqual(c[0]['risk'],.5)
    def test_threshold_and_failure_denominator(self):
        r=selected_risk(self.rows,.9,4)
        self.assertEqual(r['accepted'],2)
        self.assertEqual(r['coverage_valid'],2/3)
        self.assertEqual(r['coverage_planned'],.5)
        self.assertEqual(r['accepted_scenarios'],1)
    def test_empty_is_not_zero_risk(self):
        self.assertIsNone(selected_risk(self.rows,1,4)['risk'])
    def test_c_d_set(self):
        self.assertEqual(classify('C',['C','D']),'correct')
        self.assertEqual(classify('D',['C','D']),'correct')
        self.assertEqual(classify('C',['D']),'undertriage')
        self.assertEqual(classify(None,['D']),'technical_failure')
    def test_stable_wrong_and_continuous_failure(self):
        self.assertEqual(stability_class(['C']*3,['D']),'stable_incorrect')
        self.assertEqual(stability_class([None]*3,['D']),'technical_failure')
        self.assertEqual(stability_class(['C','D','C'],['C','D']),'changed_all_acceptable')
        self.assertEqual(stability_class(['C','D','C'],['D']),'changed_any_incorrect')

if __name__=='__main__':unittest.main()
