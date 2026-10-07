from __future__ import annotations

import copy
from contextlib import contextmanager
import random
from pathlib import Path


@contextmanager
def _temporary_global_random_seed(seed: int):
    """
    Archipelago 0.6.7 Option.from_any("random"), linked options, triggers,
    and weighted choices use Python's module-level random generator.

    Give each sampled world its own deterministic YAML-roll RNG without
    leaking that state into later work performed by the worker.
    """
    old_state = random.getstate()
    random.seed(seed)
    try:
        yield
    finally:
        random.setstate(old_state)


def read_player_yaml(path: str | Path) -> dict:
    """
    Read exactly one non-empty Archipelago player YAML document.

    Uses Archipelago's own YAML loader so duplicate-key behavior matches
    the generator rather than silently accepting malformed player files.
    """
    from Utils import parse_yamls

    path = Path(path)
    with path.open("rb") as f:
        text = f.read().decode("utf-8-sig")

    docs = [doc for doc in parse_yamls(text) if doc is not None]

    if not docs:
        raise ValueError(f"No YAML document found in {path}.")
    if len(docs) != 1:
        raise ValueError(
            "analyze_yaml() currently expects one player YAML document "
            f"per file; found {len(docs)} non-empty documents in {path}."
        )
    if not isinstance(docs[0], dict):
        raise ValueError("The YAML document must contain a mapping/object.")

    return docs[0]


def fixed_game_name(weights: dict) -> str:
    """
    Return the YAML's game name.

    The game itself must currently be deterministic. Weighted settings
    *inside* the selected game are fully supported and rerolled per world.
    """
    game = weights.get("game")

    if not isinstance(game, str):
        raise ValueError(
            "analyze_yaml() currently requires a fixed top-level 'game' "
            "value. Weighted/random game selection is not supported yet."
        )

    return game


def _get_choice(option: str, root: dict, default=None):
    """
    Match Archipelago 0.6.7 Generate.get_choice().
    """
    if option not in root:
        return default

    value = root[option]

    if type(value) is list:
        return random.choices(value)[0]

    if type(value) is not dict:
        return value

    if not value:
        return default

    if any(value.values()):
        return random.choices(
            list(value.keys()),
            weights=list(map(int, value.values())),
        )[0]

    raise RuntimeError(
        f'All options specified in "{option}" are weighted as zero.'
    )


def _update_weights(
    weights: dict,
    new_weights: dict,
    update_type: str,
    name: str,
) -> dict:
    """
    Match the merge/remove semantics used by AP 0.6.7 linked options and
    triggers. Unknown merged keys are allowed just as in the generator.
    """
    from collections import Counter

    cleaned = {}

    for option, incoming in new_weights.items():
        option_name = option.lstrip("+-")

        if option.startswith("+") and option_name in weights:
            current = copy.deepcopy(weights[option_name])

            if isinstance(incoming, set):
                current.update(incoming)
            elif isinstance(incoming, list):
                current.extend(incoming)
            elif isinstance(incoming, dict):
                counter = Counter(current)
                counter.update(incoming)
                current = dict(counter)
            else:
                raise TypeError(
                    f"Cannot merge {type(incoming).__name__} into "
                    f"{option_name!r}."
                )

            cleaned[option_name] = current

        elif option.startswith("-") and option_name in weights:
            current = copy.deepcopy(weights[option_name])

            if isinstance(incoming, set):
                current.difference_update(incoming)
            elif isinstance(incoming, list):
                for element in incoming:
                    current.remove(element)
            elif isinstance(incoming, dict):
                counter = Counter(current)
                counter.subtract(incoming)
                current = dict(counter)
            else:
                raise TypeError(
                    f"Cannot remove {type(incoming).__name__} from "
                    f"{option_name!r}."
                )

            cleaned[option_name] = current

        else:
            cleaned[option_name] = copy.deepcopy(incoming)

    weights.update(cleaned)
    return weights


