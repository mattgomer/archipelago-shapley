# AGENTS.md

## Project

This repository estimates intrinsic item usefulness in Archipelago randomizers
using Shapley values over logical reachability. It targets the pinned
Archipelago 0.6.7 source and runs on native Windows with Python 3.13.

Read `README.md` for user-facing setup and commands. This file records the
implementation constraints that should guide future changes.

## Canonical methodology

For one generated world, let `v(S)` be the number of scored checks reachable
with inventory `S`. The analyzer estimates each physical item's average
marginal contribution over uniformly random permutations of all physical
logical item copies.

Physical-copy Shapley is canonical:

- every physical copy in a logically relevant item family is a player;
- copies can interleave freely with every other family;
- counted and progressive stages therefore occur naturally;
- results distinguish `shapley_per_copy` from `family_total`;
- tables and plots rank by `shapley_per_copy` unless explicitly requested.

Do not reintroduce Owen/family-block sampling as the default. Contiguous family
blocks remove valid states such as `stage 1 -> other item -> stage 2` and
distort progressive and count-based logic.

### Repeated-copy limitation

For the isolated gate `A AND at least k copies of F`, with one copy of `A` and
`n` physical copies of `F`:

```text
phi_A       = (n + 1 - k) / (n + 1)
phi_F_total = k / (n + 1)
phi_F_copy  = k / (n * (n + 1))
```

This can overemphasize a singleton complement when repeated copies are
redundant substitutes, as with one required Ocarina among two. It is desirable
for meaningful thresholds such as Progressive Strength levels or Emblem
counts. We accept the redundant-substitute limitation rather than eliminate
legitimate interleavings with a generic family-block workaround.

## Sampling and conservation

`shapley_pairs=N` means `N` antithetic pairs: a uniformly random physical-copy
permutation and its reverse, for `2N` total permutations per world.

`world_samples=N` independently generates `N` legal worlds from the same YAML.
Weighted/random YAML options are rerolled deterministically from one master
seed. An item's cross-world mean is conditional on that item existing; absence
is not recorded as a zero.

Every item-dependent scored check contributes exactly one point:

```text
sum(family_total) = item_dependent_checks
```

Healthy runs should report:

```text
pending_constrained_with_all_offered_max = 0
still_unreachable_max = 0
max_abs_conservation_difference ~= 0
goal_reachable_with_all_items_all = True
```

Do not weaken these invariants globally to accommodate one game. First
determine whether a world intentionally defines excluded/unreachable checks.

## Inventory and event semantics

Use Archipelago's normal `World.collect()` behavior. Never manipulate
`CollectionState.prog_items` as a shortcut; some worlds implement custom
collection hooks and cached logic state.

### Shapley-family selection

Classification identifies relevant families, not relevant individual copies.
If at least one copy of a canonical logical family is progression/useful, all
physical copies of that family participate even when AP labels excess copies
as filler. SA2B Emblems established this rule. Do not special-case the name
`Emblem`, and do not include unrelated filler families.

Precollected/start-inventory items exist in every base state and are never
Shapley players.

### Acquisition constraints

Locked/source-bound physical items remain players but cannot activate merely
because their permutation position is reached:

```text
offered unrestricted item -> collect immediately
offered constrained item   -> keep pending
source becomes reachable   -> collect pending item
collect anything           -> sweep automatic events
repeat to a fixed point
```

This logic belongs in `LogicWorld` or a thin game adapter. `ShapleyAnalyzer`
must call `offer_item()` and remain game-agnostic.

### Automatic events

Automatic event/state tokens are not physical players. They are collected by
the event sweep when their source becomes reachable, allowing downstream value
to flow back to the physical item that opened the event chain.

Do not infer event status only from `item.code is None` or progression
classification. External APWorlds may assign ordinary-looking IDs to event
tokens. Prefer authoritative world metadata; use a narrow adapter hook only
when AP exposes no generic marker.

Keep source-bound physical items distinct from automatic events:

- source-bound physical item: offered, possibly pending, then activated;
- automatic event token: never offered, automatically swept.

### Goal release

`release_on_goal=True` is the default. If an item makes the completion
condition true, remaining scored checks are credited to that pivotal item.
Do not change this while fixing unrelated reachability behavior.

## World construction

Archipelago remains the source of truth for legal logic. Build enough of its
lifecycle to finalize regions, rules, topology, events, structural placements,
and precollected inventory without performing ordinary random item fill.

Current lifecycle includes:

```text
generate_early
create_regions
create_items
set_rules
connect_entrances
generate_basic
pre_fill
custom structural fill hooks
```

Some games place locked structural items in `fill_hook()` rather than
`pre_fill()`; Pokémon badges with badgesanity disabled are the demonstrated
case. Invoke custom structural hooks and synchronize their placed items, but
do not run ordinary unrestricted fill because normal placement luck is outside
the metric.

Respect YAML start inventory, pool depletion, game-created precollected
inventory, excluded-location rules, automatic events, and locked placements.

