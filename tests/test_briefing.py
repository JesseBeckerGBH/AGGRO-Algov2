from research_os.briefing import _enforce_class_labels


def test_corrects_a_relabeled_class():
    body = "Salesforce cut prices (salesforce.com, primary) per the filing."
    known = {"salesforce.com": "aggregator"}
    out = _enforce_class_labels(body, known)
    assert "(salesforce.com, aggregator)" in out
    assert "(salesforce.com, primary)" not in out


def test_leaves_correct_labels_alone():
    body = "See the paper (arxiv.org, primary) for details."
    out = _enforce_class_labels(body, {"arxiv.org": "primary"})
    assert out == body


def test_leaves_unknown_domains_alone():
    body = "A claim (unseen.example, primary) with no record."
    out = _enforce_class_labels(body, {"arxiv.org": "primary"})
    assert out == body
