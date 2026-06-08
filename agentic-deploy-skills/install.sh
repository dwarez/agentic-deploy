#!/usr/bin/env bash
# Install the SageMaker deployment skills into a coding agent's skills directory.
#
# By default, symlinks each skill into the detected agent dirs (Claude Code and
# Codex). Symlinks mean `git pull` updates the installed skills automatically.
# Pass --copy for standalone copies instead (no link back to this repo).
#
# Usage:
#   bash install.sh                 # symlink into every detected agent
#   bash install.sh --claude        # Claude Code only (~/.claude/skills)
#   bash install.sh --codex         # Codex only (~/.codex/skills)
#   bash install.sh --copy          # copy instead of symlink
#   bash install.sh --uninstall     # remove skills this script installed
#   bash install.sh --force         # overwrite unrelated entries at the target
#
# Idempotent: an already-correct install is left alone. A conflicting entry
# (a real dir, or a symlink pointing elsewhere) is skipped unless --force.

set -euo pipefail

log() { printf '[install-skills] %s\n' "$*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_SRC="$SCRIPT_DIR/sagemaker-skills"

CLAUDE_DIR="$HOME/.claude/skills"
CODEX_DIR="$HOME/.codex/skills"

# Option defaults
WANT_CLAUDE=false
WANT_CODEX=false
EXPLICIT_TARGET=false
USE_COPY=false
UNINSTALL=false
FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --claude)    WANT_CLAUDE=true; EXPLICIT_TARGET=true ;;
        --codex)     WANT_CODEX=true;  EXPLICIT_TARGET=true ;;
        --copy)      USE_COPY=true ;;
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
else
    # Auto-detect: act on any agent whose home dir exists.
    [[ -d "$HOME/.claude" ]] && TARGETS+=("$CLAUDE_DIR")
    [[ -d "$HOME/.codex"  ]] && TARGETS+=("$CODEX_DIR")
    if [[ ${#TARGETS[@]} -eq 0 ]]; then
        log "No agent detected (~/.claude or ~/.codex). Defaulting to Claude Code."
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

install_one() {
    local skill="$1" target_dir="$2"
    local src="$SKILLS_SRC/$skill"
    local dst="$target_dir/$skill"

    if [[ "$UNINSTALL" == true ]]; then
        # Only remove what we own: a symlink into this repo, or (with --force) any entry.
        if [[ -L "$dst" && "$(readlink "$dst")" == "$src" ]]; then
            rm "$dst"; log "  removed link  $dst"
        elif [[ -e "$dst" && "$FORCE" == true ]]; then
            rm -rf "$dst"; log "  removed       $dst (--force)"
        elif [[ -e "$dst" || -L "$dst" ]]; then
            log "  skip          $dst (not a link into this repo; use --force)"
        fi
        return
    fi

    # Already correctly installed?
    if [[ -L "$dst" && "$(readlink "$dst")" == "$src" ]]; then
        log "  ok            $skill (already linked)"
        return
    fi
    # Conflict at the destination.
    if [[ -e "$dst" || -L "$dst" ]]; then
        if [[ "$FORCE" != true ]]; then
            log "  skip          $skill (exists at $dst; use --force to overwrite)"
            return
        fi
        rm -rf "$dst"
    fi

    if [[ "$USE_COPY" == true ]]; then
        cp -R "$src" "$dst"; log "  copied        $skill"
    else
        ln -s "$src" "$dst"; log "  linked        $skill"
    fi
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
