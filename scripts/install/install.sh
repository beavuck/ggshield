#!/usr/bin/env bash
#
# ggshield installer (Linux / macOS).
#
# Installs the standalone ggshield build into ~/.local/bin (no sudo), then
# best-effort authenticates and installs any requested plugins.
#
#   curl -sSfL \
#     https://raw.githubusercontent.com/GitGuardian/ggshield/main/scripts/install/install.sh | bash
#
# See scripts/install/README.md for options and other install methods.
# Cleanup: uninstall.sh.

set -euo pipefail

# Every install path (and every PATH hint) is derived from $HOME; fail early
# with a clear message rather than building paths like /.local/bin under set -u.
# die() is not defined yet, so emit raw.
[ -n "${HOME:-}" ] || { printf '\033[1;31merror:\033[0m HOME is not set\n' >&2; exit 1; }

GITHUB_REPO="GitGuardian/ggshield"
DEFAULT_INSTANCE="https://dashboard.gitguardian.com"
EU_INSTANCE="https://dashboard.eu1.gitguardian.com"

BIN_DIR="${GGSHIELD_BIN_DIR:-$HOME/.local/bin}"
# NOT ~/.local/share/ggshield: that is ggshield's own data dir (plugins…)
OPT_DIR="${GGSHIELD_OPT_DIR:-$HOME/.local/share/ggshield-standalone}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/ggshield-install"
STATE_FILE="$STATE_DIR/state"

ASSUME_YES=0
INSTALL_ONLY=0
# Set when BIN_DIR is not on PATH, so the final summary can tell the user how to
# expose it (see emit_path_hint).
PATH_NEEDS_SETUP=0
# Set to the path of an older ggshield that resolves ahead of the fresh one
# while BIN_DIR *is* on PATH; emit_path_hint then says "put BIN_DIR first"
# rather than "add it".
SHADOWED_BY=""
PLUGINS=()
# honor GITGUARDIAN_INSTANCE like ggshield does; --instance overrides it
INSTANCE="${GITGUARDIAN_INSTANCE:-}"
VERSION="${GGSHIELD_VERSION:-}"

usage() {
    cat <<EOF
Usage: install.sh [OPTIONS]

Install the standalone ggshield build, then authenticate and (optionally)
install plugins. Other install methods (Homebrew, apt/rpm, pipx) are listed
in scripts/install/README.md.

Options:
  -y, --yes           never prompt, accept defaults (for CI)
      --instance URL  GitGuardian instance to authenticate against
                      (default: $DEFAULT_INSTANCE)
      --version X.Y.Z ggshield version to install (default: latest;
                      also via GGSHIELD_VERSION env var)
      --install-only  install ggshield only, skip auth and plugins
      --plugin NAME   install this ggshield plugin (repeatable)
  -h, --help          show this help

Environment:
  GITGUARDIAN_API_KEY  authenticate with this API key instead of the browser
                       flow (token login; combine with --instance)
  GGSHIELD_BIN_DIR     symlink dir (default ~/.local/bin)
  GGSHIELD_OPT_DIR     extraction dir (default ~/.local/share/ggshield-standalone)
  GGSHIELD_REQUIRE_ATTESTATION
                       set to 1 (or true/yes/on) to require gh build-provenance
                       verification on top of the sha256 check; fails the install
                       if gh is missing, older than 2.56.0, unauthenticated, or
                       cannot verify the artifact (default: off)
EOF
}

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

fetch() {
    curl --proto '=https' --tlsv1.2 -sSfL --retry 3 "$@"
}

need() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

