# AGENTS.md

## Project: Archipelago Shapley Item Valuation

This repository analyzes **intrinsic item usefulness in Archipelago randomizers** using Shapley values over logical reachability.

The immediate target game is **The Legend of Zelda: Ocarina of Time (OoT)** on **Archipelago 0.6.7**, but the architecture is intentionally designed so the Shapley engine remains game-agnostic and other games can be added with thin adapters where necessary.

The main user is comfortable with Python, statistics, and technical reasoning. Prefer direct, rigorous explanations and preserve the mathematical semantics below when modifying the code.

---

## 1. Core Goal

We want to answer:

> How intrinsically useful is each progression/useful item, independent of ordinary random item placement?

For a fixed generated logic world, define:

- `S` = a hypothetical set/multiset of item copies already obtained.
- `v(S)` = number of **scored checks** reachable with that inventory.

The Shapley value of an item copy is its average marginal contribution over random orderings of all Shapley item copies:

> An item's score is the average number of checks for which it is pivotal, over random orders in which progression/useful items could be obtained.

This is meant to measure **logical usefulness**, not placement luck.

Examples:

- `A OR B` → `0.5` each.
- `A AND B` → `0.5` each.
- `A AND (B OR C)` → `A = 2/3`, `B = C = 1/6`.
- If Hookshot opens a region with 9 free checks plus 1 Bow-gated check, Hookshot gets `9.5`, Bow gets `0.5`.

Repeated/counted copies are treated as symmetric physical Shapley players. Results are reported per copy and, where useful, as family totals.

Physical-copy Shapley is the canonical methodology. Every physical item copy
is a player, and sampling is uniform over interleavings of all copies. A
family-block/Owen allocation was investigated but rejected as the primary
metric because contiguous family blocks eliminate valid intermediate states
between progressive/count stages and other item families.

---

## 2. Conservation Property

Every item-dependent scored check should contribute exactly **1 total Shapley point** across all item copies.

Therefore:

```text
sum(all Shapley values)
=
number of scored checks that are not reachable with zero Shapley items
```

Free checks contribute no Shapley points.

This is an important correctness invariant and should remain near machine precision.

Useful diagnostics include:

- `scored_checks`
- `free_checks`
- `item_dependent_checks`
- `reachable_with_all_items`
- `still_unreachable`
- `allocated_shapley_points`
- `expected_shapley_points`
- `conservation_difference`

---

## 3. Monte Carlo Shapley Estimation

Exact Shapley computation is too expensive, so we use Monte Carlo permutations.

The estimator uses **antithetic pairs**:

1. sample one random item-copy permutation;
2. also evaluate its reverse;
3. average the pair.

Public parameter:

```python
shapley_pairs=64
```

means **64 antithetic pairs = 128 permutations per world**.

The implementation should continue using sufficient statistics rather than retaining every pair sample unless a future feature explicitly needs raw samples.

---

## 4. World Sampling

A single Archipelago configuration can generate many different legal worlds because of:

- entrance shuffle;
- boss shuffle;
- randomized restricted placements;
- weighted/random YAML options;
- other seed-dependent generation.

We therefore estimate:

```text
Phi_i = E_world[ phi_i(world) ]
```

where `phi_i(world)` is the Shapley value for item `i` in one generated world.

Public API uses:

```python
world_samples=N
```

Each sampled world must have independent deterministic seeds derived from one master `seed`.

Important:

- item averages are **conditional on the item existing**;
- absence of an item in a generated world is NOT treated as a zero Shapley value;
- world-generation randomness and Shapley permutation randomness are conceptually distinct.

---

## 5. Variance / Uncertainty Semantics

The result table currently tracks diagnostics such as:

- `observed_world_sd`
- `estimated_world_sd`
- `sampling_se_mean`
- `total_se`
- `ci95`

Interpretation:

### `observed_world_sd`

Raw standard deviation of the per-world Shapley estimates.

This includes both:

- genuine world-to-world variation;
- residual finite-Shapley-sampling noise inside each world.

### `estimated_world_sd`

