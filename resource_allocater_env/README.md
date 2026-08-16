# CPU Core Thermal Simulation — Environment Spec

Status: **spec only** — no implementation, no numeric constants finalized. This document
defines the state model, the thermodynamic relationships between quantities, and the
event/ordering semantics, so that any concrete heat-transfer equation and any concrete
numeric constants can be dropped in later without changing this structure.

---

## 1. State Model

### 1.1 Per-core state (persists across shutdown — never destroyed while `core_id` is valid)

| Field | Type | Notes |
|---|---|---|
| `core_id` | fixed, from `__init__` | stable set, doesn't change over the env's lifetime |
| `status` | enum: `RUNNING`, `IDLE`, `SHUTDOWN` | see 1.2 — partially derived, partially latched |
| `temperature` | °C, tracked always, incl. while shut down | bounded `[20, ∞)`; shutdown latch fires at the (open) high threshold |
| `requested_load` | W, tracked always, incl. while shut down | the caller's most recently set target — see §5 for how this behaves across shutdown |
| `last_synced_ts` | internal bookkeeping | the timestamp this core's `temperature`/`status` have been integrated up to |
| `pending_load_change` | `(timestamp, watts)` or none | at most one outstanding change per core — see §6.3 |

### 1.2 Status: derived vs. latched

`status` is **not** three independent values — it's one latch bit (`shutdown_latched: bool`)
plus a derived value:

```
if shutdown_latched:
    status = SHUTDOWN
elif requested_load > 0:
    status = RUNNING
else:
    status = IDLE
```

The latch is necessary (rather than deriving shutdown purely from `temperature ≥ 80`) because
restart uses hysteresis (§5) — the sim needs to remember "this core is currently in a
shut-down episode" independent of exactly where its temperature sits at any given instant.

### 1.3 Global state

| Field | Type | Notes |
|---|---|---|
| `core_ids` | fixed set, from `__init__` | |
| `passive_cooling_capacity` | shared pool, units TBD (§2) | one number for the whole processor |
| `active_cooling_capacity_per_core` | fixed constant, units TBD (§2) | same constant for every core; **not pooled** — see §4.2 |

---

## 2. Units & Conventions — ASSUMPTION, needs your confirmation

Your recollection ("W/sec and C/sec") and the existing bullets ("2W/sec" heating/cooling)
mix power and temperature-rate units without a stated conversion. I'm resolving this as
follows; flag if you remember the original doing something different:

- **`load`** is in **watts (W)** — a static input, not a rate.
- **`temperature`** and all *rates of temperature change* are in **°C/sec**.
- **Cooling capacities** (`passive_cooling_capacity`, `active_cooling_capacity_per_core`)
  are also expressed in **°C/sec** — i.e. "how much temperature-rise-rate this cooling
  resource can offset," not raw watts. This sidesteps needing a separate watts→°C thermal-mass
  constant, at the cost of folding that constant into the (still-open) heat function `H` below.
- The literal "2 W/sec" phrasing in your original bullets is read as shorthand for
  "2 °C/sec," consistent with this convention.

If the original Optiver equations actually kept watts and °C/sec as separate unit systems
connected by an explicit thermal-mass/specific-heat constant, that constant becomes another
open parameter of `H` (§3.1) rather than a structural change — the rest of this spec holds
either way.

---

## 3. Thermodynamics

### 3.1 Heat-gain function `H(load)` — OPEN, pluggable

`H: watts → °C/sec` is the rate at which a core's own compute heat would raise its
temperature, **before any cooling is applied**. Structural constraints only (no coefficients):

- Monotonic non-decreasing in `load`.
- `H(0) ≤ 0` — an idle core (zero load) has non-positive self-heating, consistent with
  "idle cores cool" being a *default* behavior, not something requiring passive/active help.
