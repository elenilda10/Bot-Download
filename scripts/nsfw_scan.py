"""Small CLI to test NudeNet on local images before enabling Telegram moderation.

Usage: python -m scripts.nsfw_scan /path/to/image.jpg --sensitivity balanced
"""
import argparse
import json

from nudenet import NudeDetector

from services.moderation.policy import find_violation


def main():
    parser = argparse.ArgumentParser(description="Classify a local image using NudeNet")
    parser.add_argument("image", help="Local image path")
    parser.add_argument("--sensitivity", choices=("relaxed", "balanced", "strict"),
                        default="balanced")
    args = parser.parse_args()
    detector = NudeDetector()
    detections = detector.detect(args.image)
    violation = find_violation(detections, args.sensitivity)
    print(json.dumps({
        "nsfw": bool(violation),
        "sensitivity": args.sensitivity,
        "violation": {"class": violation[0], "score": violation[1]} if violation else None,
        "detections": detections,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
