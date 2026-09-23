import unittest
from postanalysis.natural_coding import validate_coding, consensus_judgement

class NaturalCodingAnalysisTests(unittest.TestCase):
    def test_consensus_disagreement(self):
        rows=[{"status":"success","parsed":{"status":"mapped","triage":"C","evidence_quote":"within 48 hours"}}, {"status":"success","parsed":{"status":"mapped","triage":"D","evidence_quote":"ER now"}}]
        self.assertEqual(consensus_judgement(rows,"within 48 hours; go to the ER now")["reason"],"disagreement")
    def test_unmapped_and_invalid_quote(self):
        self.assertFalse(validate_coding({"status":"mapped","triage":"C","evidence_quote":"not here"},"within 48 hours")["valid"])
        rows=[{"status":"success","parsed":{"status":"no_timing","triage":None,"evidence_quote":None}}, {"status":"success","parsed":{"status":"no_timing","triage":None,"evidence_quote":None}}]
        self.assertFalse(consensus_judgement(rows,"no recommendation")["consensus"])
    def test_failure_and_c_d_set(self):
        rows=[{"status":"request_error"},{"status":"success","parsed":{"status":"mapped","triage":"D","evidence_quote":"ER now"}}]
        self.assertEqual(consensus_judgement(rows,"ER now")["reason"],"technical_failure:request_error")
        for x in "CD":
            self.assertTrue(validate_coding({"status":"mapped","triage":x,"evidence_quote":"within 48 hours"},"within 48 hours")["valid"])

if __name__ == "__main__": unittest.main()
