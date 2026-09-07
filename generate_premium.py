import json
with open('plans/004-seat-selection/candidates.json') as f:
    d = json.load(f)

with open('plans/004-seat-selection/premium.json') as f:
    p = json.load(f)

premium_set = set(p['premium'])
standard_set = set(p['standard'])

candidates_premium = {}
for role, arms in d.items():
    for arm in arms:
        model = arm.split('@')[0]
        if model in premium_set:
            candidates_premium[arm] = True
        elif model in standard_set:
            candidates_premium[arm] = False
        else:
            candidates_premium[arm] = "unknown"

with open('plans/004-seat-selection/candidates-premium.json', 'w') as f:
    json.dump(candidates_premium, f, indent=2)

