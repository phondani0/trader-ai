# Nautilus-Only Pivot Design

## Goal

Remove the legacy trader-ai runtime entirely and make NautilusTrader the only supported system in this repository.

## Decision

The repository will no longer support the custom `src/` runtime, legacy `scripts/` entrypoints, or old benchmark/backtest artifacts.

NautilusTrader remains in the repository as the only execution, research, and reporting path.

## Scope

This pivot includes:

- removing legacy runtime code and tests
- removing legacy benchmark artifacts and approvals snapshots
- removing legacy strategy adapters that depended on old code
- making root CLI commands delegate only to Nautilus commands
- rewriting the root README and workflow around Nautilus only

This pivot does not include:

- building a full live-trading Nautilus runner in this step
- preserving backward compatibility for old commands or file layouts

## Architecture

The repository becomes a thin root launcher plus a single Nautilus application under `nautilus/`.

- `nautilus/` stays the only app with its own dependencies, tests, config, and outputs
- root-level CLI commands become lightweight wrappers that run `uv run ...` inside `nautilus/`
- old `src/`, old strategy engine, old `scripts/`, and old `tests/` are deleted

## Strategy Policy

Legacy trader-ai strategy adapters are removed.

After the pivot, only Nautilus-native strategies remain supported:

- `demo_ema_cross`
- `nautilus_ema_cross`
- `volume_spike_trend`

Any future strategy migration must be done as a native Nautilus strategy, not by importing deleted legacy code.

## Deployment Policy

The previous PM2 deployment path for `uv run start` is removed.

Until a true Nautilus live/paper loop exists, the repo will verify and bootstrap Nautilus artifacts, but it will not pretend the old live engine still exists.

## Risks

- Deleting the legacy runtime removes the existing strategy catalog immediately.
- Some earlier Nautilus comparison outputs that depended on legacy adapters will become historical only.
- Root-level users must accept the new wrapper-based CLI surface.

## Mitigation

- Keep Nautilus tests green during the cutover.
- Keep root wrappers explicit and minimal.
- Rewrite docs so there is no ambiguity about the product path.
