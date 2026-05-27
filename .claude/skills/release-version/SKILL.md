---
name: release-version
description: On release/* branches, bump SemVer in pyproject.toml, create an annotated tag, push branch + tags, and open a release PR to main. On any other branch, just open a regular PR to main via gh.
context: fork
argument-hint: [optional bump override: major|minor|patch]
user-invocable: true
model: Sonnet
---

# Release version

Dispatch based on the current branch:

- **On `release/*`** → full release automation: bump SemVer in `pyproject.toml`, create an annotated tag, push branch + tag, open a release PR to `main`.
- **On any other branch** → skip versioning entirely and just open a regular PR to `main` via `gh pr create`.

## Step 0: Branch detection

1. Read the current branch name (`git rev-parse --abbrev-ref HEAD`).
2. If it matches `release/*` → follow **Release flow** below.
3. Otherwise → follow **Regular PR flow** below.

---

## Release flow (branch matches `release/*`)

### SemVer source of truth

- Version lives in `pyproject.toml` under `[project].version` in the form `MAJOR.MINOR.PATCH`.
- Tags must be `vMAJOR.MINOR.PATCH` (annotated).

### Step 1: Determine bump level (SemVer)

Decide the bump level from the commits currently on the branch (since it diverged from `main`), unless `$ARGUMENTS` explicitly provides `major`, `minor`, or `patch`.

Heuristic (Conventional Commits-compatible):

- **major**: any commit subject contains `!` (e.g. `feat!:` / `fix!:`) OR commit body contains `BREAKING CHANGE`
- **minor**: otherwise, any `feat:` commit exists
- **patch**: otherwise, any `fix:` commit exists
- **patch**: otherwise (docs/chore/refactor/test/style only)

### Step 2: Compute next version

1. Read current version from `pyproject.toml`.
2. Compute `next_version` by applying the bump level:
   - major: `(MAJOR+1).0.0`
   - minor: `MAJOR.(MINOR+1).0`
   - patch: `MAJOR.MINOR.(PATCH+1)`

### Step 3: Bump version + tag (atomic)

1. Update `pyproject.toml` `[project].version` to `next_version`.
2. Commit the bump as its own commit:

```
chore(release): bump version to <next_version>
```

3. Create an annotated tag on that bump commit:
   - `git tag -a "v<next_version>" -m "v<next_version>"`

### Step 4: Push branch and tags

Push the current branch and ensure tags are pushed too:

- Preferred: `git push -u origin <branch> --follow-tags`
- If needed: `git push -u origin <branch>` then `git push origin --tags`

### Step 5: Create release PR to main

Create a PR to `main` using `gh pr create`:

- Title: `release v<next_version>`
- Body:

```
*📦 Atualização v<next_version>*

_O que tem de novo nessa versão:_ 👇

✅ *Novidades*
- <adicione itens que fazem diferença para o usuário e não coisas técnicas>

🐛 *Correções*
- <adicione correções de bugs, se houverem>

🔖 *Versão*
- <next_version> (tag `v<next_version>`)

 <Coloque alguma curiosidade ou quiz sobre algum município do Brasil, baseado em nossos dados sobre eles.> 👀
```

---

## Regular PR flow (branch does NOT match `release/*`)

Do **not** touch `pyproject.toml`, do **not** create tags, do **not** bump any version.

### Step 1: Verify branch state

1. Ensure the working tree is clean (`git status --porcelain`). If dirty, stop and tell the user to commit or stash first.
2. Ensure the branch has commits ahead of `main`. If not, stop.

### Step 2: Push the branch

- `git push -u origin <branch>` (only `--force` if the user explicitly asks, never force-push `main`).

### Step 3: Open the PR

Run the standard `gh pr create` flow to `main`:

1. Inspect commits on the branch since it diverged from `main` (`git log main..HEAD`) and the full diff (`git diff main...HEAD`).
2. Draft a short, accurate title (under 70 chars) summarizing the branch.
3. Draft a body with a **Summary** (1-3 bullets) and **Test plan** (bulleted checklist). Use a HEREDOC for the body.

```
gh pr create --title "<title>" --body "$(cat <<'EOF'
## Summary
- <bullet 1>
- <bullet 2>

## Test plan
- [ ] <step 1>
- [ ] <step 2>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

4. Return the PR URL so the user can open it.

---

## Important rules (both flows)

- **NEVER amend** existing commits — always create new ones
- **NEVER force push** — warn the user if this would be needed
- **NEVER skip hooks** (no `--no-verify`)
- **NEVER push to `main`** directly — always go through a PR
