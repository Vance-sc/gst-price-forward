#!/bin/sh
# authorship-check.sh - commit authorship and wording check.
#
# Usage:
#   sh tools/authorship-check.sh pre-commit
#   sh tools/authorship-check.sh commit-msg <message-file>
#   sh tools/authorship-check.sh pre-push [remote-name] [remote-url]   (refs on stdin)
#   sh tools/authorship-check.sh ci <base-sha> <head-sha> <branch-name>
#   sh tools/authorship-check.sh scan-tree
#
# Settings:  .authorship-check.conf (repo root)
# Patterns:  tools/authorship-check.patterns (next to this script)
# Needs only git, sh, awk, grep, sed. See docs/AUTHORSHIP-CHECK.md.

set -u
LC_ALL=C
export LC_ALL

ME=authorship-check
TAB=$(printf '\t')
NL='
'

die() {
    printf '%s: %s\n' "$ME" "$*" >&2
    exit 2
}

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git work tree"
cd "$ROOT" || die "cannot cd to $ROOT"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd) || die "cannot find script folder"
CONF=${AUTHORSHIP_CHECK_CONF:-$ROOT/.authorship-check.conf}
PATTERNS=${AUTHORSHIP_CHECK_PATTERNS:-$SCRIPT_DIR/authorship-check.patterns}
[ -f "$CONF" ] || die "settings file not found: $CONF"
[ -f "$PATTERNS" ] || die "patterns file not found: $PATTERNS"

FMT=local          # local | ci
VIOLATIONS=0
FIX_IDENTITY=0
FIX_COMMIT_IDENTITY=0
FIX_BRANCH=0
FIX_MESSAGE=0
FIX_OLD_MESSAGE=0
FIX_CONTENT=0
FIX_PR_TEXT=0
BAD_BRANCH=

# ---------------------------------------------------------------- settings

