"""Chart-only retention policy. Publication and pin manifests are inputs.
No time-based deletion, no numerical or metadata retirement."""
def eligible(generations,pinned=()):
    if len(generations)<3:return set()
    registered=set().union(*(set(g) for g in generations))
    keep=set(generations[-1])|set(generations[-2])|set(pinned)
    return registered-keep
def assert_inventory(generations,active,pinned=(),already_retired=()):
    registered=set().union(*(set(g) for g in generations))
    active=set(active);retired=set(already_retired)
    if active-registered:raise ValueError("Unknown chart pack")
    if retired & active or retired-eligible(generations,pinned):raise ValueError("Invalid prior retirement")
    if registered-active!=retired:raise ValueError("Missing unjournaled chart pack")
    return eligible(generations,pinned)-retired
