from __future__ import annotations

from argparse import Namespace
import ast
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import importlib
import importlib.abc
import multiprocessing as mp
import os
from pathlib import Path
import random
import re
import sys
import time
import traceback
import types
import warnings
import zipimport
from zipfile import ZipFile

from tqdm.auto import tqdm

from .logic_world import LogicWorld
from .oot_world import OOTLogicWorld
from .dsr_world import DSRLogicWorld
from .analyzer import ShapleyAnalyzer
from .result import AnalysisResult
from .yaml_config import (
    fixed_game_name,
    read_player_yaml,
    roll_yaml_options,
)


# Generation stages used by Archipelago's own test.general.setup_multiworld().
# Keeping them here lets us construct a world without importing test.general,
# because importing test.general imports the global `worlds` registry and AP
# 0.6.7 eagerly loads every bundled game.
def _run_special_fill_hooks(multiworld) -> None:
    """Run game-defined main-fill hooks without doing ordinary item fill.

    Some worlds finalize structural placements in ``fill_hook()`` rather than
    ``pre_fill()``.  The analyzer needs those placements (especially locked
    ones), but deliberately must not run AP's remaining unrestricted fill,
    because ordinary placement luck is outside the intrinsic Shapley game.

    This mirrors the categorized pools and unfilled-location inputs used by
    ``distribute_items_restrictive`` at the hook boundary, while deliberately
    omitting its ordinary early-item and main-fill placements. Games that only
    inherit AutoWorld's no-op hook and have no stage hook are skipped,
    preserving their prior RNG stream.
    """
    from worlds.AutoWorld import World, call_all

    world_types = {world.__class__ for world in multiworld.worlds.values()}
    has_instance_hook = any(
        world_type.fill_hook is not World.fill_hook
        for world_type in world_types
    )
    has_stage_hook = any(
        getattr(world_type, "stage_fill_hook", None) is not None
        for world_type in world_types
    )
    if not (has_instance_hook or has_stage_hook):
        return

    original_pool = list(multiworld.itempool)
    original_random_state = multiworld.random.getstate()
    original_filled_ids = {
        id(location.item)
        for location in multiworld.get_filled_locations()
        if location.item is not None
    }

    fill_locations = sorted(multiworld.get_unfilled_locations())
    multiworld.random.shuffle(fill_locations)

    itempool = sorted(multiworld.itempool)
    multiworld.random.shuffle(itempool)
    progitempool = [item for item in itempool if item.advancement]
    usefulitempool = [
        item for item in itempool
        if not item.advancement and item.useful
    ]
    filleritempool = [
        item for item in itempool
        if not item.advancement and not item.useful
    ]

    call_all(
        multiworld,
        "fill_hook",
        progitempool,
        usefulitempool,
        filleritempool,
        fill_locations,
    )

    placed_by_hooks = [
        location.item
        for location in multiworld.get_filled_locations()
        if (
            location.item is not None
            and id(location.item) not in original_filled_ids
        )
    ]
    if not placed_by_hooks:
        # A hook such as SA2B's merely prioritizes the later ordinary fill.
        # It has no meaning for placement-independent Shapley analysis.
        multiworld.itempool[:] = original_pool
        multiworld.random.setstate(original_random_state)
        return

    # The real fill operates on categorized copies of MultiWorld.itempool.
    # Synchronize the master pool so hook-placed objects are represented once:
    # at their generated source location, not both there and in itempool.
    multiworld.itempool[:] = (
        progitempool + usefulitempool + filleritempool
    )

# Only demonstrated semantic overrides belong here. Built-in module/class
# metadata is discovered from the installed Archipelago source instead of
# growing a second hand-maintained game registry.
_GAME_OVERRIDES = {
    "Ocarina of Time": {
        "logic_world_class": OOTLogicWorld,
    },
    "Sonic Adventure 2 Battle": {
        "world_diagnostics": lambda logic_world: {
            "emblem_counts": logic_world.item_family_classification_counts(
                "Emblem"
            ),
            "maximum_required_emblems": max(
                [logic_world.world.emblems_for_cannons_core]
                + list(logic_world.world.region_emblem_map.values())
            ),
            "cannons_core_emblems": (
                logic_world.world.emblems_for_cannons_core
            ),
            "gate_costs": dict(logic_world.world.gate_costs),
            "level_gate_costs": dict(logic_world.world.region_emblem_map),
        },
    },
    "Dark Souls Remastered": {
        "logic_world_class": DSRLogicWorld,
    },
}