Approximate true world-to-world SD after subtracting the average inner sampling variance:

```text
sqrt(max(observed_world_variance - mean(inner_se^2), 0))
```

### `sampling_se_mean`

Residual Shapley Monte Carlo uncertainty in the aggregate mean.

### `total_se`

Standard error of the estimated cross-world mean.

For multiple worlds, the observed per-world spread is divided by `sqrt(worlds_present)` when estimating uncertainty in the mean.

The important distinction is:

- **world SD** answers: “How much can the item's value vary in an individual generated world?”
- **SE / CI of mean** answers: “How precisely have we estimated the expected value over the YAML's world distribution?”

Do not confuse the two.

---

## 6. Plot Semantics

`analysis.plot()` currently uses a **horizontal box-and-whisker plot** built from the actual per-world Shapley values.

For each item:

- box = Q1 to Q3;
- center line = median;
- whiskers = standard 1.5×IQR rule;
- points outside whiskers = outlier worlds, hidden by default for readability;
- triangle marker = arithmetic mean across sampled worlds;
- items are ranked by mean Shapley value;
- vertical dashed line at `Shapley = 1` is the break-even line.

This plot is intended to communicate both:

- the expected item value;
- how variable the item is across generated worlds.

Use `analysis.plot(show_outliers=True)` to display the complete outlier range.
Hiding them changes only the visualization, never the underlying values,
means, ranking, or saved analysis.

Some highly right-skewed entrance-randomized items can have means far above their medians because rare worlds give them enormous value. This is expected and informative.

Possible future improvement:
- reduce how strongly extreme outliers stretch the x-axis while still preserving mean/median/outlier information.

---

## 7. Goal Release Semantics

Archipelago releases remaining items/checks when a game is complete.

The analyzer models this with:

```python
release_on_goal=True
```

by default.

Conceptually:

```text
v(S) =
    total scored checks, if completion condition is satisfied
    otherwise normally reachable scored checks
```

If the latest item causes the completion condition to become true, all still-unreached scored checks become newly available and are credited to that pivotal item in that permutation.

This is deliberate.

Use:

```python
release_on_goal=False
```

only when comparing against physical-reachability-only semantics.

Audit output currently does not explicitly label whether a location's credit came from normal reachability versus goal release. That would be a useful future enhancement.

---

## 8. Acquisition Constraints

A major methodological issue was discovered with structurally constrained items.

Originally every Shapley item was injected directly into inventory when encountered in the permutation. That is incorrect for items that cannot legally be obtained until a particular source is reachable.

Example: OoT dungeon rewards.

If Forest Medallion is assigned to Twinrova, the player should not be allowed to “have Forest Medallion” before Twinrova is logically reachable.

The current model therefore uses **offered items + acquisition closure**.

Conceptually:

```text
v*(S) = v(C(S))
```

where `C(S)` is the inventory after repeatedly activating offered constrained items whose acquisition sources have become reachable.

Behavior:

- unrestricted item offered → collect immediately;
- constrained item offered → leave pending;
- whenever its source becomes reachable → collect it;
- after collecting anything, sweep automatic logic events;
- continue to fixed point.

This preserves Shapley semantics correctly.

Example:

```text
Forest Medallion already pending at Twinrova
Mirror Shield becomes newly offered
→ Twinrova becomes reachable
→ pending Forest Medallion activates
→ downstream checks open
```

In that permutation, Mirror Shield correctly receives the marginal cascade because it was the pivotal new player.

If Twinrova was already reachable when Forest Medallion was offered, Forest Medallion receives the downstream marginal credit instead.

---

## 9. Generic Acquisition Layer

Acquisition logic lives in `LogicWorld`, not in the Shapley analyzer.

Generic default behavior:

```python
def acquisition_source_for_item(self, raw_item, source_location):
    if source_location is not None and getattr(source_location, "locked", False):
        return source_location
    return None
```

Key methods include:

```python
acquisition_source(item)
is_acquisition_constrained(item)
can_activate_item(state, item)
close_pending_items(state, pending_items, remaining_events)
offer_item(state, item, pending_items, remaining_events)
```

