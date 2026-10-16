# pytauargus: Agent Rules

## Branching

`master` is the only long-lived branch. Never commit directly to
`master`: work on a short-lived feature branch (`<type>/<short-topic>`),
open a PR, merge it, delete the branch. Release-train commits (version
bumps, the engine pin bump, `chore(release)`) use the same flow.

## Commits

Conventional Commits: lowercase type, optional scope, imperative
subject. No `Co-authored-by` trailer. Never stage build artifacts
(`.venv/`, `build/`, `dist/`, `*.so`, `*.dylib`).

## Engine pin

The `tauargus-engine` submodule gitlink is the pin. Bump it only to
engine commits that are already pushed. Local dev reads the sibling
checkout; `TAUARGUS_ENGINE_DIR` overrides its location.

## More rules

Build commands, verification, the release procedure and the
documentation style live in the tauargus-orchestration repo: `AGENTS.md`
and the `release` skill. TUI docs live in `docs/tui.md` and
`docs/ui-design.md`.
