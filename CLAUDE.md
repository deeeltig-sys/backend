# AI Assistant Rules — CampusMEET

These rules apply to every AI coding assistant working in this repository
(Claude Code, Claude in chat, or any other tool). They are not suggestions.
A change that violates these rules is a failed change, even if the
requested feature technically works.

## 1. Scope discipline

- Touch only the files and lines required to satisfy the specific request.
  If a task can be done by editing one function, do not refactor the file
  around it "while you're in there."
- Do not rename variables, reformat untouched code, reorder imports, or
  change indentation/style in files or sections you were not asked to
  change — even if you'd write it differently.
- If finishing the task properly requires touching something outside the
  stated scope (e.g., a shared component used in 10 places), stop and say
  so before editing. Do not silently expand scope.
- "While I'm here" is not a justification for any edit. Every changed line
  must trace back to the actual request.

## 2. Never break what already works

- Before editing a file, read enough of it (and its call sites, if it's a
  shared component/util/API route) to understand what currently depends
  on it. Do not guess at behavior.
- Prefer additive changes (new class, new prop, new function) over
  modifying existing logic in place, when both achieve the goal.
- If a change could affect more than one page/component (shared CSS
  classes, shared utils, shared API contracts), explicitly list every
  place it will touch before making the change, and confirm that list is
  complete — don't discover a fourth caller after the fact.
- Never delete or rewrite a working migration, RLS policy, or DB function
  to "clean it up" unless that is the explicit task. This project already
  has a history of needing follow-up `*_fix.sql` migrations — every one of
  those was an unnecessary bug. Don't add another.
- If you are not certain a change is safe, say what you're unsure about
  instead of proceeding on a guess.

## 3. Fixing one thing must not mean breaking another

- When fixing bug A, do not "improve" adjacent code B, C, or D in the same
  pass, even if you notice something that looks wrong. Flag it separately
  instead of fixing it inline.
- After a fix, mentally (or actually) trace every other place that calls
  the changed function/component/endpoint. A fix that works for the
  reported case but silently changes behavior for other callers is not a
  fix — it's a new bug.
- Test/verify the specific broken behavior is now correct. Do not assume
  a fix worked because the code "looks right."

## 4. When in doubt, ask or report — don't improvise

- If a request is ambiguous about how far a change should reach (e.g.,
  "make the names gold" — one page, one component, or app-wide?), state
  the assumed scope explicitly and give the option to widen or narrow it,
  rather than guessing silently in either direction.
- If you find something broken that wasn't part of the current task, name
  it in your response — do not fix it without being asked, and do not
  silently ignore it either.

## 5. Every response involving a code change must state

- Exactly which files were changed and why, in one line each.
- What was deliberately *not* touched, if it was adjacent to the change
  and someone might reasonably wonder why it wasn't included.
- Any place the same pattern exists but wasn't updated (so nothing is
  silently left inconsistent).


NOTE FROM BOSS - MAKAVELI X
This platform is building towards becoming the next facebook / IG purposely built for African as the
first social media platform for the black sin. This MD is to prevent the problems that arises after
an AI assistant is done working, a new fix introduces you to a new mistake. 
Please never a same mistake, let always push to build forward pass what exist today.
Thank YOU for helping me my CO-DEVELOPER