_BUILTIN_GAME_MODULES = None


def _literal_game_names(init_path: Path) -> set[str]:
    """Read literal ``game = ...`` declarations without importing a world."""
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()

    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if (
            any(isinstance(target, ast.Name) and target.id == "game" for target in targets)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            names.add(value.value)
    return names


def _discover_builtin_game_modules() -> dict[str, str]:
    global _BUILTIN_GAME_MODULES
    if _BUILTIN_GAME_MODULES is not None:
        return dict(_BUILTIN_GAME_MODULES)

    import BaseClasses

    worlds_path = Path(BaseClasses.__file__).resolve().parent / "worlds"
    discovered = {}
    collisions = {}
    for init_path in worlds_path.glob("*/__init__.py"):
        for game_name in _literal_game_names(init_path):
            old = discovered.setdefault(game_name, init_path.parent.name)
            if old != init_path.parent.name:
                collisions.setdefault(game_name, {old}).add(init_path.parent.name)
    if collisions:
        details = ", ".join(
            f"{game}: {sorted(modules)}" for game, modules in collisions.items()
        )
        raise RuntimeError(f"Ambiguous built-in Archipelago game declarations: {details}")
    _BUILTIN_GAME_MODULES = discovered
    return dict(discovered)


def _read_apworld_manifest(path: Path) -> dict:
    with ZipFile(path) as archive:
        candidates = [name for name in archive.namelist() if name.endswith("archipelago.json")]
        if not candidates:
            # AP 0.6.7 still accepts legacy APWorlds without a manifest. They
            # may only be selected when the caller supplies that exact file;
            # registration after import verifies the requested game name.
            return {}
        import json
        return json.loads(archive.read(min(candidates, key=len)))


def _normalize_apworld_paths(apworld_paths=None) -> tuple[str, ...]:
    if apworld_paths is None:
        return ()
    if isinstance(apworld_paths, (str, Path)):
        apworld_paths = [apworld_paths]
    resolved = []
    for raw_path in apworld_paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".apworld":
            raise FileNotFoundError(f"External APWorld not found: {path}")
        resolved.append(str(path))
    return tuple(resolved)


def _external_path_for_game(
    game_name: str,
    apworld_paths=(),
    *,
    allow_legacy: bool = True,
) -> str | None:
    matches = []
    legacy = []
    for raw_path in apworld_paths:
        path = Path(raw_path)
        manifest = _read_apworld_manifest(path)
        if manifest.get("game") == game_name:
            matches.append(str(path))
        elif not manifest:
            legacy.append(str(path))
    if allow_legacy and not matches and len(legacy) == 1:
        matches = legacy
    if len(matches) > 1:
        raise RuntimeError(
            f"Multiple explicitly supplied APWorlds provide {game_name!r}: {matches}. "
            "Supply exactly the version associated with this YAML."
        )
    return matches[0] if matches else None


def _adapter_for_game(game_name: str, apworld_paths=()) -> dict:
    builtin_module = _discover_builtin_game_modules().get(game_name)
    external_path = _external_path_for_game(
        game_name,
        apworld_paths,
        # A manifest-less APWorld cannot identify its game before import.
        # Never let it shadow a known built-in merely because it is the only
        # legacy package in the explicitly supplied path list.
        allow_legacy=builtin_module is None,
    )
    if external_path:
        source = {"source": "apworld", "apworld_path": external_path}
    else:
        if builtin_module is None:
            raise NotImplementedError(
                f"No built-in Archipelago 0.6.7 world was discovered for {game_name!r}, "
                "and no matching explicit .apworld path was supplied."
            )
        source = {"source": "builtin", "module": builtin_module}

    source.update(_GAME_OVERRIDES.get(game_name, {}))
    source.setdefault("logic_world_class", LogicWorld)
    return source


@contextmanager
def _silence_analysis_warnings():
    """Silence deprecation-warning spam while analysis is running."""
    old_showwarning = warnings.showwarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        warnings.showwarning = lambda *args, **kwargs: None
        try:
            yield
        finally:
            warnings.showwarning = old_showwarning


def _install_scoped_worlds_namespace() -> None:
    """
    Install a lightweight `worlds` package namespace without executing
    Archipelago 0.6.7's worlds/__init__.py.

    AP 0.6.7 eagerly imports every bundled world from worlds/__init__.py. That
    is undesirable for analysis workers: analyzing OoT should not import
    Secret of Evermore, Zillion, etc. or require their optional dependencies.

    A normal Python package only needs a module object with __path__ for
    submodule imports to work. We create that namespace, then explicitly import
    only the requested world's module plus whatever dependencies that module
    itself imports (for example worlds.generic.Rules).

    Future analyze_yaml() support can use this same mechanism after resolving
    the YAML's game to its Archipelago world-module name.
    """
    existing = sys.modules.get("worlds")
    if existing is not None:
        # A scoped namespace created by us is safe to reuse. If the full AP
        # registry was already imported in this interpreter, leave it alone;
        # replacing a live package would invalidate registered classes. Fresh
        # worker processes never hit this branch.
        if getattr(existing, "_ap_shapley_scoped", False):
            return
        return

    # BaseClasses is safe to import: it does not execute worlds/__init__.py.
    import BaseClasses

    ap_root = Path(BaseClasses.__file__).resolve().parent
    worlds_path = ap_root / "worlds"
    if not worlds_path.is_dir():
        raise RuntimeError(
            f"Could not locate Archipelago worlds directory at {worlds_path}"
        )

    package = types.ModuleType("worlds")
    package.__file__ = str(worlds_path / "__init__.py")
    package.__package__ = "worlds"
    package.__path__ = [str(worlds_path)]
    package._ap_shapley_scoped = True
    sys.modules["worlds"] = package


def _load_world_type(module_name: str, class_name: str):
    """Load exactly one Archipelago world module in the current process."""
    _install_scoped_worlds_namespace()

    # Import AutoWorld explicitly so BaseClasses.set_options() can resolve
    # `from worlds import AutoWorld` from our scoped package.
    importlib.import_module("worlds.AutoWorld")
    module = importlib.import_module(f"worlds.{module_name}")

    try:
        return getattr(module, class_name)
    except AttributeError as exc:
        raise RuntimeError(
            f"Archipelago world module worlds.{module_name} does not expose "
            f"{class_name}."
        ) from exc


_APWORLD_SPECS = {}
_APWORLD_FINDER_INSTALLED = False


class _ScopedAPWorldFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        return _APWORLD_SPECS.get(fullname)


def _load_external_world_type(apworld_path: str, expected_game: str):
    """Load one explicitly selected APWorld without scanning global plugins."""
    global _APWORLD_FINDER_INSTALLED

    path = Path(apworld_path).resolve()
    manifest = _read_apworld_manifest(path)
    actual_game = manifest.get("game")
    if actual_game is not None and actual_game != expected_game:
        raise ValueError(
            f"{path} provides {actual_game!r}, not requested game {expected_game!r}."
        )

    from Utils import version_tuple, tuplize_version

    minimum = manifest.get("minimum_ap_version")
    maximum = manifest.get("maximum_ap_version")
    if minimum and tuplize_version(minimum) > version_tuple:
        raise RuntimeError(f"{path.name} requires Archipelago >= {minimum}")
    if maximum and tuplize_version(maximum) < version_tuple:
        raise RuntimeError(f"{path.name} requires Archipelago <= {maximum}")

    _install_scoped_worlds_namespace()
    auto_world = importlib.import_module("worlds.AutoWorld")
    existing = auto_world.AutoWorldRegister.world_types.get(expected_game)
    if existing is not None:
        return existing

    with ZipFile(path) as archive:
        roots = sorted({
            name.split("/", 1)[0]
            for name in archive.namelist()
            if name.count("/") == 1 and name.endswith("/__init__.py")
        })
    if len(roots) != 1:
        raise RuntimeError(
            f"Could not identify one root package in {path}; found {roots}."
        )

    fullname = f"worlds.{roots[0]}"
    spec = zipimport.zipimporter(str(path)).find_spec(fullname)
    if spec is None:
        raise ImportError(f"Could not create import spec for {fullname} from {path}")
    _APWORLD_SPECS[fullname] = spec
    if not _APWORLD_FINDER_INSTALLED:
        sys.meta_path.insert(0, _ScopedAPWorldFinder())
        _APWORLD_FINDER_INSTALLED = True
    importlib.import_module(fullname)

    try:
        return auto_world.AutoWorldRegister.world_types[expected_game]
    except KeyError as exc:
        raise RuntimeError(
            f"{path} imported as {fullname} but did not register {expected_game!r}."
        ) from exc


def _load_adapter_world_type(game_name: str, adapter: dict):
    if adapter["source"] == "apworld":
        return _load_external_world_type(adapter["apworld_path"], game_name)

    _install_scoped_worlds_namespace()
    auto_world = importlib.import_module("worlds.AutoWorld")
    importlib.import_module(f"worlds.{adapter['module']}")
    try:
        return auto_world.AutoWorldRegister.world_types[game_name]
    except KeyError as exc:
        raise RuntimeError(
            f"worlds.{adapter['module']} loaded but did not register {game_name!r}."
        ) from exc


def _setup_single_multiworld(
    world_type,
    options: dict,
    seed: int,
):
    """
    Construct one Archipelago MultiWorld without importing test.general.

    This mirrors AP 0.6.7 test.general.setup_multiworld() for a single player.
    The important difference is that the selected world type has already been
    loaded through the scoped loader above, so unrelated games are never
    imported.
    """
    from BaseClasses import CollectionState, MultiWorld
    from Options import StartInventoryPool
    from worlds.AutoWorld import call_all
    from worlds.generic.Rules import exclusion_rules

    multiworld = MultiWorld(1)
    multiworld.game = {1: world_type.game}
    multiworld.player_name = {1: "Analyzer"}
    multiworld.set_seed(seed)

    args = Namespace()
    for key, option in world_type.options_dataclass.type_hints.items():
        raw_value = options.get(key, option.default)

        # YAML analysis already resolved this through the appropriate AP
        # Option class. Manual analyze_oot() calls still arrive as ordinary
        # primitive/raw values and are converted here.
        if isinstance(raw_value, option):
            value = raw_value
        else:
            value = option.from_any(raw_value)

        setattr(args, key, {1: value})

    multiworld.set_options(args)
    multiworld.state = CollectionState(multiworld)

    call_all(multiworld, "generate_early")

    world = multiworld.worlds[1]
    for item_name, count in world.options.start_inventory.value.items():
        for _ in range(count):
            multiworld.push_precollected(world.create_item(item_name))

    from_pool = getattr(
        world.options,
        "start_inventory_from_pool",
        StartInventoryPool({}),
    ).value.copy()
    for item_name, count in from_pool.items():
        for _ in range(count):
            multiworld.push_precollected(world.create_item(item_name))

    call_all(multiworld, "create_regions")
    call_all(multiworld, "create_items")
    call_all(multiworld, "set_rules")
    exclusion_rules(multiworld, 1, world.options.exclude_locations.value)
    call_all(multiworld, "connect_entrances")
    call_all(multiworld, "generate_basic")

    # Match AP's post-create_items removal of start_inventory_from_pool.
    # Missing requested copies remain precollected but do not shrink the pool.
    if from_pool:
        remaining = from_pool.copy()
        new_itempool = []
        removed = 0
        for item in multiworld.itempool:
            if remaining.get(item.name, 0):
                remaining[item.name] -= 1
                removed += 1
            else:
                new_itempool.append(item)
        new_itempool.extend(world.create_filler() for _ in range(removed))
        multiworld.itempool[:] = new_itempool

    call_all(multiworld, "pre_fill")

    _run_special_fill_hooks(multiworld)

    return multiworld


def _build_oot(
    options: dict,
    world_seed: int,
    player: int = 1,
):
    """Build one resolved OoT logic world using game-scoped imports."""
    if player != 1:
        raise ValueError("The current single-player analyzer expects player=1")

    world_type = _load_world_type("oot", "OOTWorld")
    multiworld = _setup_single_multiworld(
        world_type=world_type,
        options=options,
        seed=world_seed,
    )

    return OOTLogicWorld(multiworld, player=player)



def _build_logic_world(
    game_name: str,
    options: dict,
    world_seed: int,
    player: int = 1,
    apworld_paths=(),
):
    adapter = _adapter_for_game(game_name, apworld_paths)
    world_type = _load_adapter_world_type(game_name, adapter)
    multiworld = _setup_single_multiworld(
        world_type=world_type,
        options=options,
        seed=world_seed,
    )
    return adapter["logic_world_class"](
        multiworld,
        player=player,
    )


def _analyze_yaml_world_job(job):
    """
    Process-safe worker for one YAML-derived world sample.

    The YAML itself is rolled independently for this world before AP generates
    topology, constrained placements, and any other seeded world randomness.
    """
    (
        world_index,
        game_name,
        weights,
        yaml_roll_seed,
        world_seed,
        sampling_seed,
        shapley_pairs,
        player,
        release_on_goal,
        apworld_paths,
        progress_queue,
    ) = job

    adapter = _adapter_for_game(game_name, apworld_paths)

    def report_pair(count):
        if progress_queue is not None:
            progress_queue.put((world_index, int(count)))

    with _silence_analysis_warnings():
        world_type = _load_adapter_world_type(game_name, adapter)

        options = roll_yaml_options(
            weights=weights,
            world_type=world_type,
            roll_seed=yaml_roll_seed,
        )

        multiworld = _setup_single_multiworld(
            world_type=world_type,
            options=options,
            seed=world_seed,
        )
        logic_world = adapter["logic_world_class"](
            multiworld,
            player=player,
        )

        result = ShapleyAnalyzer(
            logic_world,
            release_on_goal=release_on_goal,
        ).run(
            shapley_pairs=shapley_pairs,
            sampling_seed=sampling_seed,
            progress_callback=report_pair,
            world_seed=world_seed,
        )

        diagnostics = adapter.get("world_diagnostics")
        if diagnostics is not None:
            result.summary_data.update(diagnostics(logic_world))

    return result.strip_logic_world()


def _analyze_world_job(job):
    """
    Process-safe worker for one complete world sample.

    Each completed antithetic Shapley pair sends one tiny progress message
    back to the parent. The parent owns tqdm, so workers never print competing
    progress bars.
    """
    (
        world_index,
        game_module,
        world_class_name,
        options,
        world_seed,
        sampling_seed,
        shapley_pairs,
        player,
        release_on_goal,
        progress_queue,
    ) = job

    if game_module != "oot" or world_class_name != "OOTWorld":
        raise NotImplementedError(
            "Only OoT has a LogicWorld adapter today; the worker bootstrap is "
            "already game-scoped for future adapters."
        )

    def report_pair(count):
        if progress_queue is not None:
            progress_queue.put((world_index, int(count)))

    with _silence_analysis_warnings():
        logic_world = _build_oot(
            options=options,
            world_seed=world_seed,
            player=player,
        )
        result = ShapleyAnalyzer(
            logic_world,
            release_on_goal=release_on_goal,
        ).run(
            shapley_pairs=shapley_pairs,
            sampling_seed=sampling_seed,
            progress_callback=report_pair,
            world_seed=world_seed,
        )

    return result.strip_logic_world()

def _resolve_worker_count(n_jobs: int, world_samples: int) -> int:
    if n_jobs == 0 or n_jobs < -1:
        raise ValueError("n_jobs must be -1 or a positive integer")

    requested = (os.cpu_count() or 1) if n_jobs == -1 else n_jobs
    return max(1, min(int(requested), world_samples))


def _worker_context():
    """Use Windows' required spawn mode while retaining fork on Unix/Colab."""
    if sys.platform == "win32":
        return mp.get_context("spawn")
    if "fork" in mp.get_all_start_methods():
        return mp.get_context("fork")
    return mp.get_context()


def analyze_oot(
    options: dict | None = None,
    world_samples: int = 1,
    shapley_pairs: int = 64,
    seed: int = 2026,
    player: int = 1,
    n_jobs: int = -1,
    progress: bool = True,
    release_on_goal: bool = True,
):
    """
    Analyze OoT item importance across one or more independently generated
    logic worlds.

    Progress is measured in completed antithetic Shapley pairs. With
    world_samples=W and shapley_pairs=P, the visible bar has exactly W*P
    units. World construction is not counted as fake extra work; after the
    pair bar reaches 100%, the description changes briefly to "Finalizing..."
    while aggregate result tables are built.
    """
    if world_samples < 1:
        raise ValueError("world_samples must be >= 1")
    if shapley_pairs < 1:
        raise ValueError("shapley_pairs must be >= 1")

    options = {} if options is None else dict(options)
    workers = _resolve_worker_count(n_jobs, world_samples)

    seed_rng = random.Random(seed)
    job_specs = [
        (
            world_index,
            "oot",
            "OOTWorld",
            options,
            seed_rng.getrandbits(63),
            seed_rng.getrandbits(63),
            shapley_pairs,
            player,
            release_on_goal,
        )
        for world_index in range(world_samples)
    ]

    total_pairs = world_samples * shapley_pairs
    bar = tqdm(
        total=total_pairs,
        disable=not progress,
        desc=f"Worlds 0/{world_samples} | Shapley pairs",
        unit="pair",
        mininterval=0.20,
        leave=True,
    )

    world_results = [None] * world_samples
    worlds_completed = 0

    def update_description():
        bar.set_description(
            f"Worlds {worlds_completed}/{world_samples} | Shapley pairs"
        )

    try:
        with _silence_analysis_warnings():
            parallel = workers > 1 and world_samples > 1

            if parallel:
                # Windows requires spawn; Unix/Colab retains fork when it is
                # available. Manager.Queue gives workers a process-safe,
                # pickleable channel for tiny pair-progress messages.
                mp_context = _worker_context()

                manager = mp_context.Manager()
                progress_queue = manager.Queue()

                try:
                    jobs = [
                        (*spec, progress_queue)
                        for spec in job_specs
                    ]

                    with ProcessPoolExecutor(
                        max_workers=workers,
                        mp_context=mp_context,
                    ) as pool:
                        future_to_index = {
                            pool.submit(_analyze_world_job, job): job[0]
                            for job in jobs
                        }
                        pending = set(future_to_index)

                        # Do not block until a whole world finishes. Poll
                        # futures briefly so pair-completion messages can
                        # continuously advance the parent-owned tqdm bar.
                        while pending:
                            done, pending = wait(
                                pending,
                                timeout=0.20,
                                return_when=FIRST_COMPLETED,
                            )

                            # Drain all pair progress currently available.
                            while True:
                                try:
                                    _, count = progress_queue.get_nowait()
                                except Exception:
                                    break
                                else:
                                    bar.update(count)

                            for future in done:
                                index = future_to_index[future]
                                world_results[index] = future.result()
                                worlds_completed += 1
                                update_description()

                        # All worker callbacks happen before their futures
                        # complete, so one final drain catches any messages
                        # that reached the Manager queue between polls.
                        while True:
                            try:
                                _, count = progress_queue.get_nowait()
                            except Exception:
                                break
                            else:
                                bar.update(count)

                finally:
                    manager.shutdown()

            else:
                # Serial mode uses the exact same pair-based denominator.
                for spec in job_specs:
                    (
                        world_index,
                        _,
                        _,
                        _,
                        world_seed,
                        sampling_seed,
                        _,
                        _,
                        serial_release_on_goal,
                    ) = spec

                    logic_world = _build_oot(
                        options=options,
                        world_seed=world_seed,
                        player=player,
                    )

                    world_results[world_index] = ShapleyAnalyzer(
                        logic_world,
                        release_on_goal=serial_release_on_goal,
                    ).run(
                        shapley_pairs=shapley_pairs,
                        sampling_seed=sampling_seed,
                        progress_callback=bar.update,
                        world_seed=world_seed,
                    )

                    worlds_completed += 1
                    update_description()

            # Keep aggregation visible, but do not distort the numerical
            # denominator with made-up "+2 finalization steps".
            bar.set_description("Finalizing analysis")
            bar.refresh()

            analysis = AnalysisResult(
                world_results=world_results,
                seed=seed,
                options=options,
                player=player,
                progress_stage_callback=None,
            )

            bar.set_description("Analysis complete")
            bar.refresh()
            return analysis
    finally:
        bar.close()


def analyze_yaml(
    yaml_path: str | Path,
    world_samples: int = 1,
    shapley_pairs: int = 64,
    seed: int = 2026,
    player: int = 1,
    n_jobs: int = -1,
    progress: bool = True,
    release_on_goal: bool = True,
    apworld_paths=None,
):
    """
    Analyze one Archipelago player YAML.

    Each world sample independently rerolls any weighted/random YAML settings,
    then independently generates the game's seeded topology and special
    placements. Deterministic YAML settings remain identical across samples.

    Built-in games are discovered from the pinned local Archipelago source.
    External games require an explicit exact ``.apworld`` path (or paths);
    the analyzer never downloads or silently chooses plugin versions.
    """
    if world_samples < 1:
        raise ValueError("world_samples must be >= 1")
    if shapley_pairs < 1:
        raise ValueError("shapley_pairs must be >= 1")

    yaml_path = Path(yaml_path)
    weights = read_player_yaml(yaml_path)
    game_name = fixed_game_name(weights)
    apworld_paths = _normalize_apworld_paths(apworld_paths)

    # Validate support before starting worker processes.
    _adapter_for_game(game_name, apworld_paths)

    workers = _resolve_worker_count(n_jobs, world_samples)
    seed_rng = random.Random(seed)

    job_specs = [
        (
            world_index,
            game_name,
            weights,
            seed_rng.getrandbits(63),  # YAML option rolling
            seed_rng.getrandbits(63),  # AP world generation
            seed_rng.getrandbits(63),  # Shapley permutation sampling
            shapley_pairs,
            player,
            release_on_goal,
            apworld_paths,
        )
        for world_index in range(world_samples)
    ]

    total_pairs = world_samples * shapley_pairs
    bar = tqdm(
        total=total_pairs,
        disable=not progress,
        desc=f"Worlds 0/{world_samples} | Shapley pairs",
        unit="pair",
        mininterval=0.20,
        leave=True,
    )

    world_results = [None] * world_samples
    worlds_completed = 0

    def update_description():
        bar.set_description(
            f"Worlds {worlds_completed}/{world_samples} | Shapley pairs"
        )

    try:
        with _silence_analysis_warnings():
            parallel = workers > 1 and world_samples > 1

            if parallel:
                mp_context = _worker_context()

                manager = mp_context.Manager()
                progress_queue = manager.Queue()

                try:
                    jobs = [
                        (*spec, progress_queue)
                        for spec in job_specs
                    ]

                    with ProcessPoolExecutor(
                        max_workers=workers,
                        mp_context=mp_context,
                    ) as pool:
                        future_to_index = {
                            pool.submit(
                                _analyze_yaml_world_job,
                                job,
                            ): job[0]
                            for job in jobs
                        }
                        pending = set(future_to_index)

                        while pending:
                            done, pending = wait(
                                pending,
                                timeout=0.20,
                                return_when=FIRST_COMPLETED,
                            )

                            while True:
                                try:
                                    _, count = (
                                        progress_queue.get_nowait()
                                    )
                                except Exception:
                                    break
                                else:
                                    bar.update(count)

                            for future in done:
                                index = future_to_index[future]
                                world_results[index] = future.result()
                                worlds_completed += 1
                                update_description()

                        while True:
                            try:
                                _, count = progress_queue.get_nowait()
                            except Exception:
                                break
                            else:
                                bar.update(count)

                finally:
                    manager.shutdown()

            else:
                adapter = _adapter_for_game(game_name, apworld_paths)

                for spec in job_specs:
                    (
                        world_index,
                        _,
                        _,
                        yaml_roll_seed,
                        world_seed,
                        sampling_seed,
                        _,
                        _,
                        serial_release_on_goal,
                        _,
                    ) = spec

                    world_type = _load_adapter_world_type(game_name, adapter)

                    options = roll_yaml_options(
                        weights=weights,
                        world_type=world_type,
                        roll_seed=yaml_roll_seed,
                    )

                    multiworld = _setup_single_multiworld(
                        world_type=world_type,
                        options=options,
                        seed=world_seed,
                    )
                    logic_world = adapter["logic_world_class"](
                        multiworld,
                        player=player,
                    )

                    world_results[world_index] = ShapleyAnalyzer(
                        logic_world,
                        release_on_goal=serial_release_on_goal,
                    ).run(
                        shapley_pairs=shapley_pairs,
                        sampling_seed=sampling_seed,
                        progress_callback=bar.update,
                        world_seed=world_seed,
                    )

                    worlds_completed += 1
                    update_description()

            bar.set_description("Finalizing analysis")
            bar.refresh()

            analysis = AnalysisResult(
                world_results=world_results,
                seed=seed,
                options={
                    "source": "yaml",
                    "yaml_path": str(yaml_path),
                    "game": game_name,
                },
                player=player,
                progress_stage_callback=None,
            )

            # Lightweight metadata useful in notebooks/debugging.
            analysis.yaml_path = str(yaml_path)
            analysis.yaml_game = game_name

            bar.set_description("Analysis complete")
            bar.refresh()
            return analysis

    finally:
        bar.close()


def _expand_yaml_inputs(yaml_inputs) -> list[Path]:
    if isinstance(yaml_inputs, (str, Path)):
        yaml_inputs = [yaml_inputs]
    paths = []
    for raw_path in yaml_inputs:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            paths.extend(sorted(
                candidate for candidate in path.iterdir()
                if candidate.suffix.lower() in {".yaml", ".yml"}
            ))
        elif path.is_file():
            paths.append(path)
        else:
            raise FileNotFoundError(path)
    return [path.resolve() for path in paths]


def _result_stem(yaml_path: Path, game_name: str) -> str:
    player = re.sub(r"[^A-Za-z0-9]+", "_", yaml_path.stem).strip("_").lower()
    game = re.sub(r"[^A-Za-z0-9]+", "_", game_name).strip("_").lower()
    return f"{player}_{game}"


def analyze_yamls(
    yaml_inputs,
    *,
    world_samples: int = 1,
    shapley_pairs: int = 64,
    seed: int = 2026,
    n_jobs: int = -1,
    progress: bool = True,
    release_on_goal: bool = True,
    apworld_paths=None,
    output_dir: str | Path | None = None,
    save_plots: bool = True,
    plot_top_n: int = 30,
):
    """Analyze YAMLs independently; this does not create a joint multiworld game."""
    results = {}
    for yaml_path in _expand_yaml_inputs(yaml_inputs):
        result = analyze_yaml(
            yaml_path,
            world_samples=world_samples,
            shapley_pairs=shapley_pairs,
            seed=seed,
            n_jobs=n_jobs,
            progress=progress,
            release_on_goal=release_on_goal,
            apworld_paths=apworld_paths,
        )
        results[str(yaml_path)] = result
        if output_dir is not None:
            destination = Path(output_dir)
            stem = _result_stem(yaml_path, result.game)
            result.save(destination / f"{stem}.pkl")
            if save_plots:
                result.plot(
                    top_n=plot_top_n,
                    save_path=destination / f"{stem}.png",
                    show=False,
                )
    return results


def compatibility_check_yamls(
    yaml_inputs,
    *,
    world_samples: int = 2,
    shapley_pairs: int = 2,
    seed: int = 2026,
    n_jobs: int = 2,
    apworld_paths=None,
):
    """Run tiny independent analyses and return compact PASS/FAIL records."""
    rows = []
    for yaml_path in _expand_yaml_inputs(yaml_inputs):
        started = time.perf_counter()
        row = {"yaml_path": str(yaml_path)}
        try:
            weights = read_player_yaml(yaml_path)
            game_name = fixed_game_name(weights)
            paths = _normalize_apworld_paths(apworld_paths)
            adapter = _adapter_for_game(game_name, paths)
            world_type = _load_adapter_world_type(game_name, adapter)
            row.update({
                "game": game_name,
                "source": adapter["source"],
                "module": world_type.__module__,
                "world_class": world_type.__name__,
            })
            result = analyze_yaml(
                yaml_path,
                world_samples=world_samples,
                shapley_pairs=shapley_pairs,
                seed=seed,
                n_jobs=n_jobs,
                progress=False,
                apworld_paths=paths,
            )
            summary = result.summary()
            row.update(summary)
            healthy = (
                summary["goal_reachable_with_all_items_all"]
                and summary["still_unreachable_max"] == 0
                and summary["pending_constrained_with_all_offered_max"] == 0
                and summary["max_abs_conservation_difference"] <= 1e-9
            )
            row["status"] = "PASS" if healthy else "FAIL"
            row["reason"] = "" if healthy else "one or more acceptance invariants failed"
        except Exception as exc:
            row["status"] = "LOAD FAIL" if "game" not in row else "FAIL"
            row["reason"] = f"{type(exc).__name__}: {exc}"
            row["traceback"] = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__, limit=8)
            )
        row["runtime_seconds"] = time.perf_counter() - started
        rows.append(row)
    return rows
