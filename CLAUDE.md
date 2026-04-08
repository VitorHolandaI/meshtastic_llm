# Commit style

- Use conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`
- Keep messages short and direct — one line is preferred
- No "Co-Authored-By", no "Generated with Claude Code", no AI attribution of any kind

# Workflow

Before committing and pushing any significant change:

1. **Test** — run the gateway and verify the changed behaviour works end-to-end
2. **Update `changes.md`** — log what changed and why
3. **Commit** — one logical change per commit, short message
4. **Push** — only after the above are done
   - `git push` → GitHub (origin, public)
   - `git push gitea main` → local Gitea backup (`10.66.66.11`)

This keeps the remote history clean and every commit on `main` represents a known-working state.
