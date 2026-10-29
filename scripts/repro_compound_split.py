"""Print the real Engine 2 analysis for the reported citizen-portal input."""
from core.intake import process
from core.router_baseline import route
from core.splitter import propose_split


TEXT = "road has a pothole, and the streetlight is also not working in ward 3"


def main():
    proposal = propose_split(TEXT)
    whole = process(TEXT)
    print(f"original={TEXT!r}")
    print(f"whole_complaint_category={whole['issue']!r}")
    print(f"whole_complaint_route={[item['dept'] for item in route(TEXT)['top3']]}")
    print(f"segmentation_and_per_clause_analysis={len(proposal['parts'])} parts")
    for index, part in enumerate(proposal["parts"], 1):
        top3 = part["suggestion"]["top3"]
        print(f"{index}. offsets=({part['start']},{part['end']}) text={part['text']!r}")
        print(f"   issue={part['issue']!r} department={part['department']!r}")
        print(f"   top3={[(item['dept'], item['score']) for item in top3]!r}")
        print(f"   evidence={part['suggestion']['evidence'].get(part['department'], [])!r}")
    print(f"is_compound={proposal['is_compound']} note={proposal['note']!r}")


if __name__ == "__main__":
    main()
