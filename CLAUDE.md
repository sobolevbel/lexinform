@AGENTS.md

## Claude Code

- The area rules in `.claude/rules/` load by themselves when you read or edit a matching file; if a
  change reaches an area whose files you have not opened, read its rules file first.
- Slash commands for recurring jobs are in `.claude/commands/` (`/last-run`, `/dry-run`, `/bill`,
  `/bug-hunt`, `/product-review`, `/migration`, `/relay`, …).
