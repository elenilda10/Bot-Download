"""Conservative NudeNet thresholds; ignore covered body parts and faces."""
THRESHOLDS = {
    "relaxed": {"FEMALE_GENITALIA_EXPOSED": .93, "MALE_GENITALIA_EXPOSED": .93,
                "ANUS_EXPOSED": .93, "FEMALE_BREAST_EXPOSED": .97, "BUTTOCKS_EXPOSED": .99},
    "balanced": {"FEMALE_GENITALIA_EXPOSED": .82, "MALE_GENITALIA_EXPOSED": .82,
                 "ANUS_EXPOSED": .85, "FEMALE_BREAST_EXPOSED": .92, "BUTTOCKS_EXPOSED": .96},
    "strict": {"FEMALE_GENITALIA_EXPOSED": .72, "MALE_GENITALIA_EXPOSED": .72,
               "ANUS_EXPOSED": .76, "FEMALE_BREAST_EXPOSED": .83, "BUTTOCKS_EXPOSED": .89},
}


def find_violation(detections, sensitivity="balanced"):
    thresholds = THRESHOLDS.get(sensitivity, THRESHOLDS["balanced"])
    best = None
    for item in detections if isinstance(detections, list) else ():
        if not isinstance(item, dict):
            continue
        name = str(item.get("class", "")).upper()
        minimum = thresholds.get(name)
        if minimum is None:
            continue
        try:
            score = float(item.get("score", 0))
        except (TypeError, ValueError):
            continue
        if 0 <= score <= 1 and score >= minimum and (best is None or score > best[1]):
            best = (name, score)
    return best
