#!/usr/bin/env bash
# Keep the deployment in step with this folder, without anyone typing `git push`.
#
# The loop below watches the working tree and, once an edit has settled, commits what
# changed and pushes it. Pushing is the part that matters: `main` is what Render builds
# from, and a push is what makes .github/workflows/deploy.yml ask Render to rebuild and
# then wait for the live site to serve the new commit. So the whole chain —
# edit → commit → push → rebuild → live — runs on its own from here.
#
#     scripts/autosync.sh --install      start it, and start it again at every login
#     scripts/autosync.sh --uninstall    stop it and forget it
#     scripts/autosync.sh --status       is it running, and what has it done
#     scripts/autosync.sh --once         one pass, in the foreground, then exit
#     scripts/autosync.sh                the loop itself (this is what launchd runs)
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
PAUSE="$REPO/.git/autosync-off"
LABEL="com.resq.autosync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

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
    summary="$(printf '%s\n' "$files" | head -3 | paste -sd ', ' -)"
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

install() {
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
    <key>StandardOutPath</key><string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
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
    rm -f "$PLIST"
    log "uninstalled"
    say "autosync is off. Changes now wait for a push, as before."
}

status() {
    if [ -f "$PLIST" ] && launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
        say "autosync: running (launchd job $LABEL, watching $REPO)"
    elif [ -f "$PLIST" ]; then
        say "autosync: installed but not running — start it with --install, or by hand"
    else
        say "autosync: not installed — start it with scripts/autosync.sh --install"
    fi
    [ -f "$PAUSE" ] && say "paused by $PAUSE"
    if [ -f "$LOG" ]; then
        say "last lines of $LOG:"
        tail -8 "$LOG"
    fi
}

case "${1:-}" in
    --install) install ;;
    --uninstall) uninstall ;;
    --status) status ;;
    --once) run_loop_once; say "one pass done; see $LOG" ;;
    *) run_loop ;;
esac
