"""
Hand-written injected-error test fixtures for the faithfulness verifier.

Each case pairs an answer with context and an expected outcome:
  - "PASS": answer is fully supported by context -> verifier should pass it
  - "FAIL": answer contains a claim the context contradicts or doesn't
    support -> verifier should flag it
"""

TEST_CASES = [
    {
        "id": "t01",
        "entity": "Apple",
        "question": "What does Apple identify as a key supply chain risk?",
        "answer": "Apple identifies reliance on single or limited sources for many "
                  "critical components as a key supply chain concentration risk.",
        "context": "Difficulties and delays in manufacturing, internally, through "
                    "third-party providers or otherwise within the supply chain... "
                    "The Company's reliance on single or limited sources for the "
                    "supply and manufacture of many critical components subjects it "
                    "to significant risk.",
        "expected": "PASS",
    },
    {
        "id": "t02",
        "entity": "Apple",
        "question": "What does Apple identify as a key supply chain risk?",
        "answer": "Apple has no supply chain risks and sources all components from "
                  "multiple redundant domestic suppliers.",
        "context": "The Company's reliance on single or limited sources for the "
                    "supply and manufacture of many critical components subjects it "
                    "to significant risk of business interruption.",
        "expected": "FAIL",
    },
    {
        "id": "t03",
        "entity": "Johnson & Johnson",
        "question": "What legal proceedings does JNJ disclose related to talc litigation?",
        "answer": "JNJ discloses that it faces numerous lawsuits alleging that its "
                  "talc-based products caused ovarian cancer and mesothelioma.",
        "context": "The Company is a defendant in numerous lawsuits... alleging that "
                    "talc, including talc used in the Company's consumer products, "
                    "causes ovarian cancer, mesothelioma, and other cancers.",
        "expected": "PASS",
    },
    {
        "id": "t04",
        "entity": "Johnson & Johnson",
        "question": "What legal proceedings does JNJ disclose related to talc litigation?",
        "answer": "JNJ has fully resolved all talc-related litigation with a final "
                    "settlement approved by every court and no remaining lawsuits.",
        "context": "The Company is a defendant in numerous lawsuits... alleging that "
                    "talc causes ovarian cancer and mesothelioma. Trials are "
                    "ongoing in multiple jurisdictions.",
        "expected": "FAIL",
    },
    {
        "id": "t05",
        "entity": "Microsoft",
        "question": "What cybersecurity risks does Microsoft highlight?",
        "answer": "Microsoft highlights that cyberattacks against its cloud services "
                    "infrastructure could result in service disruption, data breaches, "
                    "and reputational harm.",
        "context": "Cyberattacks, which have generally increased as a result of the "
                    "growing sophistication of nation-state actors and criminal "
                    "organizations, against our datacenters, network infrastructure, "
                    "and cloud services could result in interruption of services, "
                    "unauthorized data access, and damage to our reputation.",
        "expected": "PASS",
    },
    {
        "id": "t06",
        "entity": "Microsoft",
        "question": "What cybersecurity risks does Microsoft highlight?",
        "answer": "Microsoft states that its systems have never been targeted by "
                    "nation-state actors and cybersecurity is not a material risk.",
        "context": "Cyberattacks against our datacenters and cloud services, "
                    "including by nation-state actors, could result in interruption "
                    "of services and damage to our reputation.",
        "expected": "FAIL",
    },
    {
        "id": "t07",
        "entity": "JPMorgan Chase",
        "question": "What does JPMorgan disclose about capital requirements?",
        "answer": "JPMorgan discloses it is subject to regulatory capital "
                    "requirements, including risk-based capital and leverage ratios "
                    "set by U.S. banking regulators.",
        "context": "The Firm is subject to regulatory capital rules, including "
                    "risk-based capital and leverage requirements, issued by the "
                    "Federal Reserve and other U.S. banking regulators.",
        "expected": "PASS",
    },
    {
        "id": "t08",
        "entity": "JPMorgan Chase",
        "question": "What does JPMorgan disclose about capital requirements?",
        "answer": "JPMorgan is exempt from all regulatory capital requirements "
                    "because of its size and market position.",
        "context": "The Firm is subject to regulatory capital rules, including "
                    "risk-based capital and leverage requirements.",
        "expected": "FAIL",
    },
    {
        "id": "t09",
        "entity": "Pfizer",
        "question": "What patent risks does Pfizer disclose?",
        "answer": "Pfizer discloses that the expiration of patent protection on key "
                    "products could lead to a significant decline in revenue as "
                    "generic competitors enter the market.",
        "context": "Upon expiration (or loss) of patent protection... we can expect "
                    "increased competition from generic products, which typically "
                    "leads to a rapid and severe decline in revenues for the "
                    "affected product.",
        "expected": "PASS",
    },
    {
        "id": "t10",
        "entity": "Pfizer",
        "question": "What patent risks does Pfizer disclose?",
        "answer": "Pfizer's patents are all permanent and do not expire, so generic "
                    "competition is not a concern.",
        "context": "Upon expiration of patent protection, we can expect increased "
                    "competition from generic products, which typically leads to a "
                    "rapid decline in revenues for the affected product.",
        "expected": "FAIL",
    },
    {
        "id": "t11",
        "entity": "Apple",
        "question": "What does Apple say about litigation risk generally?",
        "answer": "Apple states it is currently subject to various legal proceedings "
                    "and claims that arise in the ordinary course of business, some "
                    "of which could be significant.",
        "context": "The Company is subject to various legal proceedings and claims "
                    "that arise in the ordinary course of business. The outcome of "
                    "litigation is inherently uncertain.",
        "expected": "PASS",
    },
    {
        "id": "t12",
        "entity": "Apple",
        "question": "What does Apple say about litigation risk generally?",
        "answer": "Apple has never faced any legal proceedings in its entire "
                    "corporate history.",
        "context": "The Company is subject to various legal proceedings and claims "
                    "that arise in the ordinary course of business.",
        "expected": "FAIL",
    },
]