# A remote/headless session has no browser for the OAuth redirect; ggshield's
# --method oob is the way out (reported by users behind SSH / VM port-forwarding).
is_headless() {
    [ -n "${SSH_CONNECTION:-}${SSH_TTY:-}" ] && return 0
    [ "$OS" = linux ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && return 0
    return 1
}

detect_platform() {
    case "$(uname -s)" in
    Linux) OS=linux ;;
    Darwin) OS=darwin ;;
    MINGW* | MSYS* | CYGWIN*)
        die "Windows is not supported by this script. Use install.ps1 instead: \
irm https://raw.githubusercontent.com/$GITHUB_REPO/main/scripts/install/install.ps1 | iex"
        ;;
    *) die "unsupported OS: $(uname -s)" ;;
    esac

    case "$(uname -m)" in
    x86_64 | amd64) ARCH=x86_64 ;;
    arm64 | aarch64) ARCH=arm64 ;;
    *) die "unsupported architecture: $(uname -m)" ;;
    esac

    if [ "$OS" = darwin ]; then
        LIBC=""
        # uname -m lies under Rosetta 2; sysctl does not (rustup pattern)
        if [ "$ARCH" = x86_64 ] &&
            [ "$(sysctl -n hw.optional.arm64 2>/dev/null || true)" = 1 ]; then
            ARCH=arm64
        fi
        TARGET="$ARCH-apple-darwin"
    else
        LIBC=gnu
        # base Alpine has no ldd (musl-utils), so check the loader file too
        if [ -e "/lib/ld-musl-$(uname -m).so.1" ] ||
            ldd --version 2>&1 | grep -qi musl; then
            LIBC=musl
        fi
        # ggshield's Linux target triple uses the kernel arch name (aarch64);
        # macOS keeps arm64.
        local linux_arch="$ARCH"
        [ "$ARCH" = arm64 ] && linux_arch=aarch64
        TARGET="$linux_arch-unknown-linux-gnu"
    fi

    # The standalone build is glibc-only; there is no musl tarball.
    if [ "$LIBC" = musl ]; then
        die "the standalone build is glibc-only; Alpine/musl is not supported here.
Use the Docker image (gitguardian/ggshield) or 'pipx install ggshield'.
See https://docs.gitguardian.com/ggshield-docs/getting-started"
    fi
}

resolve_version() {
    if [ -n "$VERSION" ]; then
        VERSION="${VERSION#v}"
        return 0
    fi
    say "Resolving latest ggshield version"
    VERSION=$(fetch "https://api.github.com/repos/$GITHUB_REPO/releases/latest" |
        grep -o '"tag_name": *"v[^"]*"' | head -1 | grep -o '[0-9][^"]*') || true
    [ -n "$VERSION" ] || die "could not resolve the latest version from the GitHub API"
}

# GitHub computes a sha256 digest for every release asset; pair each "name"
# with the "digest" that follows it in the release JSON.
asset_digest() {
    local asset="$1"
    fetch "https://api.github.com/repos/$GITHUB_REPO/releases/tags/v$VERSION" |
        grep -o '"name": *"[^"]*"\|"digest": *"sha256:[0-9a-f]*"' |
        grep -A1 -F "\"$asset\"" | grep -o 'sha256:[0-9a-f]*' | head -1 || true
}

# HTTP status of an asset's download URL (HEAD, following redirects). Tells
# "asset absent" (404) from "couldn't reach GitHub" (504/000/…). Deliberately
# not the `fetch` wrapper: no -f, so 4xx still yields the code.
asset_http_status() {
    curl --proto '=https' --tlsv1.2 -sIL -o /dev/null -w '%{http_code}' \
        --max-time 20 --retry 2 \
        "https://github.com/$GITHUB_REPO/releases/download/v$VERSION/$1" \
        2>/dev/null || echo 000
}

# sha256 is mandatory and fails closed; gh provenance is opt-in (verify_attestation).
verify_download() {
    local file="$1" asset="$2" digest sum_tool
    digest=$(asset_digest "$asset")
    [ -n "$digest" ] ||
        die "could not retrieve the expected sha256 digest for $asset from the GitHub API; refusing to install unverified"

    if command -v sha256sum >/dev/null 2>&1; then
        sum_tool="sha256sum"
    elif command -v shasum >/dev/null 2>&1; then
        sum_tool="shasum -a 256"
    else
        die "cannot verify $asset: no sha256 tool found (need sha256sum or shasum)"
    fi
    say "Verifying sha256 checksum"
    echo "${digest#sha256:}  $file" | $sum_tool -c - >/dev/null ||
        die "checksum mismatch for $asset"

    verify_attestation "$file" "$asset"
}

# 1/true/yes/on (any case, surrounding whitespace ignored) enable it; unset/empty/
# 0/false/no/off disable; warn on anything else and treat as off, so a typo'd
# opt-in is never silently ignored.
require_attestation() {
    local v="${GGSHIELD_REQUIRE_ATTESTATION:-}"
    v="${v#"${v%%[![:space:]]*}"}" # strip leading whitespace
    v="${v%"${v##*[![:space:]]}"}" # strip trailing whitespace
    case "$(printf '%s' "$v" | tr '[:upper:]' '[:lower:]')" in
    1 | true | yes | on) return 0 ;;
    '' | 0 | false | no | off) return 1 ;;
    *)
        warn "unrecognized GGSHIELD_REQUIRE_ATTESTATION value '$GGSHIELD_REQUIRE_ATTESTATION'; treating as off (use 1 to require)"
        return 1
        ;;
    esac
}

