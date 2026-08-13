# Squad requirements (`squads.json`)

`squads.json` defines squad-building goals, organized into **categories**, each
containing one or more **squads**. `squad_report.py` evaluates these against the
collected guild data. The format is validated against
[`squads.schema.json`](squads.schema.json).

## Top-level structure

```json
{
  "categories": [
    {
      "name": "Category display name",
      "squads": [ { "...": "one or more squad definitions" } ]
    }
  ]
}
```

A category may have an empty `squads` array (e.g. as a placeholder for work in
progress).

## A squad

A squad has two relic-evaluation **modes**:

- **`minRelic`** (default) — every unit needs at least a hard relic floor.
- **`commonRelic`** — raid-style: no floor. The squad reports its **best
  common relic level**: the highest value from `thresholds` that *every* squad
  unit reaches. The higher the common level, the better the raid performance.

### `minRelic` squad

```json
{
  "name": "Squad display name",
  "mode": "minRelic",
  "minRelic": 5,
  "size": 5,
  "required": ["Bossk", { "name": "Boba Fett", "minRelic": 7 }],
  "pool": ["Boushh (Leia Organa)", "Jango Fett", "Embo", "Aurra Sing"],
  "poolCount": 2
}
```

| Field        | Required | Default | Meaning                                                                 |
|--------------|----------|---------|-------------------------------------------------------------------------|
| `name`       | yes      |         | Display name of the squad.                                               |
| `mode`       | no       | `minRelic` | `minRelic` or `commonRelic`.                                           |
| `minRelic`   | no       | `0`     | Minimum relic for every unit (`minRelic` mode). `0` = just owned.        |
| `size`       | no       | `5`     | Final squad size. Must equal `len(required) + poolCount`.                |
| `required`   | yes      |         | Specific units that must be in the squad.                                |
| `pool`       | no       | `[]`    | Extra units to fill the remaining slots (see below).                     |
| `poolCount`  | no       | `size - len(required)` | How many pool units are needed.                      |

### `commonRelic` squad

```json
{
  "name": "JMMV",
  "mode": "commonRelic",
  "size": 3,
  "required": ["Jedi Master Mace Windu", "Depa Billaba", "Temple Guard"]
}
```

| Field        | Required | Default | Meaning                                                                 |
|--------------|----------|---------|-------------------------------------------------------------------------|
| `thresholds` | no       | `[0, 1, 3, 5, 7, 8, 9]` | Relic levels that count. The best common level is the highest of these that all squad units reach. |

`minRelic` is ignored in this mode. Example: squad units at R9, R7, R5, R8, R6
with thresholds `[0,1,3,5,7,8,9]` — all units are ≥ 5 but not all ≥ 7, so the
best common relic level is **R5**. A missing required unit means the squad
can't form, so there is no common level.

### Units (`required` entries and named `pool` entries)

A unit is either a plain display-name string, or an object with an optional
per-unit relic override:

```json
"Bossk"
{ "name": "Boba Fett", "minRelic": 7 }
```

`minRelic` in the object overrides the squad-level value for that unit.

### The `pool`

Two forms:

1. **A specific list of units:**
   ```json
   "pool": ["Boushh (Leia Organa)", "Jango Fett", "Embo", "Aurra Sing"],
   "poolCount": 2
   ```
   You need `poolCount` of these at the required relic.

2. **A faction/role tag:** any owned **character** carrying the tag can fill a
   pool slot (ships are excluded):
   ```json
   "pool": { "tag": "Bounty Hunter" },
   "poolCount": 4
   ```
   Tags are matched case-insensitively against each unit's faction tags (e.g.
   `Bounty Hunter`, `Jedi`, `Scoundrel`, `First Order`).

Units already listed in `required` are never double-counted from the pool.

## Example

```json
{
  "categories": [
    {
      "name": "Bounty Hunters",
      "squads": [
        {
          "name": "Bossk BH",
          "minRelic": 5,
          "required": ["Bossk"],
          "pool": ["Boba Fett", "Dengar", "IG-88", "Boushh (Leia Organa)", "Fennec Shand"],
          "poolCount": 4
        },
        {
          "name": "BH tag squad",
          "minRelic": 3,
          "required": ["Greef Karga"],
          "pool": { "tag": "Bounty Hunter" },
          "poolCount": 4
        }
      ]
    }
  ]
}
```

## Evaluation rules

### `minRelic` mode

Per unit, the report assigns one of three statuses:

- **met** — owned and relic level >= required relic
- **upgrade** — owned but relic level below required relic
- **missing** — not owned

A squad is **complete** when every required unit is met and `poolCount` pool
units are met. The numeric **gap** measures how far a player is:

```
gap = (missing required units  × 2)
    + (upgrade required units  × 1)
    + (chosen pool slots: missing × 2, upgrade × 1)
```

`gap == 0` means the squad is complete. There is no imposed "close" cutoff —
the gap is reported per player so you can pick your own threshold.

### `commonRelic` mode

No gap. Each player's result is their **best common relic level** (`commonRelic`)
plus the `nextThreshold` they'd need to push the whole squad to. The report
still lists every required unit's actual relic level and the chosen pool units,
flagging the units that block the next threshold.
