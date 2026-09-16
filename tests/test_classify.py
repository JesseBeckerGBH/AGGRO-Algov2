from research_os.classify import classify_one
from research_os.models import Mission, Result


def _r(url: str) -> Result:
    r = Result(title="t", url=url, snippet="s")
    r.domain = url.split("//", 1)[1].split("/", 1)[0].removeprefix("www.")
    return r


def test_known_primary_domains():
    assert classify_one(_r("https://arxiv.org/abs/2401.1"))[0] == "primary"
    assert classify_one(_r("https://github.com/org/repo"))[0] == "primary"


def test_tld_pattern_primary_and_secondary():
    assert classify_one(_r("https://nasa.gov/mission"))[0] == "primary"
    cls, sig = classify_one(_r("https://cs.stanford.edu/paper"))
    assert cls == "high_trust_secondary" and sig == "domain_pattern"


def test_community_and_default():
    assert classify_one(_r("https://reddit.com/r/tennis/x"))[0] == "community"
    cls, sig = classify_one(_r("https://some-unknown-blog.example/post"))
    assert cls == "aggregator" and sig == "default"


def test_first_party_vendor_domain_is_primary_when_mission_is_about_it():
    mission = Mission(
        id="m", objective="Track Salesforce's price increases",
        success_condition="x", vocabulary_seed=["salesforce pricing increase"],
    )
    cls, sig = classify_one(_r("https://help.salesforce.com/pricing"), mission)
    assert cls == "primary" and sig == "first_party_domain"
    # unrelated mission: same domain, no first-party boost -> falls to default
    other = Mission(id="m2", objective="Track tennis outcomes", success_condition="x")
    cls2, sig2 = classify_one(_r("https://help.salesforce.com/pricing"), other)
    assert cls2 == "aggregator" and sig2 == "default"


def test_short_domain_root_does_not_false_positive_first_party():
    mission = Mission(id="m", objective="Track io devices", success_condition="x")
    cls, sig = classify_one(_r("https://some.io/post"), mission)
    assert sig != "first_party_domain"
