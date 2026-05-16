from src.version import Version


def test_from_str_basic():
    v = Version.from_str("1.2.3")
    assert (v.major, v.minor, v.patch, v.rc) == (1, 2, 3, None)
    assert str(v) == "1.2.3"


def test_from_str_rc():
    v = Version.from_str("2.5.0rc7")
    assert (v.major, v.minor, v.patch, v.rc) == (2, 5, 0, 7)
    assert str(v) == "2.5.0rc7"


def test_from_str_tag_prefix():
    v = Version.from_str("v2.5.0rc7")
    assert (v.major, v.minor, v.patch, v.rc) == (2, 5, 0, 7)
    assert str(v) == "2.5.0rc7"


def test_from_str_legacy_hyphenated_rc_normalized_to_canonical():
    v = Version.from_str("2.5.0-rc7")
    assert (v.major, v.minor, v.patch, v.rc) == (2, 5, 0, 7)
    assert str(v) == "2.5.0rc7"


def test_from_str_legacy_beta_normalized_to_rc():
    v = Version.from_str("2.5.0-beta7")
    assert (v.major, v.minor, v.patch, v.rc) == (2, 5, 0, 7)
    assert str(v) == "2.5.0rc7"


def test_str_with_rc_zero_preserved():
    v = Version(1, 0, 0, rc=0)
    assert str(v) == "1.0.0rc0"


def test_comparison_rc_precedes_final():
    final = Version.from_str("1.0.0")
    rc1 = Version.from_str("1.0.0rc1")
    assert rc1 < final
    assert final > rc1


def test_comparison_different_components():
    v1 = Version.from_str("1.0.1")
    v2 = Version.from_str("1.1.0")
    v3 = Version.from_str("2.0.0")
    assert v1 < v2 < v3
    assert v3 > v2 > v1


def test_le_ge_and_equality():
    a = Version.from_str("3.4.5rc2")
    b = Version.from_str("3.4.5rc2")
    c = Version.from_str("3.4.5rc3")
    assert a == b
    assert a <= b
    assert a < c
    assert c >= a
    assert a != c


def test_equality_with_non_version():
    v = Version.from_str("1.2.3")
    assert (v == "1.2.3") is False


def test_ordering_with_non_version_returns_not_implemented():
    v = Version.from_str("1.2.3")

    assert Version.__lt__(v, "1.2.3") is NotImplemented
    assert Version.__gt__(v, "1.2.3") is NotImplemented


def test_ordering_multiple_rcs_before_final():
    final = Version.from_str("1.2.3")
    rc1 = Version.from_str("1.2.3rc1")
    rc2 = Version.from_str("1.2.3rc2")
    assert rc1 < rc2 < final


def test_round_trip_str():
    original = Version(9, 8, 7, rc=4)
    parsed = Version.from_str(str(original))
    assert original == parsed