`ShapleyAnalyzer` should call `offer_item(...)` rather than directly collecting an item.

This design is important: keep item-acquisition semantics in adapters / logic-world wrappers, not in the generic analyzer.

---

## 10. OoT-Specific Acquisition Override

OoT dungeon rewards need explicit treatment because `code=None` does not mean “automatic event.”

OoT adapter override:

```python
def acquisition_source_for_item(self, raw_item, source_location):
    if (
        source_location is not None
        and getattr(raw_item, "type", None) == "DungeonReward"
    ):
        return source_location

    return super().acquisition_source_for_item(
        raw_item,
        source_location,
    )
```

This keeps dungeon rewards as Shapley items while preserving their generated boss-reward / Link's Pocket acquisition source.

Keep this OoT-specific knowledge in `oot_world.py`.

Do NOT put OoT-specific conditionals in `ShapleyAnalyzer`.

---

## 11. Constrained Item Diagnostics

Summaries currently report:

- `constrained_item_copies_min`
- `constrained_item_copies_max`
- `constrained_item_families_min`
- `constrained_item_families_max`
- `pending_constrained_with_all_offered_max`

Healthy behavior:

```text
pending_constrained_with_all_offered_max = 0
```

That means after every Shapley item has been offered, all constrained items eventually become legally obtainable.

If this is nonzero, investigate:

- misclassified constrained items;
- source mapping;
- circular acquisition dependencies;
- unreachable generated world logic.

In a recent OoT run with mostly default-style settings, there were unexpectedly many constrained items:

```text
81 constrained copies
45 constrained families
```

This is not necessarily a bug: Archipelago may pre-place/lock many items depending on settings.

Do not add ad-hoc OoT-specific “allowed source pool” logic unless evidence requires it. The current philosophy is:

> Let Archipelago generate legal worlds, respect the exact constrained placements AP generates, and average over many `world_samples`.

That is more generic and portable.

---

## 12. Automatic Logic Events

Earlier code incorrectly treated all progression items with `code=None` as automatic events.

This caused OoT dungeon rewards such as medallions to be auto-collected when their boss location became reachable.

That was wrong and has been fixed.

Dungeon rewards must remain Shapley items.

Automatic logic events should only represent genuine logical/event state transitions that are not randomized player items.

External APWorlds may represent automatic event/state items with normal item
IDs or progression classification. Shapley-player detection must distinguish
physical inventory from event tokens using authoritative world metadata or a
narrow adapter hook when necessary. Event tokens are never physical Shapley
players; their downstream value propagates through the automatic event sweep.

---

## 13. Architecture

Current intended repository structure:

```text
ap_shapley_project/
├── install_archipelago_colab.py
├── README.md
├── ap_shapley/
│   ├── __init__.py
│   ├── logic_world.py
│   ├── oot_world.py
│   ├── analyzer.py
│   ├── result.py
│   ├── builders.py
│   └── yaml_config.py
└── ...
```

Responsibilities:

### `logic_world.py`

Generic wrapper around an Archipelago world.

Owns:

- scored locations;
- Shapley item identification;
- automatic logic event sweeping;
- acquisition constraints;
- completion-condition handling;
- generic state helpers.

### `oot_world.py`

Only OoT-specific quirks.

Examples:

- dungeon reward acquisition semantics;
- any future OoT-specific canonicalization or event behavior.

### `analyzer.py`

Generic one-world Shapley Monte Carlo engine.

Should know nothing about OoT.

### `result.py`

Results, aggregation, plotting, audits, summaries.

### `builders.py`

World construction, multiprocessing, public `analyze_oot()` / `analyze_yaml()` front ends.

### `yaml_config.py`

Archipelago-style player YAML parsing and per-world weighted/random option rolling.

---

## 14. Public API

Preferred public API:

```python
from ap_shapley import analyze_yaml

analysis = analyze_yaml(
    "/path/to/player.yaml",
    world_samples=100,
    shapley_pairs=64,
    seed=2026,
    n_jobs=20,
    progress=True,
    release_on_goal=True,
)
```

