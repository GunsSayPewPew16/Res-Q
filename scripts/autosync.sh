#!/usr/bin/env bash
# Keep the deployment in step with this folder, without anyone typing `git push`.
#
# The loop below watches the working tree and, once an edit has settled, commits what
# changed and pushes it. Pushing is the part that matters: `main` is what Render builds
# from, and a push is what makes .github/workflows/deploy.yml ask Render to rebuild and
# then wait for the live site to serve the new commit. So the whole chain —
# edit → commit → push → rebuild → live — runs on its own from here.
#
#     scripts/autosync.sh --install      start watching, and push every commit from now on
#     scripts/autosync.sh --uninstall    stop it and forget it
#     scripts/autosync.sh --status       is it running, and what has it done
#     scripts/autosync.sh --once         one pass, in the foreground, then exit
#     scripts/autosync.sh                the loop itself
#
# It installs two things and either one of them is enough to get a change deployed:
#
#   * a **commit hook** (`.git/hooks/post-commit`) that pushes after every commit made in
#     this checkout — by an agent, by an editor, by hand. Committing is publishing, which is
#     the part that was being done manually.
#   * a **watcher** (a launchd job) that also commits on its own, so an edit that nobody
#     committed still leaves. macOS only lets a background agent into ~/Downloads,
#     ~/Desktop and ~/Documents with the user's permission (see `guarded_folder`), so where
#     the watcher cannot start, the hook carries on alone and says so.
#
# `--push` is the hook's own entry point: no loop, no committing, just what is ahead.
#
# A few deliberate restraints, because this writes to a live site:
#
#   * An edit is only committed once the same set of changes has been on disk, unchanged,
#     for AUTOSYNC_QUIET seconds (10 by default). A file being saved twice, or a change
#     landing in three files over a few seconds, becomes one commit rather than three.
#   * The backend is compiled before anything is committed: a change that cannot even be
#     imported is not pushed, and the reason is left in the log.
#   * Only AUTOSYNC_BRANCH (main) is pushed, never force, and never while a merge or rebase
#     is half-finished — that is somebody else's work in progress, not an edit to publish.
#   * `touch .git/autosync-off` pauses it. `rm .git/autosync-off` resumes it. Nothing else
#     is needed to stop a commit going out while something bigger is being prepared.
#
# The log is .git/autosync.log — inside the repository's own git directory, so it is never
# itself committed.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

LOG="$REPO/.git/autosync.log"
LAUNCHD_LOG="${AUTOSYNC_LAUNCHD_LOG:-/tmp/resq-autosync-launchd.log}"
PAUSE="$REPO/.git/autosync-off"
LABEL="com.resq.autosync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

HOOK="$REPO/.git/hooks/post-commit"

INTERVAL="${AUTOSYNC_INTERVAL:-20}"   # how often the tree is looked at, in seconds
QUIET="${AUTOSYNC_QUIET:-10}"         # how long an edit must sit still before it is committed
BRANCH="${AUTOSYNC_BRANCH:-main}"     # the branch the deployment builds from

# Files that are never part of a commit: scratch output, editor leftovers, and anything
# large enough that it was probably never meant to ship.
JUNK='(^|/)(\.DS_Store|.*\.log|.*\.tmp|.*\.bak|.*~|node_modules/|\.freebuff/|__pycache__/|.*\.pyc|.*\.db)$'

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

say() { printf '%s\n' "$*"; }

# ---------------------------------------------------------------- what to do with a tree

dirty_signature() {
    # The identity of the current set of changes: same output means nothing has moved since
    # the last look, which is what tells the loop an edit has settled.
    git status --porcelain=v1 2>/dev/null | shasum | awk '{print $1}'
}

sanity_check() {
    # The cheapest check that catches the mistake worth catching: a backend change that does
    # not even compile. It is not a test suite — it only has to be fast and never wrong.
    python3 -m compileall -q "$REPO/backend" >/dev/null 2>&1
}

commit_message() {
    local files count summary
    files="$(git diff --cached --name-only)"
    count="$(printf '%s\n' "$files" | grep -c . )"
    summary="$(printf '%s\n' "$files" | head -3 | tr '\n' '\n' | sed -e 's/$/,/' | tr -d '\n' | sed -e 's/,$//' -e 's/,/, /g')"
    if [ "$count" -gt 3 ]; then
        summary="$summary and $((count - 3)) more"
    fi
    printf 'Auto-commit: %s\n\n%s\n\nGenerated with Codebuff 🤖\nCo-Authored-By: Codebuff <noreply@codebuff.com>\n' \
        "$summary" "$files"
}

