---
name: github-project-init
description: Parses an implementation plan or conversation history to extract milestones, sprints, and issues, then creates them in the target GitHub repository using the gh CLI.
context:
argument-hint: [optional path to plan markdown file]
user-invocable: true
model: Sonnet
---

# GitHub Project Initialization

Automate the process of converting a project plan or conversation requirements into GitHub Milestones and Issues using the GitHub CLI (`gh`).

## Prerequisites

1. **GitHub CLI Auth**: The model must check `gh auth status` to ensure the user is authenticated.
2. **Git Repository**: The current working directory must be a Git repository with a remote repository configured on GitHub, or the user must specify a repository using `OWNER/REPO`.

## Procedure

### Step 1: Detect Repository and CLI Auth Status

1. Assume gh is installed.
2. Determine the target GitHub repository:
   - Check the git remotes: `git remote -v` or `gh repo view --json owner,name -q '.owner.login + "/" + .name'`.
   - If not in a git repository or no remote exists, ask the user to provide the target repository in the format `OWNER/REPO` or run git init/remote setup.

### Step 2: Retrieve the Source Plan

1. Read the plan from the source specified by the user or found automatically:
   - If `$ARGUMENTS` is provided, treat it as a path to a plan file (e.g., `plan.md`, `implementation_plan.md`, `task.md`).
   - If no arguments are provided, look for common plan files in the current workspace (e.g., `plan.md`, `implementation_plan.md`, `task.md`).
   - If no plan file is found, fallback to parsing the current conversation context (history) to extract the tasks, milestones, and requirements discussed.
   - If no plan can be found or extracted, ask the user to provide one.

### Step 3: Analyze and Organize the Plan

Analyze the plan and break it down into:
- **Milestones / Sprints**: Group related issues together. Each milestone represents a major release, MVP stage, or sprint. Give them clear names (e.g., `MVP Release`, `Sprint 1 - Foundations`, `v1.0.0-Beta`).
- **Issues**: Individual actionable tasks. For each issue, determine:
  - **Title**: A clean descriptive title (e.g., `feat: build auth middleware` or `refactor: user controller`).
  - **Body**: A clear markdown description containing:
    - Description of the task
    - Tasks checklist / acceptance criteria
    - Optional implementation hints/references
  - **Labels**: Applicable labels (e.g., `enhancement`, `bug`, `documentation`, `chore`).
  - **Milestone Assignment**: The milestone it belongs to.

### Step 4: Present Draft for User Approval

Before running any mutating GitHub CLI commands, print a structured preview of the planned milestones and issues to the user.

Format the preview as follows:

```markdown
### Proposed Milestones:
1. **[Milestone Title]** - [Description] (due: [Due Date])

### Proposed Issues:
- **[Issue Title]** -> Milestone: `[Milestone Title]`, Labels: `[labels]`
  ```markdown
  [Brief body preview]
  ```
```

Wait for explicit user confirmation before proceeding to creation.

### Step 5: Execute GitHub API & CLI Commands

Once approved, perform the following actions:

#### 1. Create Milestones

For each milestone:
Check if the milestone already exists in the target repository using the GitHub API:
```bash
gh api repos/:owner/:repo/milestones --jq '.[] | select(.title == "Milestone Title") | .number'
```

If it does not exist, create it:
```bash
gh api \
  --method POST \
  -H "Accept: application/vnd.github+json" \
  /repos/:owner/:repo/milestones \
  -f title='Milestone Title' \
  -f description='Milestone Description'
```
Extract and save the milestone number from the output (or the existing milestone number).

#### 2. Create Issues

For each issue, create it using `gh issue create`. Assign the milestone and labels:
```bash
gh issue create \
  --title "Issue Title" \
  --body "Issue body content" \
  --milestone "Milestone Title" \
  --label "label1,label2"
```

*Note:* If a label does not exist, GitHub will automatically create it or prompt depending on permissions. Try to stick to standard GitHub labels (`enhancement`, `bug`, `documentation`, `duplicate`, `help wanted`, `good first issue`, `invalid`, `question`, `wontfix`).

### Step 6: Print Execution Summary

Print a final report to the user summarizing all actions taken, listing:
- Milestones created (or matched) with links to their GitHub pages.
- Issues created with their numbers and direct URLs.
