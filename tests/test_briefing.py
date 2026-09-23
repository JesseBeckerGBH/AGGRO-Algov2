from research_os.briefing import _enforce_class_labels, render
from research_os.models import Mission, Result

MISSION = Mission(id="m", objective="track competitor pricing",
                  success_condition="x", novelty_requirement="high")


def _r(title, domain, cls, angle="technical"):
    r = Result(title=title, url=f"https://{domain}/x", snippet="s")
    r.domain = domain
    r.source_class = cls
    r.query_angle = angle
    r.final_score = 0.5
    return r


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


def test_unpaired_aggregator_disconfirming_gets_flagged_unverified():
    """source-classes.yaml: an aggregator result may enter a briefing only
    when the upstream primary source has also been retrieved and cited.
    With no stronger-class corroboration, it must be labeled, not hidden."""
    strong = _r("primary claim", "vendor.com", "primary")
    weak = _r("blog take", "somesite.example", "aggregator", angle="disconfirming")
    brief = render(MISSION, [strong, weak], top=6, retrieved=10)
    assert "unverified signal" in brief.body
    assert "somesite.example" in brief.body  # still surfaced, just flagged


def test_disconfirming_paired_with_primary_is_not_flagged():
    strong = _r("primary claim", "vendor.com", "primary")
    strong_contra = _r("official contradicting statement", "vendor.com",
                       "primary", angle="disconfirming")
    weak = _r("blog take", "somesite.example", "aggregator", angle="disconfirming")
    brief = render(MISSION, [strong, strong_contra, weak], top=6, retrieved=10)
    assert "unverified signal" not in brief.body