## Game loading and support

Built-in worlds are discovered from literal game declarations in the pinned
local Archipelago source and loaded through a scoped `worlds` namespace. Do
not import the global AP world registry: unrelated worlds may require optional
dependencies that are not installed.

External games require the caller's exact local `.apworld` path. Never
download, upgrade, or silently substitute an APWorld version. Built-in and
external worlds use the same generic construction/analysis pipeline.

Locally smoke-tested games:

- Ocarina of Time
- Pokemon Red and Blue
- Sonic Adventure 2 Battle
- Paint
- Subnautica
- Starcraft 2
- Celeste (Open World)
- Dark Souls Remastered 0.2.6 via an explicit APWorld
- Majora's Mask Recompiled 0.9.5.post2 via an explicit APWorld

Do not claim universal game support solely because discovery is generic.

### Demonstrated adapters

Use base `LogicWorld` first. Add an override only after a correctness failure
shows that the generic Archipelago interface is insufficient.

- `OOTLogicWorld`: dungeon rewards remain physical Shapley items but use their
  generated boss/Link's Pocket source as an acquisition constraint.
- `DSRLogicWorld`: uses DSR APWorld item-category metadata to identify EVENT
  items that have normal IDs/classification but must be automatically swept.

SA2B's extra Emblem diagnostics are reporting only, not alternate logic. Keep
game-specific semantics out of `ShapleyAnalyzer`.

## Architecture

```text
ap_shapley/
  analyzer.py      generic fixed-world physical Shapley estimator
  logic_world.py   inventory, reachability, events, acquisition closure
  oot_world.py     demonstrated OoT acquisition override
  dsr_world.py     demonstrated DSR event-classification override
  builders.py      scoped loading, lifecycle, multiprocessing, public APIs
  yaml_config.py   AP-style YAML parsing and option rolling
  result.py        aggregation, persistence, summaries, plots, audits
  combined.py      cross-analysis tables and plots
```

Primary APIs are `analyze_yaml()`, `analyze_yamls()`,
`AnalysisResult.save()`, and `AnalysisResult.load()`. The older `analyze_oot()`
remains available, but YAML-driven analysis is preferred. Do not reintroduce
obsolete APIs or separate public world and sampling seeds.

## Results and plots

`analysis.plot()` is a horizontal box plot of actual per-world values:

- ranking and default metric: `shapley_per_copy`;
- box: Q1 to Q3;
- line: median;
- whiskers: 1.5 times IQR;
- triangle: arithmetic mean;
- dashed line at 1: one-check break-even;
- outlier markers: hidden by default, available with `show_outliers=True`.

Hiding outlier markers changes only display scaling. Outliers still affect
means, rankings, saved values, and all statistics.

Audits include `audit_item()`, `plot_audit()`, `audit_location()`,
`display_location_audit()`, and `show_location_logic()`. Audit allocations
should reconcile with the associated Shapley values.

## Windows and performance

Parallelism is process-level across independent worlds. The hot path is
arbitrary Python reachability logic, so threads and GPUs are not appropriate.
`n_jobs` is capped by `world_samples`.

Windows uses multiprocessing `spawn`. Any runner that starts workers must use:

```python
if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
```

Never start a process pool at import time. Preserve Linux compatibility. Use
small smoke settings before scaling:

```text
world_samples=2
shapley_pairs=4
n_jobs=2
```

## Repository conventions

Tracked public material includes source, tests, documentation, sanitized YAMLs
under `examples/`, documentation images under `docs/assets/`, and the minimal
public `explore_analysis.ipynb`.

Private/local material is ignored:

- `batch_yamls/` and `local_yamls/`;
- `private_notebooks/`;
- `.venv/` and `Archipelago/`;
- `external_apworlds/`;
- `output/` and `results/`.

Never move player names, private YAML settings, local APWorld packages, saved
pickle analyses, or generated friend-group plots into tracked files.

## Validation

Run the lightweight suite after changes:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -q
```

For game integration changes:

1. import and construct one world;
2. validate all-items reachability, completion, events, and pending closure;
3. run one world with one or two Shapley pairs;
4. run two worlds with four pairs and Windows multiprocessing;
5. inspect obvious item/location audits;
6. scale only after invariants and qualitative logic make sense.

Preserve tests for physical duplicates, progressive interleavings, count
thresholds, mixed-classification families, acquisition constraints, automatic
event cascades, precollected items, structural fill hooks, and conservation.

## Design rule

Fix failure classes rather than patching game names. If Paint exposes missing
collection hooks, fix generic collection semantics. If a game creates
precollected items late, fix base-state construction. If an external APWorld
uses authoritative event metadata, expose a narrow adapter hook. A game-specific
override is justified only by a documented semantic incompatibility.

The quantity this project seeks is:

> Expected logical usefulness of a physical item under the distribution of
> legal worlds generated by a player's Archipelago YAML.

Preserve that meaning unless the user explicitly requests a methodological
change.
