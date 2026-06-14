---
name: nono-commit
description: Commit changes in the Coach Nono project following the [claude]-prefix convention. Use this skill whenever the user says "commit", "stage and commit", or asks to save progress to git in this project. Commits map one-to-one to checklist items in acc-coach-roadmap.md or to explicit user edits — never bundled by phase. Each subject is prefixed [claude], uses imperative mood, has optional bullets for multi-file changes, and never includes Co-Authored-By.
---

# nono-commit

Commit Coach Nono changes: one commit per checklist item or explicit user edit.

## What counts as one commit

The roadmap (`acc-coach-roadmap.md`) is the canonical checklist. Every `- [ ]` item is a potential commit unit. Explicit edits from the user (renaming, persona changes, new config fields) are separate units on top.

**One commit = one checkable item or one explicit instruction.**  
Never bundle a full phase. Never dump all modified files into one commit without verifying they answer the same "what is this for?" question.

## Commit message format

```
[claude] <imperative verb phrase, ≤ 60 chars>

- sub-part bullet   ← only when the commit touches distinct files/sub-systems worth naming
- sub-part bullet   ← max 5; omit entirely for single-concern changes
```

- Imperative mood: "Add", "Wire", "Rename", "Tick" — not "Added" or "Adding"
- No period at the end of the subject
- No `Co-Authored-By` trailer, ever
- Blank line between subject and bullets (if bullets are present)

### Examples

```
[claude] Add .gitignore: exclude data/, .env, __pycache__
```

```
[claude] Add DB schema: sessions, laps, findings, pbs

- laps.status: pending | processing | done | failed with SKIP LOCKED job-claim
- findings: per-corner kind/severity/time_loss_s
- pbs: unique best-lap reference per game/car/track
```

```
[claude] Add Coach Nono persona and voice output plan

- config.py: coach_name, coach_persona, coach_tts + tts_voice_ref hooks
- .env.example: COACH_NAME, COACH_PERSONA, COACH_TTS, TTS_VOICE_REF documented
- roadmap Phase E: voice output item using XTTS v2 zero-shot cloning from Nono's voice
```

---

## Workflow

1. **Read the diff before writing anything**
   ```bash
   git status
   git diff           # unstaged
   git diff --cached  # already staged
   ```

2. **Map each changed file to a checklist item or explicit edit.** If a file serves two different purposes (e.g., `config.py` has both the base loader and persona fields added in a second pass), split it with `git add -p`.

3. **Stage and commit each unit in order:**
   ```bash
   git add <files>
   git commit -m "$(cat <<'EOF'
   [claude] one-liner here

   - bullet if needed
   EOF
   )"
   ```

4. **Confirm with `git log --oneline -10`** — subjects should be scannable and non-redundant.

5. **End with a clean tree.** `git status` should show nothing unexpected after the last commit.

## Splitting mixed-content files

When one file has content belonging to two separate commits:
```bash
git add -p <file>   # interactively stage only the relevant hunks
git commit ...
git add <file>      # stage the remainder
git commit ...
```
