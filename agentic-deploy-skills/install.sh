#!/usr/bin/env bash
# Install the SageMaker deployment skills into a coding agent's skills directory.
#
# Every install is a standalone copy with the <skill-path> placeholder rewritten
# to each skill's real install path. The skills reference their bundled scripts
# via that placeholder; harnesses (Claude Code, Codex, Pi) announce the skill's
# base dir and rely on the model to resolve it — which weaker / self-hosted
# models (Codex via custom OpenAI providers, Claude Code via an Anthropic-compat
# proxy) don't always do. Baking the absolute path at install time makes the
# scripts reachable regardless of model capability. The repo source keeps the
# placeholder intact.
#
# `git pull` does NOT update installed skills — re-run this script after changing
# the source.
#
# Usage:
#   bash install.sh                 # install into every detected agent
#   bash install.sh --claude        # Claude Code only (~/.claude/skills)
#   bash install.sh --codex         # Codex only (~/.codex/skills)
#   bash install.sh --pi            # Pi only (~/.pi/agent/skills)
#   bash install.sh --uninstall     # remove skills this script installed
#   bash install.sh --force         # overwrite entries this script didn't create
#
# Idempotent: re-running refreshes our own copies. An entry we didn't create
# (no install marker) is left alone unless --force.

set -euo pipefail

log() { printf '[install-skills] %s\n' "$*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_SRC="$SCRIPT_DIR/sagemaker-skills"

CLAUDE_DIR="$HOME/.claude/skills"
CODEX_DIR="$HOME/.codex/skills"
PI_DIR="$HOME/.pi/agent/skills"

# Marker file dropped inside each installed skill dir, so we can recognise (and
# safely refresh / remove) our own copies without clobbering unrelated skills.
MARKER=".agentic-deploy-install"

# Option defaults
WANT_CLAUDE=false
WANT_CODEX=false
WANT_PI=false
EXPLICIT_TARGET=false
UNINSTALL=false
FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --claude)    WANT_CLAUDE=true; EXPLICIT_TARGET=true ;;
        --codex)     WANT_CODEX=true;  EXPLICIT_TARGET=true ;;
        --pi)        WANT_PI=true;     EXPLICIT_TARGET=true ;;
        --copy)      log "note: --copy is now the default (all installs are copies); ignoring." ;;
        --uninstall) UNINSTALL=true ;;
        --force)     FORCE=true ;;
        -h|--help)   grep '^#' "$0" | sed '1d; s/^# \{0,1\}//'; exit 0 ;;
        *)           log "Unknown option: $1 (try --help)"; exit 64 ;;
    esac
    shift
done

if [[ ! -d "$SKILLS_SRC" ]]; then
    log "ERROR: skills source not found at $SKILLS_SRC"
    exit 1
fi

# Resolve which agent dirs to act on.
TARGETS=()
if [[ "$EXPLICIT_TARGET" == true ]]; then
    [[ "$WANT_CLAUDE" == true ]] && TARGETS+=("$CLAUDE_DIR")
    [[ "$WANT_CODEX"  == true ]] && TARGETS+=("$CODEX_DIR")
    [[ "$WANT_PI"     == true ]] && TARGETS+=("$PI_DIR")
else
    # Auto-detect: act on any agent whose home dir exists.
    [[ -d "$HOME/.claude" ]] && TARGETS+=("$CLAUDE_DIR")
    [[ -d "$HOME/.codex"  ]] && TARGETS+=("$CODEX_DIR")
    [[ -d "$HOME/.pi"     ]] && TARGETS+=("$PI_DIR")
    if [[ ${#TARGETS[@]} -eq 0 ]]; then
        log "No agent detected (~/.claude, ~/.codex, ~/.pi). Defaulting to Claude Code."
        TARGETS+=("$CLAUDE_DIR")
    fi
fi

# Skill list, derived from the source tree (picks up new skills automatically).
SKILLS=()
for skill_md in "$SKILLS_SRC"/*/SKILL.md; do
    [[ -e "$skill_md" ]] || continue
    SKILLS+=("$(basename "$(dirname "$skill_md")")")
done
if [[ ${#SKILLS[@]} -eq 0 ]]; then
    log "ERROR: no skills (*/SKILL.md) found under $SKILLS_SRC"
    exit 1
fi

# Rewrite the <skill-path> placeholder to the skill's real install dir in every
# file that contains it, so the bundled scripts are reachable without relying on
# the model to resolve the placeholder against the harness-announced base dir.
# Uses a temp file instead of `sed -i` to stay portable across BSD and GNU sed.
substitute_skill_path() {
    local skill_dir="$1" f tmp
    # Newline-delimited file list: portable across BSD and GNU grep (BSD `-Z`
    # does not null-terminate with `-l`). Skill paths never contain newlines.
    while IFS= read -r f; do
        [[ -n "$f" ]] || continue
        tmp="$f.tmp.$$"
        if LC_ALL=C sed "s#<skill-path>#${skill_dir}#g" "$f" > "$tmp"; then
            mv "$tmp" "$f"
        else
            rm -f "$tmp"
        fi
    done < <(grep -rl -- '<skill-path>' "$skill_dir" 2>/dev/null || true)
}

# True if $dst is one of our installs: a copy carrying the marker file.
is_ours() {
    [[ -f "$1/$MARKER" ]]
}

install_one() {
    local skill="$1" target_dir="$2"
    local src="$SKILLS_SRC/$skill"
    local dst="$target_dir/$skill"

    if [[ "$UNINSTALL" == true ]]; then
        if is_ours "$dst"; then
            rm -rf "$dst"; log "  removed       $skill"
        elif [[ "$FORCE" == true && -e "$dst" ]]; then
            rm -rf "$dst"; log "  removed       $skill (--force)"
        elif [[ -e "$dst" ]]; then
            log "  skip          $skill (not installed by this script; use --force)"
        fi
        return
    fi

    # An entry we don't own is left alone unless --force.
    if [[ -e "$dst" ]] && ! is_ours "$dst" && [[ "$FORCE" != true ]]; then
        log "  skip          $skill (exists at $dst, not ours; use --force)"
        return
    fi

    rm -rf "$dst"                 # clear any stale copy before reinstalling
    cp -R "$src" "$dst"
    substitute_skill_path "$dst"
    : > "$dst/$MARKER"
    log "  installed     $skill"
}

ACTION="Installing"; [[ "$UNINSTALL" == true ]] && ACTION="Uninstalling"
log "$ACTION ${#SKILLS[@]} skill(s) for ${#TARGETS[@]} target(s)"

for target_dir in "${TARGETS[@]}"; do
    [[ "$UNINSTALL" != true ]] && mkdir -p "$target_dir"
    [[ -d "$target_dir" ]] || { log "$target_dir: not present — skipping"; continue; }
    log "$target_dir:"
    for skill in "${SKILLS[@]}"; do
        install_one "$skill" "$target_dir"
    done
done

log "Done."