# Build-provenance verification is opt-in and OFF by default. gh is not a ggshield
# dependency, its version/auth/network state is ambient, and even a public lookup
# needs an authenticated gh — running it automatically is fragile (END-609) and
# usually can't run anyway, while the mandatory sha256 already covers integrity.
# When opted in, fail closed if gh is missing, too old, unauthenticated, or the
# provenance does not verify.
verify_attestation() {
    local file="$1" asset="$2"

    if ! require_attestation; then
        say "Skipping build provenance check (set GGSHIELD_REQUIRE_ATTESTATION=1 to require it). To verify a downloaded asset yourself:"
        printf '    gh attestation verify <asset> --repo %s\n' "$GITHUB_REPO"
        return 0
    fi

    command -v gh >/dev/null 2>&1 ||
        die "GGSHIELD_REQUIRE_ATTESTATION is set but gh is not installed (need gh >= 2.56.0)"
    gh attestation --help >/dev/null 2>&1 ||
        die "GGSHIELD_REQUIRE_ATTESTATION is set but this gh is too old for 'gh attestation' (need >= 2.56.0; e.g. 'brew upgrade gh')"
    gh auth status >/dev/null 2>&1 ||
        die "GGSHIELD_REQUIRE_ATTESTATION is set but gh is not authenticated; run 'gh auth login'"
    say "Verifying build provenance attestation"
    gh attestation verify "$file" --repo "$GITHUB_REPO" >/dev/null ||
        die "build provenance verification failed for $asset: it does not match $GITHUB_REPO"
}

install_tarball() {
    need tar
    resolve_version

    local asset dir tmp bin
    asset="ggshield-$VERSION-$TARGET.tar.gz"
    case "$(asset_http_status "$asset")" in
    200) ;;
    404) die "release v$VERSION has no standalone asset $asset.
