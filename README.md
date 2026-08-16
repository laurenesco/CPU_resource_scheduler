# Reinforcement Learning CPU Resource Scheduling Agent
A computational resource scheduler powered by reinforcement learning.

# Gymnasium Environment
The gymnasium environment is used to train and test the agent. This environment simulates multiple processor cores without physical sensors. Each core has a power load (in watts) and a temperature. Cooling combines:

- One **passive** cooling capacity, **shared** across the whole processor
- One **active** cooling capacity, assigned **per running core**

The environment exposes three operations:

1. **`__init__(...)`**
   Initialization with cooling capacities and a stable set of core IDs.

2. **`set_core_load(timestamp, core_id, watts)`**
   Schedules or applies a load change for a core. May restart a shut-down core (you decide, and justify, exactly when/how).

3. **`tick(timestamp)`**
   Advances the simulation to `timestamp` and returns the IDs of cores whose *externally visible* state changed since the previous tick (e.g. shut down, restarted — you decide what counts as "externally visible" and justify it). Cores can also attempt to restart during a tick.

> **Important:** Do not invent a specific heat equation or specific numeric thresholds (shutdown temp, restart temp, cooling coefficients, etc.). Treat those as open requirements you'd clarify with an interviewer first. Your job is to design the state model, time-advancement mechanics, and event ordering so that any reasonable heat equation / threshold values could be dropped in later.

## Thermodynamics

- At 80 degrees Celsius, a core automatically shuts off
- 20 degrees Celsius is the lowest temperature a core can reach
- When active, cores increase temperature at a rate of 2W/sec
- When idle, cores cool at a rate of 2W/sec

## Passive cooling

## Active Cooling


## Constraints & Assumptions

- Timestamps are monotonic but may have gaps of arbitrary length between calls (no fixed tick interval).
- Load changes are processed lazily through time advancement, there is no background thread ticking the simulation on its own.
- A core is either running or shut down; its temperature and requested load (watts) must be tracked at all times, including while shut down.
- The result of `tick()` must have deterministic ordering by event order
- All state changes that logically occur "at" one timestamp must be atomic from the caller's perspective. A caller should never observe a partially-applied timestamp.
