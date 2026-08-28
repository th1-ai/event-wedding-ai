# Function spaces - Hotel Aurora

<!--
Reference only - the agent reads config/agent.yaml: spaces: / rates:, not
this file. Copy this to knowledge/event-spaces.md and keep it in sync by
hand; it is what a human reads before a site visit.
-->

## Hotel install (this repo's fixtures)

| Space | Best for | Capacities |
|---|---|---|
| Grand Ballroom | Weddings, large conferences | banquet 160, theatre 200, cabaret 110, standing 240 |
| Garden Terrace | Ceremonies, receptions | standing 120, banquet 80 |
| Private Dining Room | Small dinners, board dinners | private dining 22, composite 36 (with the restaurant's own tables) |
| Boardroom | Board meetings | boardroom 12 |
| Garden Suite | Away-days, small offsites | theatre 36, cabaret 20 |

Only the Grand Ballroom and the Garden Terrace carry the wedding Saturday
uplift (`config/agent.yaml: wedding_saturday_uplift`).

## Restaurant install - what changes

A restaurant-only property does not have a ballroom or a boardroom. Trim
`config/agent.yaml: spaces:` to two rows:

```yaml
spaces:
  - slug: private-dining-room
    name: "Private Dining Room"
    capacities: {private_dining: 22, composite: 36}
  - slug: whole-venue
    name: "Whole Restaurant (exclusive use)"
    capacities: {seated: 80, standing: 120}
```

and drop `wedding_saturday_uplift`/`uplift_spaces` (there is no wedding venue
hire to uplift). Use the `offsite` or `meeting` checklist templates - they are
the right length for a private dinner or a launch party. `tools/beo.py:
build_beo()` still runs unchanged; call the artefact the kitchen running order
instead of a BEO if you like, the sections are the same.