arm64 Linux builds ship from v1.52.0 on; pick a newer --version, or see the
other install methods in scripts/install/README.md" ;;
    *) warn "could not confirm $asset availability; attempting the download anyway" ;;
    esac

    dir="$OPT_DIR/ggshield-$VERSION-$TARGET"
    tmp=$(mktemp -d)
    # shellcheck disable=SC2064 # expand now: $tmp is local, gone at exit time
    trap "rm -rf '$tmp'" EXIT

    say "Downloading $asset"
    # Download to a file first: never pipe the network into tar
    fetch -o "$tmp/$asset" \
        "https://github.com/$GITHUB_REPO/releases/download/v$VERSION/$asset"
    verify_download "$tmp/$asset" "$asset"

    say "Installing to $dir"
    rm -rf "$dir"
    mkdir -p "$OPT_DIR"
    tar -xzf "$tmp/$asset" -C "$OPT_DIR"
    [ -d "$dir" ] || die "unexpected archive layout: $dir not found after extraction"

    bin=$(find "$dir" -type f -name ggshield | head -1)
    if [ -z "$bin" ] || [ ! -x "$bin" ]; then
        die "no ggshield executable found in $dir"
    fi
    mkdir -p "$BIN_DIR"
    if [ -e "$BIN_DIR/ggshield" ] && [ ! -L "$BIN_DIR/ggshield" ]; then
        die "$BIN_DIR/ggshield exists and is not a symlink (manually installed?); remove it first"
    fi
    if [ -L "$BIN_DIR/ggshield" ]; then
        case "$(readlink "$BIN_DIR/ggshield")" in
        "$OPT_DIR"/*) ;;
        *) warn "replacing existing ggshield symlink at $BIN_DIR/ggshield (was: $(readlink "$BIN_DIR/ggshield"))" ;;
        esac
    fi
    ln -sf "$bin" "$BIN_DIR/ggshield"

    # Defer the "not on PATH" guidance to the very end (emit_path_hint) so it is
    # the last thing the user sees, not buried before the auth/plugin output.
    case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) PATH_NEEDS_SETUP=1 ;;
    esac
    GGSHIELD="$BIN_DIR/ggshield"
}

write_state() {
    mkdir -p "$STATE_DIR"
    cat >"$STATE_FILE" <<EOF
method=tarball
version=${VERSION:-latest}
opt_dir=$OPT_DIR
bin_link=$BIN_DIR/ggshield
EOF
}

run_gg() {
    # Children may prompt; reconnect stdin to the terminal when we are piped.
    # /dev/tty can exist yet be unopenable (no controlling terminal, e.g. CI or
    # a container), so test that it actually opens — not just that it exists.
    if [ ! -t 0 ] && { : </dev/tty; } 2>/dev/null; then
        "$GGSHIELD" "$@" </dev/tty
    else
        "$GGSHIELD" "$@"
    fi
}

# Hints for steps that did NOT complete — only shown when something is left to
# do (auth failed/skipped, a plugin failed, or --install-only).
emit_auth_hint() {
    local inst="${INSTANCE:-$DEFAULT_INSTANCE}"
    printf '    ggshield auth login --instance %s\n' "$inst"
    [ -z "$INSTANCE" ] &&
        printf '    # EU workspace: ggshield auth login --instance %s\n' "$EU_INSTANCE"
    is_headless && printf '    # headless / no browser: add --method oob\n'
    return 0
}

emit_plugin_hint() {
    [ ${#PLUGINS[@]} -gt 0 ] &&
        printf '    ggshield plugin install %s\n' "${PLUGINS[*]}"
    return 0
}

# ~/.local/bin is the no-sudo convention, but it is not guaranteed to be on
# PATH: macOS never adds it, and Debian/Ubuntu add it from ~/.profile only when
# the directory already exists at login — so a brand-new install often needs a
# shell restart. No sudo-less directory is on PATH across every distro and
# macOS, so we install there and tell the user how to expose it, per shell.
# Also handles the "$SHADOWED_BY" case: BIN_DIR is on PATH but an older ggshield
# sits ahead of it — the same fix (prepend BIN_DIR) resolves both, only the
# wording differs. Uses $SHELL (the login shell) rather than $0: this script
# always runs under bash (curl | bash), which says nothing about the shell the
# user reopens.
emit_path_hint() {
    # The headline goes through warn() (bold yellow, stderr) so it stands out;
    # as an indented "# ..." comment it blended into the command block below and
    # users missed it. The commands stay on stdout, unprefixed, to copy-paste cleanly.
    # reload defaults to `source` (zsh/bash); `.` is the POSIX form for the
    # generic-sh fallback. Paths are double-quoted in the emitted commands so a
    # BIN_DIR/rc with spaces still copy-pastes correctly.
    local rc reload="source" f headline
    if [ -n "$SHADOWED_BY" ]; then
        headline="an older ggshield at $SHADOWED_BY shadows the new one; put $BIN_DIR first on your PATH, then restart your terminal:"
    else
        headline="$BIN_DIR is not on your PATH. Add it, then restart your terminal:"
    fi
    case "$(basename "${SHELL:-sh}")" in
    fish)
        # fish persists PATH via universal variables — no file edit, no restart.
        # --move pulls BIN_DIR to the front when it is already present (shadow case).
        if [ -n "$SHADOWED_BY" ]; then
            warn "an older ggshield at $SHADOWED_BY shadows the new one; move $BIN_DIR to the front with:"
            printf '    fish_add_path --move -- "%s"\n' "$BIN_DIR"
        else
            warn "$BIN_DIR is not on your PATH. Add it permanently with:"
            printf '    fish_add_path -- "%s"\n' "$BIN_DIR"
        fi
        return 0
        ;;
    zsh) rc="$HOME/.zshrc" ;;
    bash)
        if [ "$OS" = darwin ]; then
            # macOS Terminal runs bash as a login shell, which reads the FIRST
            # of these that exists. Appending to ~/.bash_profile when ~/.profile
            # is the active file would shadow it, so reuse whichever exists.
            rc="$HOME/.bash_profile"
            for f in "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile"; do
                if [ -e "$f" ]; then rc="$f"; break; fi
            done
        else
            # Linux: interactive terminals read ~/.bashrc.
            rc="$HOME/.bashrc"
        fi
        ;;
    *)
        rc="$HOME/.profile"
        reload="."
        ;;
    esac
    # Prepend BIN_DIR so it wins whether it was absent or merely behind an older
    # install. A duplicate entry (shadow case) is harmless: the front one wins.
    warn "$headline"
    printf '    echo '\''export PATH="%s:$PATH"'\'' >> "%s"\n' "$BIN_DIR" "$rc"
    printf '    # (or apply it to the current shell now: %s "%s")\n' "$reload" "$rc"
    return 0
}

try_auth() {
    local inst="${INSTANCE:-$DEFAULT_INSTANCE}"
    if [ -n "${GITGUARDIAN_API_KEY:-}" ]; then
        say "Authenticating with GITGUARDIAN_API_KEY against $inst"
        # token login reads the key from stdin; do not use run_gg (it would
        # rebind stdin to /dev/tty)
        printf '%s\n' "$GITGUARDIAN_API_KEY" |
            "$GGSHIELD" auth login --method token ${INSTANCE:+--instance "$INSTANCE"} &&
            return 0
        warn "authentication with GITGUARDIAN_API_KEY failed (instance: $inst)"
        return 1
    fi
    if [ "$ASSUME_YES" = 1 ]; then
        warn "non-interactive run (-y) without GITGUARDIAN_API_KEY: skipping auth"
        return 1
    fi
    say "Authenticating against $inst"
    is_headless &&
        warn "headless/remote session detected; if the browser flow fails, retry with --method oob"
    run_gg auth login ${INSTANCE:+--instance "$INSTANCE"} && return 0
    warn "authentication failed (instance: $inst)"
    return 1
}

post_install() {
    hash -r 2>/dev/null || true
    # actually run it: an executable can still fail (e.g. wrong libc)
    local version_out
    if ! version_out=$("$GGSHIELD" --version 2>&1); then
        die "ggshield was installed but cannot run: $version_out"
    fi
    say "Installed: $version_out"

    # A ggshield from a previous install (brew, pipx, manual) sitting earlier on
    # PATH shadows the one we just linked. The fix is the same as "not on PATH" —
    # put BIN_DIR first — so route it through emit_path_hint with wording that
    # names the offending binary, instead of a dead-end "fix your PATH order".
    # Only when BIN_DIR is actually on PATH (PATH_NEEDS_SETUP still 0): if it is
    # absent, the "not on PATH" hint already prepends it and covers this too.
    local resolved
    resolved=$(command -v ggshield 2>/dev/null || true)
    if [ "$PATH_NEEDS_SETUP" = 0 ] && [ -n "$resolved" ] && [ "$resolved" != "$GGSHIELD" ]; then
        SHADOWED_BY="$resolved"
        PATH_NEEDS_SETUP=1
    fi

    if [ "$INSTALL_ONLY" = 1 ]; then
        # plugin install is auth-gated and --install-only skips auth, so any
        # --plugin is deferred, not installed now: say so rather than silently
        # dropping the flag.
        [ ${#PLUGINS[@]} -gt 0 ] &&
            warn "--plugin not installed: --install-only skips authentication; run the steps below once authenticated"
        say "ggshield is installed. To finish setup:"
        [ "$PATH_NEEDS_SETUP" = 1 ] && emit_path_hint
        emit_auth_hint
        emit_plugin_hint
        return 0
    fi

    # Plugins need a working authentication, so only attempt them once auth
    # succeeds; otherwise skip.
    local auth_ok=0 plugins_pending=0
    if try_auth; then
        auth_ok=1
        local plugin
        # macOS ships bash 3.2, where "${arr[@]}" on an empty array trips
        # `set -u`; the ${arr[@]+…} guard expands to nothing when empty.
        for plugin in ${PLUGINS[@]+"${PLUGINS[@]}"}; do
            say "Installing the $plugin plugin"
            run_gg plugin install "$plugin" ||
                { warn "could not install the $plugin plugin (continuing)"; plugins_pending=1; }
        done
    elif [ ${#PLUGINS[@]} -gt 0 ]; then
        warn "skipping plugin install until ggshield is authenticated"
        plugins_pending=1
    fi

    # Only nag about next steps when something actually remains. A binary the
    # shell cannot find by name is not "ready", so PATH setup counts too.
    if [ "$auth_ok" = 1 ] && [ "$plugins_pending" = 0 ] && [ "$PATH_NEEDS_SETUP" = 0 ]; then
        say "ggshield is ready."
        return 0
    fi
    say "To finish setup:"
    # PATH first: the other hints below assume ggshield is callable by name.
    [ "$PATH_NEEDS_SETUP" = 1 ] && emit_path_hint
    [ "$auth_ok" = 0 ] && emit_auth_hint
    [ "$plugins_pending" = 1 ] && emit_plugin_hint
    return 0
}

main() {
    while [ $# -gt 0 ]; do
        case "$1" in
        -y | --yes) ASSUME_YES=1 ;;
        --instance)
            shift
            INSTANCE="${1:?--instance requires a URL}"
            ;;
        --version)
            shift
            VERSION="${1:?--version requires a value}"
            ;;
        --install-only) INSTALL_ONLY=1 ;;
        --plugin)
            shift
            PLUGINS+=("${1:?--plugin requires a name}")
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *) die "unknown option: $1 (see --help)" ;;
        esac
        shift
    done

    need curl
    need uname
    detect_platform
    say "Platform: $OS/$ARCH${LIBC:+ ($LIBC)} — installing the standalone build"

    install_tarball
    write_state
    post_install
}

main "$@"