- The two numbers you already have — idle self-cools at ~2°C/sec, an active core heats at
  ~2°C/sec — are read as **two sample outputs of `H`** (e.g. `H(0) ≈ -2`, `H(load_ref) ≈ +2`
  for some reference load), not the definition of `H` itself. The actual shape (linear,
  piecewise, saturating at high load, etc.) is left open.

### 3.2 Cooling need

```
need_i(t) = max(H(load_i(t)), 0)
```

This is "how much cooling this core's own heat generation demands right now" — i.e. the
rate that must be offset to hold temperature steady. Idle cores (where `H ≤ 0`) have
`need = 0` by construction: they're already net-cooling on their own and don't generate
demand. This is consistent with — not in conflict with — "passive cooling applies to all
cores, even idle": every core is *eligible* to receive an allocation from the passive pool;
idle cores simply compute to zero because they have nothing to offset. Passive cooling isn't
excluded from idle cores, it's just naturally moot for them under this formula.

### 3.3 Passive cooling — shared, proportional to need

Two sub-cases, since "proportional to need" is ambiguous when supply exceeds total demand.
Proposed rule (**flag if you recall the original doing it differently**):

```
total_need(t) = sum(need_i(t) for all cores i)

if total_need <= passive_cooling_capacity:
    passive_share_i = need_i(t)                      # fully met, no contention
    # (passive_cooling_capacity - total_need) goes unused this instant)
else:
    passive_share_i = passive_cooling_capacity * (need_i(t) / total_need(t))   # rationed
```

i.e. proportionality only governs *rationing under contention*; when there's slack, every
core simply gets what it needs and the rest is unused. This is the standard reading of
"proportional to need" as a fairness rule, but it's a real design choice — the strict
alternative (`passive_share_i = capacity * need_i/total_need` unconditionally, which can
over-supply cores when slack exists) is also defensible and worth deciding explicitly before
implementation.

### 3.4 Active cooling — per-core fixed slot, deficit-triggered

```
remaining_need_i(t) = need_i(t) - passive_share_i(t)

if remaining_need_i(t) > 0:
    active_share_i(t) = min(remaining_need_i(t), active_cooling_capacity_per_core)
else:
    active_share_i(t) = 0
```

Per your answers: active cooling is a **fixed constant per running core**, **not pooled**
across cores — so there's no cross-core contention for active cooling the way there is for
passive. A core either draws from its own fixed active budget or it doesn't; other cores'
active cooling is unaffected either way. (This also makes the "reclaim on shutdown" question
moot — there's no shared pool to reclaim into.)

### 3.5 Net rate & integration

```
net_rate_i(t) = H(load_i(t)) - passive_share_i(t) - active_share_i(t)
```

`net_rate_i` can be negative even while the core is actively heating from load, if combined
passive + active cooling exceeds `need_i`. Temperature integrates piecewise-linearly between
events (load changes, capacity changes, threshold crossings) and is clamped at the 20°C floor.
Because everything above is piecewise-linear in time between such events, exact crossing
times (e.g. "when does this core hit the shutdown threshold") can be solved for algebraically
rather than approximated by sub-stepping — see §6.2.

---

## 4. Status Transitions

### 4.1 Shutdown

Latches `shutdown_latched = True` the instant `temperature` reaches the (open, TBD) high
threshold, computed exactly (§6.2), not approximated at tick granularity.

### 4.2 Restart

- The latch clears once `temperature` drops below a **hysteresis threshold strictly lower
  than the shutdown threshold** (exact value open/TBD — this gap is what prevents
  shutdown/restart chattering right at the boundary).
- Clearing the latch does **not** by itself mean the core starts running. Per your agreement:
  a core only actually transitions out of `SHUTDOWN` if it has a **nonzero `requested_load`**
  pending at that moment. If `requested_load == 0` when the hysteresis threshold is crossed,
  the core stays latched `SHUTDOWN` — there's no point restarting into `IDLE` with nothing to
  do, and a subsequent `set_core_load` call with nonzero watts is what actually triggers
  restart eligibility to be acted on.
- Restart is evaluated during `tick()`, not inside `set_core_load()` — a `set_core_load` call
  on a shut-down core only updates `requested_load`; the transition itself happens lazily,
  the next time `tick()` integrates past the hysteresis crossing (or immediately, if the core
  is already below the hysteresis threshold when the load is set).

### 4.3 Running ↔ Idle

Derived directly from `requested_load` crossing zero (§1.2) — no separate threshold or
latch needed here, since there's no hysteresis concern between these two states.

---

## 5. `set_core_load(timestamp, core_id, watts)`

- Stores `pending_load_change = (timestamp, watts)` for that core, **overwriting** any
  previously stored, not-yet-applied pending change for the same core (latest-value-wins,
  per your answer to Q14 — no queue of multiple changes per core).
- `timestamp` must be `≥` the core's `last_synced_ts` — otherwise raise. (Q13: violating
  monotonicity is a caller bug, not something to silently absorb, especially given there's no
  background thread to reconcile it.)
