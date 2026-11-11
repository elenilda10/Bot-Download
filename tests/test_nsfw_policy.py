from services.moderation.policy import find_violation


def test_explicit_detected():
    assert find_violation([{"class": "MALE_GENITALIA_EXPOSED", "score": 0.96}]) == (
        "MALE_GENITALIA_EXPOSED", 0.96)


def test_covered_skin_ignored():
    assert find_violation([{"class": "FEMALE_BREAST_COVERED", "score": 1.0},
                           {"class": "FACE_FEMALE", "score": 1.0}]) is None


def test_low_confidence_ignored():
    assert find_violation([{"class": "FEMALE_BREAST_EXPOSED", "score": 0.60}]) is None
