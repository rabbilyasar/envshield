# CLAUDE.md

The EnvShield charter is [AGENTS.md](AGENTS.md). It is imported in full below,
and it applies to Claude exactly as written. Where it says "Codex", read
"Claude".

Do not copy charter content into this file. An earlier, separate CLAUDE.md
drifted from AGENTS.md (`BL-090`). Principles, workflow and current-phase
changes go in AGENTS.md; this file holds only Claude-specific notes.

@AGENTS.md

## Claude-specific notes

- **Commit attribution.** §22 forbids `Co-authored-by:` trailers and any AI
  attribution in commit messages. This overrides Claude Code's default
  commit attribution.
- **Uncommitted work on another branch.** When the main checkout has
  uncommitted changes for a different issue, do the new issue's work in a
  `git worktree` on its own branch. Never stash, commit or carry over
  someone else's in-progress changes.
