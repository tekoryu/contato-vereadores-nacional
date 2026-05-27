---
name: atomic-commit
description: Stage Git changes as isolated atomic conventional commits and optionally open a PR to main. Use when the user wants to commit their work.
context:
argument-hint: [optional commit message override]
user-invocable: true
model: Sonnet
---

# Atomic commit

Stage and record changes following the project's conventional commit style with isolated, atomic commits. Each logical change gets its own commit — never bundle unrelated changes together.

## Commit message format

```
<type>: <short imperative description>
```

**Types:** `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `style`

- Keep the description lowercase, imperative, concise (under 72 chars total)
- No period at the end
- Examples from this repo:
  - `feat: add TSE 2025 cod_mun_tse_ibge pipeline notebooks`
  - `fix: complete EDA notebook for perfil_eleitorado dataset`
  - `chore: remove deprecated election and geography notebooks`
  - `docs: add data processing todo tracking`
  - `refactor: optimized existing functionalities from gold`

## Procedure

### Step 1: Stage all files and analyze changes

1. Run `git add -A` to stage everything.
2. Run `git status` to see all staged changes.
3. Run `git diff --cached --stat` to understand the scope of changes.
4. Run `git diff --cached` to read the actual diffs.

### Step 2: Group changes into isolated atomic commits

Analyze the staged changes and group them by **logical unit of work**. Each group becomes one commit. Grouping rules:

- **One notebook pipeline = one commit** (e.g., all 3 notebooks for a new dataset together)
- **One bug fix = one commit** (even if it touches multiple files)
- **One config/doc change = one commit**
- **Unrelated file changes = separate commits** (NEVER mix a feat with a docs change)
- **New files vs modified files** on different topics = separate commits

For each group, determine the appropriate `type` prefix.

### Step 3: Commit each group in isolation

For each logical group:

1. `git reset HEAD` to unstage everything.
2. `git add <specific files>` for only the files in this group.
3. Commit with the conventional message format.
4. Use a HEREDOC for the commit message:

```bash
git commit -m "$(cat <<'EOF'
<type>: <description>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

If `$ARGUMENTS` contains `--execute`:
- **Do not ask for confirmation.** Commit each group immediately without presenting a plan or waiting for user input.
- After all commits are done, print a summary of what was committed.

Otherwise:
- Present each planned commit to the user **before executing**, showing the commit message and files included.
- Wait for user confirmation before proceeding.

### Step 4: Offer to create a Pull Request

After all commits are done:

1. Show a summary of all commits made.
2. Ask the user if they want to create a PR to `main`.
3. If yes:
   - Push the current branch to origin with `git push -u origin <branch>`
   - Create PR using `gh pr create` with:
     - Title: brief summary of the branch's purpose
     - Body: list of all commits made, with a summary section
4. If the current branch matches `release/*`, suggest using **`release-version`** (skill folder `.claude/skills/release-version/`) to bump version, tag, push tags, and open the PR.

PR body format:
```
## Summary
<1-3 bullet points describing the overall changes>

## Commits
<list each commit message>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## Important rules

- **NEVER bundle unrelated changes** into a single commit
- **NEVER amend** existing commits — always create new ones
- **NEVER force push** — warn the user if this would be needed
- **NEVER skip hooks** (no `--no-verify`)
- If `$ARGUMENTS` is provided, use it as a hint for the commit message but still follow the grouping rules