The older manual OoT API still exists:

```python
from ap_shapley import analyze_oot

analysis = analyze_oot(
    options={...},
    world_samples=20,
    shapley_pairs=64,
    seed=2026,
    n_jobs=-1,
    progress=True,
    release_on_goal=True,
)
```

The preferred long-term workflow is YAML-driven.

Do NOT reintroduce old/dead public APIs such as:

- `analyze_default_oot`
- `n_pairs`
- separate public `world_seed`
- separate public `sampling_seed`

One master `seed` is intentional.

---

## 15. YAML Semantics

`analyze_yaml()` accepts one Archipelago player YAML.

Validated YAML games (tiny local analysis):

```text
Ocarina of Time
Pokemon Red and Blue
Sonic Adventure 2 Battle
Paint
Subnautica
Starcraft 2
Celeste (Open World)
Dark Souls Remastered (explicit local 0.2.6 APWorld)
Majora's Mask Recompiled (explicit local 0.9.5.post2 APWorld)
```

Built-in worlds are discovered from literal game declarations in the pinned
local Archipelago source and then loaded through the scoped `worlds`
namespace. The registry contains only demonstrated semantic overrides, not a
hand-maintained list of built-in modules/classes. External games require an
explicit exact `.apworld` path; versions are never downloaded or selected
silently. Legacy AP 0.6.7 APWorlds without manifests can load only when their
exact file is supplied, and their registered game name is verified afterward.

The friend-group external worlds were smoke-tested with DSR 0.2.6 and Majora's
Mask Recompiled 0.9.5.post2. Keep their exact local APWorld files explicit in
batch calls; do not silently substitute another version.

Generic lifecycle requirements now include YAML `start_inventory`,
`start_inventory_from_pool` depletion, standard excluded-location rules, and
game-created precollected inventory. `CollectionState` supplies precollected
items to every analysis state and invokes the world's normal `collect()` hook;
precollected items are not members of `MultiWorld.get_items()` and therefore
are not Shapley players. Paint validated that custom collect/cache semantics
must be honored; SC2 validated generated precollected inventory.

`Pokemon Red and Blue` uses the generic `LogicWorld`; it currently requires no
game-specific adapter behavior.

`Sonic Adventure 2 Battle` also uses generic `LogicWorld`. Its randomized
stage-to-gate assignment and calculated Emblem thresholds make outer world
sampling meaningful even though AP reports `topology_present = False`.

SA2B demonstrated that per-copy AP `ItemClassification` cannot by itself
define the physical Shapley pool. If at least one copy makes a canonical item
family progression/useful, every physical copy sharing that logical family
identity participates, including excess copies AP classifies as filler. This
preserves counted logic such as `state.has("Emblem", player, k)` without
including unrelated filler families.

For each `world_sample`, the analyzer independently:

1. reads the same player YAML;
2. rerolls weighted/list/`random` options;
3. applies linked options / triggers;
4. generates a fresh legal AP world;
5. runs the Shapley estimator;
6. aggregates results across worlds.

Deterministic YAML settings remain constant across samples.

Weighted/random settings are rerolled independently per sampled world.

The top-level `game:` currently needs to be fixed, not weighted/random.

The YAML layer intentionally mirrors Archipelago 0.6.7 option-rolling behavior without importing `Generate.py`, because importing global AP generation machinery risks loading every world/plugin and optional dependency.

---

## 16. Example Current OoT YAML Behavior

A real test YAML currently being used enables aggressive entrance and item shuffling, including:

```yaml
shuffle_interior_entrances: all
shuffle_grotto_entrances: true
shuffle_dungeon_entrances: all
shuffle_overworld_entrances: true
owl_drops: true
warp_songs: true
spawn_positions: both
shuffle_bosses: full

tokensanity: all
shuffle_song_items: any
shuffle_child_trade: shuffle
shuffle_smallkeys: keysanity
shuffle_hideoutkeys: keysanity
shuffle_bosskeys: keysanity

shopsanity: fixed_number
shop_slots: 4
shuffle_scrubs: regular
shuffle_cows: true
shuffle_frog_song_rupees: true

bridge: medallions
bridge_medallions: 4
shuffle_ganon_bosskey: medallions
ganon_bosskey_medallions: 6
```

