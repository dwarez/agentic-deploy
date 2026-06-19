#!/usr/bin/env bash
# Install the SageMaker deployment skills into a coding agent's skills directory.
#
# Claude Code and Codex: symlinks each skill into the agent dir, so `git pull`
# updates the installed skills automatically (pass --copy for standalone copies).
# Pi: always COPIES, because Pi's skill discovery ignores symlinks. Pi also does
# not resolve the <skill-path> placeholder the skills use to locate their bundled
# scripts (Claude Code and Codex do), so for Pi that placeholder is rewritten to
# each skill's real install path at copy time.
#
# Usage:
#   bash install.sh                 # install into every detected agent
#   bash install.sh --claude        # Claude Code only (~/.claude/skills)
#   bash install.sh --codex         # Codex only (~/.codex/skills)
#   bash install.sh --pi            # Pi only (~/.pi/agent/skills, copy + path substitution)
#   bash install.sh --copy          # copy instead of symlink (Claude/Codex; Pi always copies)
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
PI_DIR="$HOME/.pi/agent/skills"

# Marker file dropped inside each skill dir copied into Pi, so we can recognise
# (and safely refresh/remove) our own copies without clobbering unrelated skills.
PI_MARKER=".agentic-deploy-install"

# Option defaults
WANT_CLAUDE=false
WANT_CODEX=false
WANT_PI=false
EXPLICIT_TARGET=false
USE_COPY=false
UNINSTALL=false
FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --claude)    WANT_CLAUDE=true; EXPLICIT_TARGET=true ;;
        --codex)     WANT_CODEX=true;  EXPLICIT_TARGET=true ;;
        --pi)        WANT_PI=true;     EXPLICIT_TARGET=true ;;
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
    [[ "$WANT_PI"     == true ]] && TARGETS+=("$PI_DIR")
else
    # Auto-detect: act on any agent whose home dir exists.
    [[ -d "$HOME/.claude" ]] && TARGETS+=("$CLAUDE_DIR")
    [[ -d "$HOME/.codex"  ]] && TARGETS+=("$CODEX_DIR")
    [[ -d "$HOME/.pi"     ]] && TARGETS+=("$PI_DIR")
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

# Rewrite the <skill-path> placeholder to the skill's real install dir in every
# file that contains it. Pi (unlike Claude Code / Codex) does not substitute the
# placeholder at runtime, so the copied scripts would otherwise be unreachable.
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

# Pi install path: always copy (symlinks are ignored by Pi discovery) and bake
# the real absolute path into the placeholder. Ownership is tracked with a marker
# file so re-runs refresh our copies and --uninstall removes only what we made.
install_one_pi() {
    local skill="$1" target_dir="$2"
    local src="$SKILLS_SRC/$skill"
    local dst="$target_dir/$skill"
    local marker="$dst/$PI_MARKER"

    if [[ "$UNINSTALL" == true ]]; then
        if [[ -f "$marker" ]]; then
            rm -rf "$dst"; log "  removed       $skill"
        elif [[ -e "$dst" && "$FORCE" == true ]]; then
            rm -rf "$dst"; log "  removed       $skill (--force)"
        elif [[ -e "$dst" || -L "$dst" ]]; then
            log "  skip          $skill (not installed by this script; use --force)"
        fi
        return
    fi

    # Conflict: an existing entry that isn't one of our copies.
    if [[ ( -e "$dst" || -L "$dst" ) && ! -f "$marker" && "$FORCE" != true ]]; then
        log "  skip          $skill (exists at $dst, not ours; use --force)"
        return
    fi

    rm -rf "$dst"
    cp -R "$src" "$dst"
    substitute_skill_path "$dst"
    : > "$marker"
    log "  copied+patched $skill"
}

ACTION="Installing"; [[ "$UNINSTALL" == true ]] && ACTION="Uninstalling"
log "$ACTION ${#SKILLS[@]} skill(s) for ${#TARGETS[@]} target(s)"

for target_dir in "${TARGETS[@]}"; do
    [[ "$UNINSTALL" != true ]] && mkdir -p "$target_dir"
    [[ -d "$target_dir" ]] || { log "$target_dir: not present — skipping"; continue; }
    log "$target_dir:"
    for skill in "${SKILLS[@]}"; do
        if [[ "$target_dir" == "$PI_DIR" ]]; then
            install_one_pi "$skill" "$target_dir"
        else
            install_one "$skill" "$target_dir"
        fi
    done
done

log "Done."
