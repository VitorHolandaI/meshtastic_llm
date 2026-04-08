# Commit style

- Use conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`
- Keep messages short and direct — one line is preferred
- No "Co-Authored-By", no "Generated with Claude Code", no AI attribution of any kind

# Branch strategy

- `main` — stable, tested, always working
- Feature branches for every significant change: `feat/sqlite-sessions`, `fix/ack-timeout`, etc.
- Open a PR from the feature branch into `main` — never commit big changes directly to `main`
- Small fixes and docs can go directly to `main`

# Workflow

Before committing and pushing any significant change:

1. **Branch** — create a feature branch for the work (`git checkout -b feat/my-feature`)
2. **Test** — run the test suite and verify nothing is broken
   ```bash
   source ../.venv/bin/activate
   pytest tests/ -v
   ```
3. **Update `changes.md`** — log what changed and why
4. **Commit** — one logical change per commit, short message
5. **Push** — push the branch, open a PR into `main`
   - `git push origin <branch>` → GitHub (default, triggers CI)
   - `git push gitea <branch>` → local Gitea backup

CI runs automatically on every push via GitHub Actions (`.github/workflows/tests.yml`).
A green CI run is required before merging into `main`.