publish() {
    # Stage everything, take the junk back out, and commit only if something is left.
    git add -A -- . 2>/dev/null
    local junk
    junk="$(git diff --cached --name-only | grep -E "$JUNK" || true)"
    if [ -n "$junk" ]; then
        printf '%s\n' "$junk" | while IFS= read -r path; do
            [ -n "$path" ] && git reset -q -- "$path" 2>/dev/null
        done
        log "left out of the commit: $(printf '%s' "$junk" | paste -sd ', ' -)"
    fi
    if git diff --cached --quiet 2>/dev/null; then
        return 0
    fi
    if ! sanity_check; then
        log "NOT committed: backend does not compile. Fix it and this will pick it up again."
        return 1
    fi
    git commit -q -F - <<<"$(commit_message)" || { log "commit failed"; return 1; }
    log "committed $(git rev-parse --short HEAD): $(git log -1 --pretty=%s)"
}

push_if_ahead() {
    local ahead
    ahead="$(git rev-list --count "@{upstream}..HEAD" 2>/dev/null || echo 0)"
    if [ "${ahead:-0}" -eq 0 ]; then
        return 0
    fi
    if git push -q origin "HEAD:$BRANCH" 2>>"$LOG"; then
        log "pushed $ahead commit(s) to origin/$BRANCH — Render will rebuild"
        return 0
    fi
    # A rejected push is almost always the same thing: something else landed on the remote
    # first. Replay this work on top of it once, with the local edits stashed and restored,
    # and try again — never forced, so nothing of anybody else's is thrown away.
    log "push rejected; replaying on top of origin/$BRANCH"
    if git pull -q --rebase --autostash origin "$BRANCH" >>"$LOG" 2>&1 && git push -q origin "HEAD:$BRANCH" 2>>"$LOG"; then
        log "pushed after replaying"
    else
        log "push still failing; it will be retried on the next pass"
    fi
}

in_the_middle_of_something() {
    # A merge or rebase that has not finished belongs to whoever started it; committing
    # through it would file their work as an automatic change.
    [ -d "$REPO/.git/rebase-merge" ] || [ -d "$REPO/.git/rebase-apply" ] || [ -f "$REPO/.git/MERGE_HEAD" ]
}

pass() {
    # One full pass over the tree: publish an edit, or push commits that are already made.
    if [ -n "$(git status --porcelain=v1)" ]; then
        publish && push_if_ahead
    else
        push_if_ahead
    fi
}

