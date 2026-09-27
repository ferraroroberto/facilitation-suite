"""The breakout-group algorithm, ported verbatim from facilitation-shuffle.

``build_groups(present) -> {"pairs", "g4a", "g4b"}`` — three rounds for a
1-2-4-all workshop:

- **pairs** (phase 1): shuffled pairs; ``n % 4 == 1`` → a trio first,
  ``n % 4 == 3`` → a trio last, so a trio stays whole in later merges.
- **g4a** (phase 2): whole phase-1 units merged into 4s — a pair is never
  split; ``n % 4 == 2`` → one block of 6 instead of a stray pair.
- **g4b** (phase 3): re-mixed into rooms of 4 (a room of 3, or the last
  room of 5, takes the remainder; never fewer than 3) by up to
  120 000 random shuffles, keeping the layout with the fewest people who
  share a room with someone from their round-A group; stops early at 0.

The function bodies below are the original's, unchanged (only the Excel
read/write CLI was left behind); ``tests/test_groups.py`` pins the rules.
"""

from __future__ import annotations

import logging
import random
import sys

log = logging.getLogger(__name__)


def _all_pair_units(shuffled: list[str]) -> list[list[str]]:
    """Slice even-length list into consecutive pairs (phase-1 units of size 2)."""
    n = len(shuffled)
    if n % 2 != 0:
        raise ValueError(f"Expected even n for all-pairs phase; got {n}")
    return [shuffled[i : i + 2] for i in range(0, n, 2)]


def _trio_last_units(shuffled: list[str]) -> list[list[str]]:
    """For n ≡ 3 (mod 4): (n−3)/2 pairs, then one trio at the end — total n people."""
    n = len(shuffled)
    if n < 3 or n % 2 == 0:
        raise ValueError(f"Expected odd n >= 3 for trio-last; got {n}")
    units: list[list[str]] = []
    i = 0
    while i < n - 3:
        units.append(shuffled[i : i + 2])
        i += 2
    units.append(shuffled[n - 3 : n])
    return units


def _trio_first_units(shuffled: list[str]) -> list[list[str]]:
    """For n ≡ 1 (mod 4): one trio first, then pairs — keeps trio atomic for later merges."""
    n = len(shuffled)
    if n < 3 or n % 4 != 1:
        raise ValueError(f"Expected n ≡ 1 (mod 4) for trio-first; got n={n}")
    units: list[list[str]] = [shuffled[0:3]]
    i = 3
    while i < n:
        units.append(shuffled[i : i + 2])
        i += 2
    return units


def _merge_adjacent_units(pair_units: list[list[str]]) -> list[list[str]]:
    """
    Phase-2 from ordered units: concatenate unit[0]+unit[1], unit[2]+unit[3], …
    If odd count of units, the last unit stands alone (e.g. a trio after pairs).
    Never splits a unit — couples/trios stay together.
    """
    groups: list[list[str]] = []
    p = len(pair_units)
    j = 0
    while j < p:
        if j + 1 < p:
            groups.append(pair_units[j] + pair_units[j + 1])
            j += 2
        else:
            groups.append(pair_units[j])
            j += 1
    return groups


def _merge_pair_units_mod4_eq2(pair_units: list[list[str]]) -> list[list[str]]:
    """
    n ≡ 2 (mod 4), all units are pairs: (2k+1) pair-units.

    First (k−1) merges are 2+2 → 4; the last merge is three pairs → 6 (no leftover pair of 2).
    n == 2 → single pair only.
    """
    p = len(pair_units)
    if p == 1:
        return [pair_units[0]]
    k = (p - 1) // 2
    groups: list[list[str]] = []
    idx = 0
    for _ in range(k - 1):
        groups.append(pair_units[idx] + pair_units[idx + 1])
        idx += 2
    groups.append(pair_units[idx] + pair_units[idx + 1] + pair_units[idx + 2])
    return groups


def _build_phase1_units(shuffled: list[str], n: int) -> list[list[str]]:
    """
    Build phase-1 units (pair/trio blocks) from a shuffled name list.

    Pattern by n mod 4: all pairs; pairs+trio last; all pairs (n mod 4 == 2); trio first + pairs (n mod 4 == 1).
    """
    if n == 1:
        return [shuffled[0:1]]
    r = n % 4
    if r == 0:
        return _all_pair_units(shuffled)
    if r == 3:
        return _trio_last_units(shuffled)
    if r == 2:
        return _all_pair_units(shuffled)
    return _trio_first_units(shuffled)


