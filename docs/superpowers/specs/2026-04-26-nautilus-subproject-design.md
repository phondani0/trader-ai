# Nautilus Subproject Design

## Goal

Add a NautilusTrader-based trading app inside this repository without replacing the current bot. The Nautilus app must be isolated enough that it can be developed, tested, and removed independently, while still benefiting from the same GitHub repository and workflow surface.

## Scope

This design covers only the first NautilusTrader spike:

- create an isolated in-repo subproject
- install and use NautilusTrader there
- support a minimal backtest runner first
- prepare a paper-trading runner path
- keep the current bot untouched

This design does not cover:

- replacing the existing `src/` runtime
- sharing the current custom backtest/runtime code
- migrating all current strategies
- deploying live capital immediately

## Decision

Use a root-level `nautilus/` subproject inside the existing repository.

We will not mix Nautilus code into the current root Python app. We will not create a separate repository yet. We will not share runtime code between the two systems during the spike.

## Why This Structure

This gives the best tradeoff for the current stage:

- same GitHub repo and existing workflow surface
- isolated dependency boundary
- clean comparison with the existing bot
- low-risk rollback if the spike fails
- easier future promotion if NautilusTrader works well

Keeping Nautilus inside the root project dependency graph would create unnecessary coupling. Creating a separate repository now would improve isolation but add operational overhead before the framework is validated.

## Directory Layout

```text
nautilus/
  pyproject.toml
  README.md
  config/
    settings.yaml
    instruments.yaml
  src/
    nautilus_app/
      __init__.py
      strategies/
      adapters/
      runners/
  scripts/
    backtest.py
    papertrade.py
  tests/
```

## Responsibilities

### `nautilus/pyproject.toml`

Defines the NautilusTrader subproject dependencies and commands. This subproject owns its own environment and must not rely on the root app dependency graph.

### `nautilus/config/`

Stores Nautilus-specific configuration for:

- general settings
- instruments and venue definitions
- strategy-specific runtime values

This config can borrow ideas and parameter values from the current bot, but it should not attempt to directly reuse the current config tree in the first spike.

### `nautilus/src/nautilus_app/strategies/`

Contains Nautilus-native strategies only. These are ports or simplified equivalents of current ideas, not wrappers around the current bot strategies.

### `nautilus/src/nautilus_app/adapters/`

Contains small helpers that translate simple YAML config into Nautilus-friendly objects and strategy parameters.

### `nautilus/src/nautilus_app/runners/`

Contains the assembly code for running backtests and paper-trading sessions.

### `nautilus/scripts/backtest.py`

First runnable milestone. It must prove that the subproject can load config, construct a minimal Nautilus backtest, and execute one strategy end-to-end.

### `nautilus/scripts/papertrade.py`

Second milestone. It comes only after the backtest path is stable and understandable.

### `nautilus/tests/`

Contains focused tests for:

- config loading
- runner wiring
- strategy smoke behavior

The initial goal is confidence in the structure, not full parity with the current repo.

## Isolation Rules

The Nautilus subproject must follow these rules:

1. It does not import the current bot runtime/backtest engine.
2. It does not reuse the current `src/` execution stack.
3. It may reuse high-level ideas, thresholds, and strategy concepts.
4. It may later consume shared data or configs, but not in the first spike.

This keeps the experiment honest. If NautilusTrader works, it should work because the framework is strong, not because we quietly dragged the current architecture into it.

## First Milestone

The first milestone is intentionally narrow:

- install NautilusTrader in `nautilus/`
- create the subproject structure
- run one minimal backtest
- use one simple strategy first

The first strategy should be simple enough to prove the plumbing. It does not need to be the final best strategy.

## Strategy Migration Approach

Do not port everything at once.

Recommended order:

1. one simple trend or momentum strategy
2. one paper-trading runner
3. compare Nautilus behavior against the current repo
4. only then port more complex strategy families

This avoids rebuilding the current complexity too early.

## Runtime Flow

### Backtest

`nautilus/scripts/backtest.py` should:

1. load config
2. construct instruments/venue inputs
3. construct a minimal strategy
4. run a NautilusTrader backtest
5. surface basic metrics or results

### Paper Trading

`nautilus/scripts/papertrade.py` should later:

1. load a separate paper/runtime config
2. construct the strategy runner
3. connect through NautilusTrader’s paper/live path
4. operate independently from the current bot runtime

## Comparison Strategy

The existing bot remains the baseline reference while the Nautilus subproject is built.

The comparison questions later will be:

- does Nautilus backtest more cleanly?
- is the setup easier to reason about?
- is paper/live flow cleaner?
- does strategy iteration become faster and more reliable?

Only after those answers are positive should replacement be considered.

## Risks

### Dependency Complexity

NautilusTrader may have environment and installation requirements that are heavier than the current custom stack. Isolating it inside `nautilus/` contains that risk.

### Premature Full Migration

If we try to port the full strategy set immediately, we will recreate the same complexity under a new framework. The spike must stay narrow.

### False Parity Expectations

The first Nautilus implementation will not match every behavior of the current bot. That is acceptable. The goal is to validate framework fit first.

## Testing Strategy

The first spike should include:

- unit tests for config parsing and runner setup
- one smoke test for the backtest command
- manual execution of the backtest runner with a single strategy

No live deployment should happen before backtest and paper-trading paths are both understood.

## Migration Boundary

The current bot is not replaced during this phase.

The Nautilus subproject is an isolated trial living in the same repo. If it proves better, the old code can later be retired gradually. If it does not, the subproject can be removed with minimal damage to the current bot.

