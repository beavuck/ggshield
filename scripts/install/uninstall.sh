#!/usr/bin/env bash
#
# ggshield uninstaller (Linux / macOS).
#
# By default removes ONLY the standalone install this script created
# (the ~/.local/bin symlink and ~/.local/share/ggshield-standalone tree).
# It does not touch ggshield installed another way (Homebrew, apt/rpm, pipx,
# uv, pip…). Pass --purge to also remove ggshield's config, cache and data.
#
#   curl --proto '=https' --tlsv1.2 -sSfL \
#     https://raw.githubusercontent.com/GitGuardian/ggshield/main/scripts/install/uninstall.sh | bash

set -euo pipefail

BIN_DIR="${GGSHIELD_BIN_DIR:-$HOME/.local/bin}"
OPT_DIR="${GGSHIELD_OPT_DIR:-$HOME/.local/share/ggshield-standalone}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/ggshield-install"

ASSUME_YES=0
PURGE=0

usage() {
    cat <<EOF
Usage: uninstall.sh [OPTIONS]

Remove the standalone ggshield installed by install.sh. By default this only
removes the script-owned install; it leaves configuration, caches and any
ggshield installed another way untouched.

Options:
  -y, --yes     never prompt
      --purge   also remove config, cache and data
                (~/.gitguardian.yaml, ~/.ggshield, plugins)
  -h, --help    show this help
EOF
}

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

confirm() {
    [ "$ASSUME_YES" = 1 ] && return 0
    local reply
    if [ -t 0 ]; then
        read -r -p "$1 [y/N] " reply
    elif [ -e /dev/tty ]; then
        read -r -p "$1 [y/N] " reply </dev/tty
    else
        die "cannot prompt for '$1' (no TTY). Re-run with -y"
    fi
    case "$reply" in
    y* | Y*) return 0 ;;
    *) return 1 ;;
    esac
}

# Never 'rm -rf' a misconfigured GGSHIELD_OPT_DIR (empty, /, $HOME, the bin dir).
assert_safe_optdir() {
    local p="${OPT_DIR%/}"
    case "$p" in
    "" | "/" | "$HOME" | "${HOME%/}" | "$BIN_DIR" | "${BIN_DIR%/}")
        die "refusing to remove unsafe GGSHIELD_OPT_DIR='$OPT_DIR'; point it at a dedicated directory" ;;
    esac
}

# Remove the standalone install: the bin symlink (only when it points into our
# own dir — uv/pipx also drop a ggshield shim in ~/.local/bin) and OPT_DIR.
remove_standalone() {
    assert_safe_optdir
    local owns_symlink=0 found=0
    if [ -L "$BIN_DIR/ggshield" ]; then
        case "$(readlink "$BIN_DIR/ggshield")" in
        "$OPT_DIR"/*) owns_symlink=1; found=1 ;;
        esac
    fi
    [ -d "$OPT_DIR" ] && found=1

    if [ "$found" = 0 ]; then
        warn "no script-managed standalone install found in $OPT_DIR"
        warn "ggshield installed another way (brew, apt/rpm, pipx, uv, pip) is left untouched"
        return 0
    fi

    confirm "Remove the standalone ggshield ($OPT_DIR and its $BIN_DIR symlink)?" || return 0
    if [ "$owns_symlink" = 1 ]; then
        say "Removing $BIN_DIR/ggshield"
        rm -f "$BIN_DIR/ggshield"
    fi
    say "Removing $OPT_DIR"
    rm -rf "$OPT_DIR"
    rm -rf "$STATE_DIR"
}

purge_user_data() {
    # platformdirs(appname="ggshield") config/cache/data, the global config
    # file, and the scan databases (~/.ggshield)
    local paths=("$HOME/.gitguardian.yaml" "$HOME/.ggshield")
    if [ "$(uname -s)" = Darwin ]; then
        paths+=("$HOME/Library/Application Support/ggshield" "$HOME/Library/Caches/ggshield")
    else
        paths+=(
            "${XDG_CONFIG_HOME:-$HOME/.config}/ggshield"
            "${XDG_CACHE_HOME:-$HOME/.cache}/ggshield"
            "${XDG_DATA_HOME:-$HOME/.local/share}/ggshield"
        )
    fi
    local p found=0
    for p in "${paths[@]}"; do [ -e "$p" ] && found=1; done
    if [ "$found" = 0 ]; then
        say "No ggshield config/cache/data found"
        return 0
    fi
    confirm "Remove ggshield configuration, cache and data (including plugins)?" || return 0
    for p in "${paths[@]}"; do
        [ -e "$p" ] && { say "Removing $p"; rm -rf "$p"; }
    done
    # The loop's last test ([ -e ] on an absent path) leaves $?=1; under set -e
    # that would fail the caller's `[ "$PURGE" = 1 ] && purge_user_data`.
    return 0
}

main() {
    while [ $# -gt 0 ]; do
        case "$1" in
        -y | --yes) ASSUME_YES=1 ;;
        --purge) PURGE=1 ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
        esac
        shift
    done

    remove_standalone
    [ "$PURGE" = 1 ] && purge_user_data

    hash -r 2>/dev/null || true
    say "Done."
}

main "$@"