def _build_phase2_groups(pair_units: list[list[str]], n: int) -> list[list[str]]:
    """
    Phase-2 = merge whole phase-1 units only (never shuffle people across units).

    If n mod 4 == 2: blocks of 4 plus one block of 6; else adjacent 2+2 merges (or lone last unit).
    """
    r = n % 4
    if r == 2:
        return _merge_pair_units_mod4_eq2(pair_units)
    return _merge_adjacent_units(pair_units)


def _phase3_target_sizes(n: int) -> list[int]:
    """
    Sizes for phase-3 groups: prefer 4, then 3; leftovers absorbed into last group.
    Guarantees each group ≥ 3 when n ≥ 3 (except tiny n).
    """
    if n <= 0:
        return []
    if n < 3:
        return [n]
    sizes: list[int] = []
    rem = n
    while rem > 0:
        # Take 4 only if remainder stays 0 or ≥3 (avoid stranding 1–2 alone)
        if rem >= 4 and (rem - 4 == 0 or rem - 4 >= 3):
            sizes.append(4)
            rem -= 4
        elif rem >= 3:
            sizes.append(3)
            rem -= 3
        else:
            if sizes:
                sizes[-1] += rem
            else:
                sizes.append(rem)
            rem = 0
    return sizes


def _slice_groups_in_order(order: list[str], sizes: list[int]) -> list[list[str]]:
    """Cut a permutation into contiguous chunks of given sizes (phase-3 trial layout)."""
    groups: list[list[str]] = []
    k = 0
    for s in sizes:
        groups.append(order[k : k + s])
        k += s
    return groups


def _phase2_cohort_overlap_penalty(
    groups_phase3: list[list[str]], person_to_phase2_idx: dict[str, int]
) -> int:
    """
    Lower is better: for each phase-3 group, count (c−1) when c>1 people share the same
    phase-2 table id (extra “repeats” vs ideal mixing).
    """
    pen = 0
    for g in groups_phase3:
        counts: dict[int, int] = {}
        for p in g:
            i = person_to_phase2_idx[p]
            counts[i] = counts.get(i, 0) + 1
        for c in counts.values():
            if c > 1:
                pen += c - 1
    return pen


def _build_phase3_relaxed(
    shuffled: list[str],
    n: int,
    person_to_phase2_idx: dict[str, int],
    iterations: int = 120_000,
) -> tuple[list[list[str]], int]:
    """
    Random search: shuffle names, slice by _phase3_target_sizes, keep layout with lowest
    phase-2 overlap penalty. Allows same phase-2 table twice in one phase-3 group if needed.
    """
    sizes = _phase3_target_sizes(n)
    assert sum(sizes) == n, (sizes, n)
    pool = shuffled[:]
    best: list[list[str]] | None = None
    best_pen = sys.maxsize
    for _ in range(iterations):
        random.shuffle(pool)
        trial = _slice_groups_in_order(pool, sizes)
        pen = _phase2_cohort_overlap_penalty(trial, person_to_phase2_idx)
        if pen < best_pen:
            best_pen = pen
            best = [x[:] for x in trial]
        if best_pen == 0:
            break
    assert best is not None
    return best, best_pen


def _assign_ids(groups: list[list[str]]) -> dict[str, int]:
    """Map each person to 1-based group index within that phase."""
    out: dict[str, int] = {}
    for idx, g in enumerate(groups, start=1):
        for p in g:
            out[p] = idx
    return out


def build_groups(present: list[str]) -> dict[str, list[list[str]]]:
    """
    Run the full three-phase orchestration for a list of present participants.

    Parameters
    ----------
    present : list[str]
        Names of present participants (will be shuffled in place of a copy).

    Returns
    -------
    dict with keys ``"pairs"`` (phase-1 units), ``"g4a"`` (phase-2 groups of ~4),
    ``"g4b"`` (phase-3 groups of ~4, optimised for mixing).
    """
    n = len(present)
    shuffled = present[:]
    random.shuffle(shuffled)

    pair_units = _build_phase1_units(shuffled, n)
    log.info("Phase1 units: %s -> sizes %s", len(pair_units), [len(u) for u in pair_units])

    groups_g4a = _build_phase2_groups(pair_units, n)
    log.info("Phase2 (personal_readme_g4): %s -> sizes %s", len(groups_g4a), [len(g) for g in groups_g4a])

    person_to_phase2_idx: dict[str, int] = {p: idx for idx, g in enumerate(groups_g4a) for p in g}

    groups_g4b, p3_pen = _build_phase3_relaxed(shuffled, n, person_to_phase2_idx)
    sz = [len(g) for g in groups_g4b]
    log.info(
        "Phase3 (common_enemy_g4): %s -> sizes %s (min=%s; phase2-cohort overlap penalty=%s)",
        len(groups_g4b), sz, min(sz), p3_pen,
    )

    return {"pairs": pair_units, "g4a": groups_g4a, "g4b": groups_g4b}