With this setup, many item distributions become strongly right-skewed across worlds because entrance shuffle can occasionally make an item gate enormous portions of the graph.

This is expected.

---

## 17. Entrance Shuffle Interpretation

Do not assume a location's vanilla geography explains its audit relationship.

Example:

```text
Kokiri Sword → Spirit Temple checks
```

does not necessarily mean those Spirit checks intrinsically require Kokiri Sword.

With dungeon entrance shuffle, Spirit Temple may happen to be behind an entrance whose access rule depends on Kokiri Sword.

Thus an item can receive credit for many locations in a shuffled destination region because it unlocked the entrance leading there.

The aggregate audit currently labels destination locations, not the entrance path that caused them to become reachable.

A very useful future diagnostic would be per-world causal inspection such as:

```python
analysis.audit_item_worlds("Kokiri Sword")
```

showing which worlds contributed the large value and which region/entrance became newly available.

---

## 18. Warp Song Interpretation

With shuffled warp-song entrances, warp songs are expected to show similar qualitative behavior:

- many low/moderate-value worlds;
- occasional huge-value worlds;
- strongly right-skewed distributions.

They are not necessarily perfectly exchangeable because Archipelago can impose special placement/priority constraints and because each generated world has finite-sample randomness.

Do not infer a bug merely because one warp song has a much larger sample mean than another in a small number of sampled worlds.

---

## 19. Audit API

Useful calls include:

```python
analysis.audit_item("Zeldas Lullaby", top_n=30)
analysis.plot_audit("Zeldas Lullaby", top_n=30)
analysis.audit_location("...")
analysis.show_location_logic("...", world_index=0)
```

The old method name `plot_item_audit` should not be used.

Audit totals should sum back to the item's per-copy Shapley value.

Potential future improvement:
- distinguish audit credit mechanisms:
  - normal reachability;
  - acquisition cascade;
  - goal release.

That would make strange-looking audit rows much easier to interpret.

---

## 20. Performance

The hot path is repeated evaluation of arbitrary Python Archipelago reachability rules:

```python
location.can_reach(state)
```

This is not a NumPy/GPU workload.

Primary optimization is **process-level parallelism across independent world samples**.

Current behavior:

```python
n_jobs=-1
```

uses available logical CPUs, capped by `world_samples`.

Important:

- world-level parallelism only;
- `world_samples=1` does not currently parallelize `shapley_pairs`;
- threads are inappropriate because of Python's GIL;
- GPU is not useful here.

Existing optimizations include:

1. zero-item base state/free checks computed once per world;
2. dense NumPy audit accumulation;
3. sufficient statistics instead of raw pair lists;
4. vectorized result aggregation;
5. sanity values computed during analysis;
6. multiprocessing at world level.

---

## 21. Progress Reporting

The progress bar reports actual antithetic Shapley-pair work.

Example:

```text
Worlds 6/20 | Shapley pairs: 41% 524/1280
```

Denominator:

```text
world_samples * shapley_pairs
```

There are no fake “finalization” increments.

Workers send small progress updates to the parent process, which owns the single tqdm bar.

---

## 22. Warning Suppression

Archipelago 0.6.7 produces large amounts of Python 3.13 deprecation-warning noise.

The project contains a scoped warning-suppression context manager around analysis/workers.

Do not globally silence real exceptions.

The goal is:

- hide known irrelevant warning spam;
- keep actual errors visible.

---

## 23. Scoped Archipelago World Loader

Do NOT import Archipelago's global world registry unnecessarily.

Earlier multiprocessing attempts imported all AP worlds and failed because unrelated games required optional packages such as:

- `pyevermizer`
- `zilliandomizer`

The current approach installs a lightweight `worlds` namespace and imports only:

```text
worlds.AutoWorld
worlds.oot
```

for OoT.

World setup mirrors AP's generation steps directly:

```text
generate_early
create_regions
create_items
set_rules
connect_entrances
generate_basic
pre_fill
special fill hooks (without ordinary unrestricted fill)
```

The special-hook phase is required because some games finalize structural
locked placements in `fill_hook()` rather than `pre_fill()`. For example,
Pokémon Red/Blue with `badgesanity: false` and `door_shuffle: off` locks the
eight badges to Gym Leader prize locations there. The project invokes custom
instance/stage fill hooks and synchronizes their placed items, but deliberately
does not continue into AP's ordinary restrictive/filler placement. Games with
no custom fill hook are skipped so their existing generation and RNG stream
remain unchanged.

This scoped loading strategy is intentional and important for portability.

Future game support should load only the requested game's module and dependencies.

---

## 24. Local Windows Development

The project is now moving from hosted Colab to local Windows compute.

Current machine:

```text
Intel Core i7-14700F
20 physical cores
28 logical processors
Python 3.13.16
```

The intended local stack is:

```text
Windows
VS Code
Python 3.13 virtual environment
Archipelago 0.6.7 checkout
ap_shapley_project
```

Docker/WSL was considered but abandoned because hardware virtualization is disabled and the user does not want to alter BIOS/firmware settings.

Prefer native Windows compatibility.

For active desktop use, likely sensible initial worker count:

```python
n_jobs=16
```

or:

```python
n_jobs=20
```

rather than consuming all 28 logical CPUs.

---

## 25. Windows Multiprocessing Caveat

Linux/Colab uses `fork` where available.

Windows uses `spawn`.

Any standalone local runner that starts multiprocessing should be guarded:

```python
def main():
    ...

if __name__ == "__main__":
    main()
```

Avoid code that starts a process pool at import time.

The current project has not yet been fully validated under native Windows multiprocessing, so the first local run should be very small, e.g.:

```python
world_samples=2
shapley_pairs=4
n_jobs=2
```

Fix Windows-specific multiprocessing issues before scaling.

---

## 26. Current Validation / Known Good Signals

Healthy summaries should generally have:

```text
pending_constrained_with_all_offered_max = 0
max_abs_conservation_difference ≈ 0
still_unreachable = 0
```

For aggressive OoT YAMLs with extra randomized checks, `scored_checks` should differ substantially from vanilla/default-style counts.

Earlier reference counts:

```text
default-ish OoT:
scored_checks = 263

full Tokensanity:
scored_checks = 363
```

A YAML with Tokensanity plus shopsanity, shuffled scrubs/cows/frog rewards, etc. should have more than 363 scored checks.

That is a useful smoke test that YAML options are actually affecting generation.

---

## 27. Known Open Questions

### A. Constrained-item classification

The generic rule “locked location ⇒ acquisition-constrained item” may be broader than strictly necessary.

This is currently accepted as a generic best-effort rule.

Do not replace it with a pile of OoT-specific special cases without strong evidence.

### B. Audit causality

Aggregate audits can be difficult to interpret under entrance shuffle.

Useful future additions:

- per-world item audits;
- identify which worlds generate extreme Shapley values;
- record newly unlocked regions/entrances where feasible;
- distinguish normal/cascade/release credit.

### C. Boxplot readability

Rare extreme worlds can stretch the x-axis badly.

Potential improvements:

- clipped display range with explicit outlier annotation;
- inset;
- percentile-based view;
- optional log-like view if appropriate.

Do not silently discard outliers because they are real contributors to the expected Shapley value.

### D. Inner-pair parallelism

Currently parallelism is across worlds only.

If `world_samples` is small but `shapley_pairs` is very large, future work could parallelize inner pair batches, but correctness and overhead should be evaluated carefully.

### E. Known methodological limitation: repeated copies

For the isolated toy gate

```text
A AND at least k copies of family F
```

where `A` has one physical copy and `F` has `n` physical copies, uniform
physical-copy permutations give:

\[
\phi_A = \frac{n+1-k}{n+1}, \qquad
\phi_F = \frac{k}{n+1}, \qquad
\phi_{F_i} = \frac{k}{n(n+1)}.
\]

