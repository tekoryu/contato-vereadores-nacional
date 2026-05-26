#!/usr/bin/env bash
# Claude Code status line script
# Reads JSON from stdin and prints a formatted status line

input=$(cat)

# -- Model
model=$(echo "$input" | jq -r '.model.display_name // "Claude"')

# -- Current directory (basename)
cwd=$(echo "$input" | jq -r '.cwd // .workspace.current_dir // ""')
dir=$(basename "$cwd")

# -- Git repo (owner/name) when available
repo=$(echo "$input" | jq -r '.workspace.repo | if . then .owner + "/" + .name else "" end')

# -- Context usage
used_pct=$(echo "$input" | jq -r '.context_window.used_percentage // empty')

# -- Effort level
effort=$(echo "$input" | jq -r '.effort.level // empty')

# -- Rate limits (5-hour session)
five_hour=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')

# -- PR info
pr_num=$(echo "$input" | jq -r '.pr.number // empty')
pr_state=$(echo "$input" | jq -r '.pr.review_state // "open"')

# Build output parts
parts=()

# Model + effort
if [ -n "$effort" ]; then
    parts+=("$(printf '\033[36m%s\033[0m' "$model") [${effort}]")
else
    parts+=("$(printf '\033[36m%s\033[0m' "$model")")
fi

# Directory / repo
if [ -n "$repo" ]; then
    parts+=("$(printf '\033[33m%s\033[0m' "$repo"):$dir")
else
    parts+=("$(printf '\033[33m%s\033[0m' "$dir")")
fi

# Context usage
if [ -n "$used_pct" ]; then
    used_int=$(printf '%.0f' "$used_pct")
    if [ "$used_int" -ge 80 ]; then
        ctx_color='\033[31m'  # red
    elif [ "$used_int" -ge 50 ]; then
        ctx_color='\033[33m'  # yellow
    else
        ctx_color='\033[32m'  # green
    fi
    parts+=("$(printf "${ctx_color}ctx:%d%%\033[0m" "$used_int")")
fi

# 5-hour rate limit
if [ -n "$five_hour" ]; then
    five_int=$(printf '%.0f' "$five_hour")
    parts+=("$(printf '5h:%d%%' "$five_int")")
fi

# PR badge
if [ -n "$pr_num" ]; then
    parts+=("PR#${pr_num}(${pr_state})")
fi

# Join parts with separator
IFS='|'
printf '%s' "${parts[*]}"