run_loop_once() {
    # The same pass, once, without the settling wait — for a hand-run "do it now".
    if [ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" != "$BRANCH" ]; then
        say "on $(git rev-parse --abbrev-ref HEAD), not $BRANCH: nothing to publish"
        return 0
    fi
    pass
}

# ------------------------------------------------------------------------------- the loop

run_loop() {
    log "watching $REPO (branch $BRANCH, every ${INTERVAL}s)"
    local seen="" seen_at=0 signature now
    while :; do
        if [ -f "$PAUSE" ]; then
            sleep "$INTERVAL"
            continue
        fi
        if [ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" != "$BRANCH" ] || in_the_middle_of_something; then
            sleep "$INTERVAL"
            continue
        fi
        if [ -n "$(git status --porcelain=v1 2>/dev/null)" ]; then
            signature="$(dirty_signature)"
            now="$(date +%s)"
            if [ "$signature" != "$seen" ]; then
                # Different changes from the last look: the edit is still moving, so the
                # clock on it starts over rather than committing a half-finished save.
                seen="$signature"
                seen_at="$now"
            elif [ $((now - seen_at)) -ge "$QUIET" ]; then
                if publish; then
                    push_if_ahead
                    seen=""
                else
                    # The same uncommittable changes would otherwise be retried every pass;
                    # waiting for the next edit keeps the log readable.
                    seen_at="$now"
                fi
            fi
        else
            # A clean tree still may hold commits of its own — made here, or by another
            # agent in this folder — and those are exactly what wants pushing.
            seen=""
            push_if_ahead
        fi
        sleep "$INTERVAL"
    done
}

# ---------------------------------------------------------------- installing it as a job

guarded_folder() {
    # Downloads, Desktop and Documents are guarded by macOS: a process started by launchd
    # is refused even a `cd` into them until the user grants that agent access, which a
    # script cannot grant itself. Detecting it here is what keeps the watcher from being
    # installed into a crash loop that only says "Operation not permitted" in /tmp.
    case "$REPO" in
        "$HOME/Downloads"/*|"$HOME/Desktop"/*|"$HOME/Documents"/*) return 0 ;;
    esac
    return 1
}

install_hook() {
    mkdir -p "$(dirname "$HOOK")"
    cat >"$HOOK" <<'HOOK_END'
#!/bin/sh
# Push this commit. See scripts/autosync.sh: committing here is publishing, and pushing is
# what makes Render rebuild and the live site answer with the new revision.
[ -f "$(git rev-parse --show-toplevel)/.git/autosync-off" ] && exit 0
exec /bin/bash "$(git rev-parse --show-toplevel)/scripts/autosync.sh" --push
HOOK_END
    chmod +x "$HOOK"
    log "commit hook installed at $HOOK"
}

install() {
    install_hook
    if guarded_folder; then
        log "watcher not installed: $REPO is in a macOS-guarded folder"
        say "autosync is on for this checkout: every commit now pushes itself."
        say "The background watcher is off in this folder — macOS refuses a launchd agent"
        say "access to ~/Downloads (that is the 'Operation not permitted' in"
        say "$LAUNCHD_LOG). Move the repository somewhere unguarded, or grant Terminal and"
        say "bash Full Disk Access in System Settings → Privacy & Security, and run --install"
        say "again, to have edits nobody committed pushed as well."
        return 0
    fi
    mkdir -p "$(dirname "$PLIST")"
    cat >"$PLIST" <<PLIST_END
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$REPO/scripts/autosync.sh</string>
    </array>
    <key>WorkingDirectory</key><string>$REPO</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <!-- launchd's own copy of anything the script prints, kept outside the repository:
         a job that cannot start (macOS guards ~/Downloads, ~/Desktop and ~/Documents from
         background agents until the user grants them access) leaves its complaint here,
         and a file the script itself could not have written would hide exactly that. -->
    <key>StandardOutPath</key><string>$LAUNCHD_LOG</string>
    <key>StandardErrorPath</key><string>$LAUNCHD_LOG</string>
</dict>
</plist>
PLIST_END
    launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1
    launchctl bootout "gui/$UID" "$PLIST" >/dev/null 2>&1
    if launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST" 2>/dev/null; then
        log "installed as $LABEL"
        say "autosync is on: $REPO will commit and push itself, and again after a restart."
        say "Pause it with: touch $PAUSE"
    else
        say "could not load $PLIST — run it by hand instead: bash $REPO/scripts/autosync.sh"
        exit 1
    fi
}

uninstall() {
    launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1
    launchctl bootout "gui/$UID" "$PLIST" >/dev/null 2>&1
    launchctl unload -w "$PLIST" >/dev/null 2>&1
    rm -f "$PLIST" "$HOOK"
    log "uninstalled"
    say "autosync is off. Changes now wait for a push, as before."
}

status() {
    if [ -x "$HOOK" ]; then
        say "autosync: commit hook installed — a commit in this checkout pushes itself"
    else
        say "autosync: no commit hook — commits wait for a push"
    fi
    if [ -f "$PLIST" ] && launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
        say "autosync: watcher running (launchd job $LABEL, watching $REPO)"
    elif [ -f "$PLIST" ]; then
        say "autosync: watcher installed but not running — start it with --install"
    elif guarded_folder; then
        say "autosync: watcher off (macOS keeps launchd out of $REPO) — the commit hook is the whole mechanism here"
    else
        say "autosync: watcher not installed — start it with scripts/autosync.sh --install"
    fi
    [ -f "$PAUSE" ] && say "paused by $PAUSE"
    if [ -f "$LOG" ]; then
        say "last lines of $LOG:"
        tail -8 "$LOG"
    fi
    if [ -s "$LAUNCHD_LOG" ]; then
        say "last lines of $LAUNCHD_LOG (what launchd saw):"
        tail -5 "$LAUNCHD_LOG"
    fi
}

case "${1:-}" in
    --push) push_if_ahead ;;
    --hook) install_hook ;;
    --install) install ;;
    --uninstall) uninstall ;;
    --status) status ;;
    --once) run_loop_once; say "one pass done; see $LOG" ;;
    *) run_loop ;;
esac