Here `phi_F` is total family credit and `phi_{F_i}` is one symmetric copy.
Conservation is exact: `phi_A + phi_F = 1`. Briefly, `A` has `n+1` possible
positions relative to the copies of `F`; it is pivotal in the `n+1-k`
positions having at least `k` earlier copies. Otherwise the kth required copy
of `F` is pivotal.

Compact examples:

- **Ocarina / Zelda's Lullaby:** `n=2`, `k=1` gives Zelda's Lullaby `2/3`,
  the Ocarina family `1/3`, and each Ocarina `1/6`. When the copies are
  redundant substitutes, this is the known replication/multiplicity
  limitation.
- **Progressive Strength:** with `n=3`, a gate requiring singleton `A` plus
  Strength level 1, 2, or 3 gives the Strength family `1/4`, `1/2`, or `3/4`.
  This is desirable: higher progression thresholds make Strength increasingly
  responsible for the gate.
- **Sonic Emblems:** with `n=100`, gates requiring `A` plus 10, 50, or 90
  Emblems give the Emblem family `10/101` (about `9.9%`), `50/101` (about
  `49.5%`), or `90/101` (about `89.1%`). For large `n`, `phi_F` is
  approximately `k/n`: roughly the fraction of available copies required.
  A check requiring only 30 Emblems and no other family gives the Emblem
  family 100% of that check's Shapley point.

This closed form applies to the isolated structure `A AND (#F >= k)`. Real
Archipelago logic may include OR paths, multiple prerequisites, entrance
shuffle, acquisition cascades, events, and goal release, but the same physical
permutation logic operates underneath.

Physical-copy Shapley remains canonical because it preserves legitimate
interleavings and count/progression stages. Family-block/Owen sampling removes
the replication effect but forces all copies in a family to be consecutive,
distorting logic such as `stage2 OR (stage1 AND X)`. We accept the redundant
substitute limitation rather than introduce a generic fix that breaks
progressive/count-based logic.

A better generic alternative would need to preserve ordered logical stages
and their interleavings while distinguishing redundant copies from meaningful
count/progression stages. Automatically identifying that distinction across
arbitrary Archipelago games remains unresolved. Do not add game-specific
special cases as a substitute for a generic solution.

---

## 28. Future Generalization to Other Games

Desired long-term interface:

```python
analysis = analyze_yaml(
    "/path/to/player.yaml",
    world_samples=100,
    shapley_pairs=64,
    seed=2026,
)
```

for arbitrary supported Archipelago games.

Principle:

```text
generic YAML dispatch
        ↓
game-scoped AP world loader
        ↓
generic LogicWorld
        ↓
optional thin game adapter
        ↓
generic ShapleyAnalyzer
```

Do not create separate analyzers per game.

Another game should only need an adapter if its event/item/acquisition semantics require one.

---

## 29. Engineering Preferences

When changing this project:

- preserve game-agnostic architecture;
- keep OoT-specific behavior in `oot_world.py`;
- prefer AP's own generated logic rather than custom reimplementation;
- let Archipelago own entrance shuffle and rule construction;
- preserve deterministic reproducibility from one master seed;
- do not reintroduce obsolete APIs;
- do not silently redefine the metric;
- keep conservation checks;
- prioritize correctness before micro-optimizations;
- avoid unnecessary dependency on unrelated AP world modules;
- use process parallelism rather than threads for CPU-bound reachability work;
- keep user-facing APIs compact.

If a proposed optimization changes semantics, call that out explicitly before implementing it.

---

## 30. Conceptual North Star

The desired quantity is:

> **Expected logical usefulness of an item under the distribution of legal worlds generated by a given Archipelago YAML.**

That includes legitimate variation from:

- entrance topology;
- restricted legal placements;
- weighted YAML settings;
- boss/warp/spawn randomization;
- completion logic.

It should not include arbitrary dependence on ordinary unrestricted item placement.

Archipelago itself should remain the source of truth for what constitutes a legal generated world.

The Shapley engine should measure marginal logical contribution inside those worlds, then average across them.