- Does not itself move `status` — see §4.2. It only ever writes `requested_load` /
  `pending_load_change`; all state transitions happen inside `tick()`.

---

## 6. `tick(timestamp)`

### 6.1 What counts as "externally visible" (Q11)

`status` transitions — `RUNNING↔IDLE↔SHUTDOWN`, in any direction — are the visible events.
Chosen over a narrower "just shutdown/restart" list for educational depth: it exercises the
full 3-state FSM uniformly (§1.2/§4) rather than special-casing shutdown as the only
interesting transition, and it gives an RL agent observing `tick()`'s return value a signal
for load actually taking effect (RUNNING↔IDLE), not just thermal emergencies.
**Explicitly excluded:** load-magnitude changes that don't cross zero (e.g. 40W→60W while
already `RUNNING`) are not reported — worth confirming this matches what you want the agent
to be able to observe, versus needing a separate state-query method for continuous values
(temperature, load, remaining budget) alongside the event-only `tick()` return.

### 6.2 Exact threshold crossings, not sub-stepping

Since §3.5 is piecewise-linear in time between the events that change its inputs (a pending
load change taking effect, a status transition altering `need_i`, capacity constants
changing), the exact time a core crosses the shutdown or restart-hysteresis threshold within
`[last_synced_ts, timestamp]` can be solved for directly per linear segment, rather than
approximated at whatever granularity `tick()` happens to be called. This makes the sim
correct regardless of how large the gap between ticks is.

### 6.3 Ordering within one `tick()` call

For each core with a `pending_load_change` timestamp `≤` the target `timestamp`:

1. Integrate temperature from `last_synced_ts` to `pending_load_change.timestamp` using the
   **old** load, checking for a threshold crossing in that sub-interval.
2. Apply the new load value at `pending_load_change.timestamp` (load-changes-before-physics,
   per your answer to Q9).
3. Continue integrating from `pending_load_change.timestamp` to `timestamp` using the **new**
   load, again checking for a crossing.

Cores with no pending change simply integrate straight through `[last_synced_ts, timestamp]`.

### 6.4 Deterministic ordering of the returned IDs

"Deterministic ordering by event order" is read as **chronological**: if core 3 transitions
at `t=105` and core 1 transitions at `t=110` within the same `tick()` call, core 3 is
returned before core 1, regardless of `core_id` ordering. Simultaneous transitions (same
exact instant) tie-break by ascending `core_id` for full determinism.

---

## 7. Open Questions / Parameters to Finalize Before Implementation

- Exact functional form and coefficients of `H(load)`.
- Numeric value of `passive_cooling_capacity` and `active_cooling_capacity_per_core`.
- Numeric restart hysteresis gap below the 80°C shutdown threshold.
- §2's units assumption (W vs °C/sec conflation) — confirm or correct against what you recall
  of the original Optiver equations.
- §3.3's slack-vs-strict-proportional interpretation of "proportional to need."
- §6.1's scope of "externally visible" — confirm event-only vs. also wanting a continuous
  state-query method.
