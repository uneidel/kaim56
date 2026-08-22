# Skills in this repo

Claude Code reads the folders here as project-wide skills — so they take effect
automatically when work happens at a matching place in the repo.

Both are **third-party code (MIT)**, checked in here instead of referenced, so
that the state is reproducible:

| Skill | Origin | For what |
|---|---|---|
| `owasp-security` | [agamm/claude-code-owasp](https://github.com/agamm/claude-code-owasp) | OWASP Top 10:2025, ASVS 5.0, LLM Top 10, agentic AI security. Pure guardrails when writing and reviewing code, plus references for 20+ languages. |
| `security-reviewer` | [Security-Phoenix-demo](https://github.com/Security-Phoenix-demo/security-skills-claude-code) | Review playbooks, language packs and checklists (OWASP↔ASVS, endpoints). |
| `security-assessment` | ditto | Whole project against OWASP Top 10 2025 / ASVS L1. |
| `0day-scanner` | ditto | Check a single commit/PR for vulnerabilities. |
| `threat-modeling` | ditto | STRIDE/DREAD threat model from code and data flows. |
| `opengrep-rule-generator` | ditto | Generate SAST rules (opengrep/semgrep) — needs the binary locally. |

## What was adjusted during integration

- **Frontmatter added** to `0day-scanner`, `security-assessment` and
  `threat-modeling`: these three came without `name`/`description`, so Claude
  Code wouldn't load them at all.
- **Paths straightened**: their fallback references pointed to the nested
  folder structure of the origin repo
  (`../Security-automated-claude-skills/.claude/skills/security-reviewer/…`).
  Here all skills lie flat next to each other, so the references now point to
  `../security-reviewer/…`.

## Deliberately NOT adopted

- `notebooklm` and `global-research-notebook-lm` — they send content to Google
  NotebookLM and need an account plus sign-in for it.
- `cti-search-skill` — ships an `install.sh` and searches 300+ external
  domains; useful as a research tool, but not something that needs to run
  permanently in a repo.
- `secure-prd-skill`, `project Documentaion skill` — product documentation, not a
  security topic.

Let me know if one of them should come in after all.

## Two things worth knowing

1. **Two of the skills require MCP servers** (`run_security_assessment`,
   `analyze_for_zero_day_vulnerabilities`) that are not installed here.
   Both have a fallback to the bundled checklists — so they
   work, but then operate read-only instead of with the agent.
2. Skills are **instructions that Claude follows**. Before an update from
   upstream, the same look as with the first integration pays off: what changes about
   what the model is supposed to do.

## Addendum: claude-agentic-framework

From [dralgorhythm/claude-agentic-framework](https://github.com/dralgorhythm/claude-agentic-framework)
**17 skills** (architect, builder, qa-engineer, security, security-auditor,
code-check, swarm-plan/-execute/-research/-review, ui-ux-designer, tailor, …)
and the five **worker agents** under `.claude/agents/` were adopted.

**Deliberately NOT adopted: `hooks/` and `.claude/settings.json`.** That is the
difference from the security skills: skills are instructions, hooks are code
that runs automatically on every tool event, and `settings.json` brings an
extensive bash allowlist that changes what gets executed without a prompt.
Both would silently switch this repo's working mode.

Concretely, `pre-push-main-blocker.sh` would have broken our existing workflow:
it forbids direct pushes to `main` — exactly what has been done here the whole
time. `branch-pr-discipline.sh` only warns (doesn't block), so it would have
been harmless.

If you want the branch/PR workflow, I'll bring the hooks and `settings.json`
in — but then as a deliberate switch, including moving to feature branches
and pull requests in Gitea.