conf_values() {
    awk -v k="$1" '
        { sub(/\r$/, "") }
        /^[ \t]*(#|$)/ { next }
        {
            key = $1
            if (key != k) next
            v = $0
            sub(/^[ \t]*[^ \t]+[ \t]*/, "", v)
            sub(/[ \t]+$/, "", v)
            print v
        }' "$CONF"
}

ALLOWED_IDS=$(conf_values allow_identity)
ALLOWED_COMMITTERS=$(conf_values allow_committer)
BLOCKED_BRANCHES=$(conf_values block_branch)
[ -n "$ALLOWED_IDS" ] || die "no allow_identity entry in $CONF"
PRIMARY_ID=$(printf '%s\n' "$ALLOWED_IDS" | sed -n '1p')
PRIMARY_NAME=$(printf '%s\n' "$PRIMARY_ID" | sed 's/[ ]*<.*$//')
PRIMARY_EMAIL=$(printf '%s\n' "$PRIMARY_ID" | sed -n 's/^.*<\(.*\)>.*$/\1/p')

in_list() {  # in_list <value> <newline-separated list>
    [ -n "$2" ] || return 1
    printf '%s\n' "$2" | grep -F -x -q -e "$1"
}

# ---------------------------------------------------------------- output

report() {  # report <message>
    VIOLATIONS=$((VIOLATIONS + 1))
    if [ "$FMT" = ci ]; then
        printf '::error title=Authorship check::%s\n' "$(printf '%s' "$1" | sed 's/%/%25/g')"
    else
        printf '  - %s\n' "$1"
    fi
}

# ---------------------------------------------------------------- scanner
# Input records: <context> TAB <line number> TAB <text>
# scope=msg: context is a label such as "commit abc1234".
# scope=content: context is a repo path.
# Prints one line per hit; exits 1 when anything was found.

# shellcheck disable=SC2016  # awk program, expanded by awk
SCAN_AWK='
function glob2re(g,   r, i, n, c) {
    r = ""; n = length(g); i = 1
    while (i <= n) {
        c = substr(g, i, 1)
        if (c == "*") {
            if (substr(g, i, 3) == "**/") { r = r "(.*/)?"; i += 3; continue }
            if (substr(g, i, 2) == "**") { r = r ".*"; i += 2; continue }
            r = r "[^/]*"
        } else if (c == "?") {
            r = r "[^/]"
        } else if (index(".+()|[]{}^$\\", c) > 0) {
            r = r "\\" c
        } else {
            r = r c
        }
        i++
    }
    return "^" r "$"
}
function gmatch(path, re, slash,   b) {
    if (slash) return (path ~ re)
    b = path; sub(/^.*\//, "", b)
    return (b ~ re)
}
function first_tok(s) { sub(/^[ \t]+/, "", s); sub(/[ \t].*$/, "", s); return s }
function drop_tok(s) { sub(/^[ \t]+/, "", s); sub(/^[^ \t]+[ \t]*/, "", s); return s }
function path_allowed(p,   i) {
    if (p in self) return 1
    for (i = 1; i <= npath; i++) if (gmatch(p, pre[i], pslash[i])) return 1
    return 0
}
function esc(s) { gsub(/%/, "%25", s); gsub(/\r/, "%0D", s); return s }
function escp(s) { s = esc(s); gsub(/:/, "%3A", s); gsub(/,/, "%2C", s); return s }
function hit(ctx, ln, label, text,   s) {
    nhits++
    s = text
    if (length(s) > 160) s = substr(s, 1, 157) "..."
    if (fmt == "ci") {
        if (scope == "content")
            printf "::error file=%s,line=%s,title=Authorship check::%s: %s\n", escp(ctx), ln, label, esc(s)
        else
            printf "::error title=Authorship check::%s, line %s: %s: %s\n", esc(ctx), ln, label, esc(s)
    } else {
        printf "  - %s:%s: [%s] %s\n", ctx, ln, label, s
    }
}
BEGIN {
    self[".authorship-check.conf"] = 1
    self["tools/authorship-check.sh"] = 1
    self["tools/authorship-check.patterns"] = 1
    while ((getline line < conf) > 0) {
        sub(/\r$/, "", line)
        if (line ~ /^[ \t]*(#|$)/) continue
        key = first_tok(line); val = drop_tok(line); sub(/[ \t]+$/, "", val)
        if (key == "allow_identity") {
            ids[val] = 1
        } else if (key == "allow_path") {
            npath++; pre[npath] = glob2re(val); pslash[npath] = (index(val, "/") > 0)
        } else if (key == "allow_line") {
            nline++; lre[nline] = val; lglob[nline] = ""
        } else if (key == "allow_line_in") {
            g = first_tok(val)
            nline++; lre[nline] = drop_tok(val); lglob[nline] = glob2re(g); lslash[nline] = (index(g, "/") > 0)
        }
    }
    close(conf)
    while ((getline line < pats) > 0) {
        sub(/\r$/, "", line)
        if (line ~ /^[ \t]*(#|$)/) continue
        sc = first_tok(line); line = drop_tok(line)
        ty = first_tok(line); line = drop_tok(line)
        lb = first_tok(line); pat = drop_tok(line)
        sub(/[ \t]+$/, "", pat)
        if (sc != scope && sc != "both") continue
        if (ty == "word") pat = "(^|[^a-z0-9_])(" pat ")([^a-z0-9_]|$)"
        else if (ty == "trailer") pat = "^[ \t]*" pat "[ \t]*:"
        else if (ty != "re") continue
        npat++; ptype[npat] = ty; plabel[npat] = lb; ppat[npat] = pat
        any = (npat == 1) ? "(" pat ")" : any "|(" pat ")"
    }
    close(pats)
    if (npat == 0) any = "^$x"
}
{
    sub(/\r$/, "")
    t = index($0, "\t"); ctx = substr($0, 1, t - 1); rest = substr($0, t + 1)
    t = index(rest, "\t"); ln = substr(rest, 1, t - 1); text = substr(rest, t + 1)
    if (scope == "content") {
        if (ctx != lastctx) { lastctx = ctx; skip = path_allowed(ctx) }
        if (skip) next
    }
    raw = tolower(text)
    # Quick test first: one combined pattern, so most lines cost one match.
    if (raw !~ any) next
    l = raw
    for (i = 1; i <= nline; i++)
        if (lglob[i] == "" || (scope == "content" && gmatch(ctx, lglob[i], lslash[i])))
            gsub(lre[i], " ", l)
    for (j = 1; j <= npat; j++) {
        if (ptype[j] == "trailer") {
            if (raw ~ ppat[j]) {
                v = text; sub(/^[^:]*:[ \t]*/, "", v); sub(/[ \t]+$/, "", v)
                if (!(v in ids)) { hit(ctx, ln, plabel[j], text); break }
            }
            continue
        }
        if (l ~ ppat[j]) { hit(ctx, ln, plabel[j], text); break }
    }
}
END { exit (nhits > 0) ? 1 : 0 }
'

scan() {  # scan <msg|content>   (records on stdin)
    awk -v scope="$1" -v fmt="$FMT" -v conf="$CONF" -v pats="$PATTERNS" "$SCAN_AWK"
}

add_hits() {  # add_hits <scanner output>; returns 0 when there were hits
    [ -n "$1" ] || return 1
    printf '%s\n' "$1"
    VIOLATIONS=$((VIOLATIONS + $(printf '%s\n' "$1" | grep -c '^')))
    return 0
}

number_lines() {  # number_lines <context> [strip-comments]
    awk -v ctx="$1" -v strip="${2:-}" '
        { sub(/\r$/, "") }
        strip != "" && /^# -+ >8 -+$/ { exit }
        { n++ }
        strip != "" && /^#/ { next }
        { print ctx "\t" n "\t" $0 }'
}

# Turns `git diff -U0` output into records for added lines.
# shellcheck disable=SC2016  # awk program, expanded by awk
DIFF_AWK='
/^diff --git / { inhdr = 1; path = ""; next }
inhdr && /^\+\+\+ / {
    p = substr($0, 5)
    sub(/\t$/, "", p)
    if (p ~ /^"/) { sub(/^"/, "", p); sub(/"$/, "", p) }
    if (p == "/dev/null") path = ""
    else { sub(/^b\//, "", p); path = p }
    next
}
/^@@ / {
    inhdr = 0
    h = $0
    sub(/^@@ -[0-9,]+ \+/, "", h)
    sub(/[ ,].*$/, "", h)
    ln = h + 0
    next
}
inhdr { next }
/^\+/ { if (path != "") print path "\t" ln "\t" substr($0, 2); ln++ }
'

added_lines() {  # added_lines <git diff args...>
    git -c core.quotepath=off diff -U0 --no-color --no-ext-diff --no-textconv \
        --src-prefix=a/ --dst-prefix=b/ "$@" | awk "$DIFF_AWK"
}

# ---------------------------------------------------------------- checks

check_branch() {  # check_branch <branch-name>
    b=$1
    [ -n "$b" ] || return 0
    lb=$(printf '%s' "$b" | tr '[:upper:]' '[:lower:]')
    old_ifs=$IFS
    IFS=$NL
    for p in $BLOCKED_BRANCHES; do
        lp=$(printf '%s' "$p" | tr '[:upper:]' '[:lower:]')
        case $lb in
            "$lp"*)
                report "branch name \"$b\" starts with \"$p\", which is not allowed"
                FIX_BRANCH=1
                BAD_BRANCH=$b
                break
                ;;
        esac
    done
    IFS=$old_ifs
}

ident_of() {  # ident_of <GIT_AUTHOR_IDENT|GIT_COMMITTER_IDENT>
    git var "$1" 2>/dev/null | sed 's/ [0-9][0-9]* [-+][0-9][0-9]*$//'
}

check_local_identity() {
    a=$(ident_of GIT_AUTHOR_IDENT)
    c=$(ident_of GIT_COMMITTER_IDENT)
    if [ -z "$a" ] || ! in_list "$a" "$ALLOWED_IDS"; then
        report "commit author would be \"${a:-<not set>}\"; it must be \"$PRIMARY_ID\""
        FIX_IDENTITY=1
    fi
    if [ -z "$c" ] || ! in_list "$c" "$ALLOWED_IDS"; then
        report "committer would be \"${c:-<not set>}\"; it must be \"$PRIMARY_ID\""
        FIX_IDENTITY=1
    fi
}

check_commit() {  # check_commit <sha>
    sha=$1
    short=$(git rev-parse --short "$sha")
    info=$(git log -1 --format="%an <%ae>${TAB}%cn <%ce>" "$sha")
    author=${info%%"$TAB"*}
    committer=${info#*"$TAB"}
    if ! in_list "$author" "$ALLOWED_IDS"; then
        report "commit $short: author is \"$author\"; it must be \"$PRIMARY_ID\""
        FIX_COMMIT_IDENTITY=1
    fi
    if ! in_list "$committer" "$ALLOWED_IDS"; then
        if in_list "$committer" "$ALLOWED_COMMITTERS" && in_list "$author" "$ALLOWED_IDS"; then
            :
        else
            report "commit $short: committer is \"$committer\"; it must be \"$PRIMARY_ID\""
            FIX_COMMIT_IDENTITY=1
        fi
    fi
    if add_hits "$(git log -1 --format=%B "$sha" | number_lines "commit $short message" | scan msg)"; then
        FIX_OLD_MESSAGE=1
    fi
}

current_branch() {
    git symbolic-ref --short -q HEAD 2>/dev/null || true
}

is_zero() {
    case $1 in
        *[!0]*) return 1 ;;
    esac
    return 0
}

# ---------------------------------------------------------------- help text

print_help() {
    [ "$VIOLATIONS" -gt 0 ] || return 0
    printf '\nHow to fix:\n'
    if [ "$FIX_IDENTITY" = 1 ]; then
        printf '  * Set your name and email for this repo, then commit again:\n'
        printf '      git config user.name "%s"\n' "$PRIMARY_NAME"
        printf '      git config user.email "%s"\n' "$PRIMARY_EMAIL"
        printf '    Also unset any GIT_AUTHOR_* / GIT_COMMITTER_* variables, and drop\n'
        printf '    --author from the commit command. When amending, add --reset-author.\n'
    fi
    if [ "$FIX_COMMIT_IDENTITY" = 1 ]; then
        printf '  * Rewrite the listed commits with the right author and committer\n'
        printf '    (after setting user.name / user.email as above):\n'
        printf '      last commit only:  git commit --amend --no-edit --reset-author\n'
        printf '      several commits:   git rebase -r <base> --exec "git commit --amend --no-edit --reset-author"\n'
        printf '    then push again (git push --force-with-lease if already pushed).\n'
    fi
    if [ "$FIX_BRANCH" = 1 ]; then
        printf '  * Rename the branch:\n'
        printf '      git branch -m "%s" <new-name>\n' "$BAD_BRANCH"
        printf '    If it was already pushed:  git push -u origin <new-name>  and\n'
        printf '      git push origin --delete "%s"\n' "$BAD_BRANCH"
        printf '    On GitHub, open the pull request from the new branch.\n'
    fi
    if [ "$FIX_MESSAGE" = 1 ]; then
        printf '  * Edit the commit message: remove the listed lines (for example a\n'
        printf '    Co-authored-by: or Signed-off-by: trailer, or a "generated" line).\n'
        printf '    Your message was kept in .git/COMMIT_EDITMSG; reuse it with\n'
        printf '      git commit -e -F .git/COMMIT_EDITMSG\n'
    fi
    if [ "$FIX_OLD_MESSAGE" = 1 ]; then
        printf '  * Reword the listed commit messages:\n'
        printf '      last commit only:  git commit --amend   (delete the listed lines)\n'
        printf '      older commits:     git rebase -i <base>  and mark them "reword"\n'
        printf '    then push again (git push --force-with-lease if already pushed).\n'
    fi
    if [ "$FIX_CONTENT" = 1 ]; then
        printf '  * Edit the listed lines and stage them again (git add <file>).\n'
        printf '    If a line is genuine code use (an SDK import, model id, env var),\n'
        printf '    add an allow_line or allow_path entry to .authorship-check.conf.\n'
    fi
    if [ "$FIX_PR_TEXT" = 1 ]; then
        printf '  * Edit the pull request title or description on GitHub to remove the\n'
        printf '    listed lines; the check runs again automatically.\n'
    fi
}

finish() {  # finish <what was checked>
    if [ "$VIOLATIONS" -gt 0 ]; then
        if [ "$FMT" = ci ]; then
            print_help
            printf '\nAuthorship check failed: %s problem(s) in %s.\n' "$VIOLATIONS" "$1"
            if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
                printf '### Authorship check failed\n%s problem(s) in %s. See the job log for details.\n' \
                    "$VIOLATIONS" "$1" >>"$GITHUB_STEP_SUMMARY"
            fi
        else
            print_help >&2
            printf '\n%s: blocked (%s problem(s) in %s).\n' "$ME" "$VIOLATIONS" "$1" >&2
        fi
        exit 1
    fi
    if [ "$FMT" = ci ]; then
        printf 'Authorship check passed: %s.\n' "$1"
        if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
            printf '### Authorship check passed\n%s.\n' "$1" >>"$GITHUB_STEP_SUMMARY"
        fi
    fi
    exit 0
}

# ---------------------------------------------------------------- modes

mode_pre_commit() {
    {
        check_local_identity
        check_branch "$(current_branch)"
        if add_hits "$(added_lines --cached | scan content)"; then
            FIX_CONTENT=1
        fi
    } >&2
    finish "this commit"
}

mode_commit_msg() {
    msgfile=${1:-}
    if [ -z "$msgfile" ] || [ ! -f "$msgfile" ]; then
        die "usage: $0 commit-msg <message-file>"
    fi
    {
        check_local_identity
        check_branch "$(current_branch)"
        if add_hits "$(number_lines "commit message" strip <"$msgfile" | scan msg)"; then
            FIX_MESSAGE=1
        fi
    } >&2
    finish "the commit message"
}

check_push_line() {  # check_push_line <local-ref> <local-sha> <remote-ref> <remote-sha>
    lsha=$2
    rref=$3
    rsha=$4
    is_zero "$lsha" && return 0          # branch or tag delete
    case $rref in
        refs/heads/*) check_branch "${rref#refs/heads/}" ;;
    esac
    if [ -n "$rsha" ] && ! is_zero "$rsha" && git cat-file -e "$rsha^{commit}" 2>/dev/null; then
        revs=$(git rev-list "$rsha..$lsha")
    else
        revs=$(git rev-list "$lsha" --not --remotes)
    fi
    for sha in $revs; do
        check_commit "$sha"
    done
}

mode_pre_push() {
    {
        if [ -n "${PRE_COMMIT_TO_REF:-}" ]; then
            # Run from the pre-commit framework, which passes refs in variables.
            rb=${PRE_COMMIT_REMOTE_BRANCH:-}
            case $rb in
                refs/*) ;;
                ?*) rb=refs/heads/$rb ;;
            esac
            check_push_line "${PRE_COMMIT_LOCAL_BRANCH:-HEAD}" \
                "$(git rev-parse "$PRE_COMMIT_TO_REF")" "$rb" "${PRE_COMMIT_FROM_REF:-}"
        else
            while read -r l_ref l_sha r_ref r_sha; do
                [ -n "${l_ref:-}" ] || continue
                check_push_line "$l_ref" "$l_sha" "$r_ref" "$r_sha" </dev/null
            done
        fi
    } >&2
    finish "the commits being pushed"
}

mode_ci() {
    FMT=ci
    base=${1:-}
    head=${2:-}
    branch=${3:-}
    if [ -z "$base" ] || [ -z "$head" ]; then
        die "usage: $0 ci <base-sha> <head-sha> <branch-name>"
    fi
    git cat-file -e "$base^{commit}" 2>/dev/null || die "base commit $base not found (check out with fetch-depth: 0)"
    git cat-file -e "$head^{commit}" 2>/dev/null || die "head commit $head not found"

    changed=$(git diff --name-only "$base...$head" -- .authorship-check.conf tools/authorship-check.sh \
        tools/authorship-check.patterns .github/workflows/authorship-check.yml)
    if [ -n "$changed" ]; then
        printf '::notice title=Authorship check::This pull request changes the authorship check itself: %s\n' \
            "$(printf '%s' "$changed" | tr '\n' ' ')"
    fi

    check_branch "$branch"

    ncommits=0
    for sha in $(git rev-list "$base..$head"); do
        ncommits=$((ncommits + 1))
        check_commit "$sha"
    done

    if [ -n "${AUTHORSHIP_PR_TITLE:-}" ]; then
        if add_hits "$(printf '%s\n' "$AUTHORSHIP_PR_TITLE" | number_lines "pull request title" | scan msg)"; then
            FIX_PR_TEXT=1
        fi
    fi
    if [ -n "${AUTHORSHIP_PR_BODY:-}" ]; then
        if add_hits "$(printf '%s\n' "$AUTHORSHIP_PR_BODY" | number_lines "pull request description" | scan msg)"; then
            FIX_PR_TEXT=1
        fi
    fi

    if add_hits "$(added_lines "$base...$head" | scan content)"; then
        FIX_CONTENT=1
    fi
    finish "$ncommits commit(s), branch \"$branch\" and the pull request diff"
}

mode_scan_tree() {
    printf 'Scanning tracked files (report only; nothing is blocked)...\n'
    out=$(git -c core.quotepath=off grep -I --name-only -e '' -- . 2>/dev/null |
        awk '{ f = $0; n = 0; while ((getline line < f) > 0) { n++; print f "\t" n "\t" line } close(f) }' |
        scan content)
    if [ -n "$out" ]; then
        printf '%s\n' "$out"
        printf '\n%s line(s) would be flagged if added in a commit.\n' "$(printf '%s\n' "$out" | grep -c .)"
        printf 'For genuine code use, add allow_line / allow_path entries to .authorship-check.conf.\n'
    else
        printf 'No matches.\n'
    fi
    exit 0
}

mode=${1:-}
[ $# -gt 0 ] && shift
case $mode in
    pre-commit) mode_pre_commit "$@" ;;
    commit-msg) mode_commit_msg "$@" ;;
    pre-push) mode_pre_push "$@" ;;
    ci) mode_ci "$@" ;;
    scan-tree) mode_scan_tree "$@" ;;
    *)
        printf 'usage: %s pre-commit | commit-msg <file> | pre-push [remote] [url] | ci <base> <head> <branch> | scan-tree\n' "$0" >&2
        exit 2
        ;;
esac