def _roll_linked_options(weights: dict) -> dict:
    import Options

    weights = copy.deepcopy(weights)

    for option_set in weights.get("linked_options", []):
        if "name" not in option_set:
            raise ValueError(
                "One of the YAML linked_options entries has no name."
            )

        if Options.roll_percentage(option_set["percentage"]):
            for category_name, category_options in (
                option_set["options"].items()
            ):
                target = weights
                if category_name:
                    target = target[category_name]

                _update_weights(
                    target,
                    category_options,
                    "Linked",
                    option_set["name"],
                )

    return weights


def _roll_triggers(
    weights: dict,
    triggers: list,
    valid_keys: set,
) -> dict:
    import Options

    weights = copy.deepcopy(weights)

    for i, option_set in enumerate(triggers):
        try:
            target = weights
            category = option_set.get("option_category")
            if category:
                target = target[category]

            key = _get_choice("option_name", option_set)
            trigger_result = _get_choice(
                "option_result",
                option_set,
            )

            result = _get_choice(key, target)
            target[key] = result

            if (
                result == trigger_result
                and Options.roll_percentage(
                    _get_choice("percentage", option_set, 100)
                )
            ):
                for category_name, category_options in (
                    option_set["options"].items()
                ):
                    update_target = weights
                    if category_name:
                        update_target = update_target[category_name]

                    _update_weights(
                        update_target,
                        category_options,
                        "Triggered",
                        str(option_set.get("option_name", i)),
                    )

            valid_keys.add(key)

        except Exception as exc:
            raise ValueError(
                f"YAML trigger #{i + 1} is invalid."
            ) from exc

    return weights


def roll_yaml_options(
    weights: dict,
    world_type,
    roll_seed: int,
) -> dict:
    """
    Resolve one sampled world's game options using AP 0.6.7 semantics.

    Weighted dictionaries, lists, "random"/random-range option values,
    linked_options, and triggers are rerolled independently for every
    world sample.

    Returns resolved Archipelago Option objects suitable for the scoped
    world builder.
    """
    import Options
    from BaseClasses import PlandoOptions

    with _temporary_global_random_seed(roll_seed):
        rolled = copy.deepcopy(weights)

        if "linked_options" in rolled:
            rolled = _roll_linked_options(rolled)

        valid_keys = {"triggers"}

        if "triggers" in rolled:
            rolled = _roll_triggers(
                rolled,
                rolled["triggers"],
                valid_keys,
            )

        game = _get_choice("game", rolled)
        if game != world_type.game:
            raise ValueError(
                f"YAML selected game {game!r}, but the scoped worker "
                f"loaded {world_type.game!r}."
            )

        if game not in rolled:
            raise ValueError(
                f'No game options section named "{game}" was found.'
            )

        game_weights = rolled[game]

        if "triggers" in game_weights:
            rolled = _roll_triggers(
                rolled,
                game_weights["triggers"],
                valid_keys,
            )
            game_weights = rolled[game]

        resolved = {}

        for option_key, option in (
            world_type.options_dataclass.type_hints.items()
        ):
            try:
                if option_key in game_weights:
                    if option.supports_weighting:
                        raw_value = _get_choice(
                            option_key,
                            game_weights,
                        )
                    else:
                        raw_value = game_weights[option_key]
                else:
                    raw_value = option.default

                option_value = option.from_any(raw_value)

                # Mirror AP's verification step where possible.
                option_value.verify(
                    world_type,
                    str(rolled.get("name") or "Analyzer"),
                    PlandoOptions.bosses,
                )

                # Keep the resolved Option object itself. Some AP option
                # classes store internal values as Counter/set-like objects
                # that are not accepted by from_any() if converted again.
                resolved[option_key] = option_value

            except Exception as exc:
                raise ValueError(
                    f"Error resolving YAML option {option_key!r} "
                    f"for {game}."
                ) from exc

        return resolved
