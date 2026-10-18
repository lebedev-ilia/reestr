Result written to `memory/jobs/job-041/result.md`. Summary of what was done for bl-112:

**Fixed:** `write_fact()` in `core/memory.py` now merges old+new `sources`/`links` on repeat writes instead of overwriting (same pattern as the `write_rule` fix for bl-031). Added `_parse_list_field`/`_merge_lists` helpers. 8 new selftest checks in `core/selftest.py`, wired into `./jarvis --selftest` (already part of it via `test_tools`). Journal entry added to `memory/core_changes.md`.

**Restored from git history:** 8 living facts had their `sources`/`links` header fields restored (text bodies untouched) — `ilya.md` (the case named in the brief), `dolgi-kontur.md`, `avtonomnaya-rabota-ustroystvo.md`, `brend-kompanii-avtomatizacii.md`, `golos-stek.md`, `rassylka-otpravka-ustroystvo.md`, `vk-api-osobennosti.md`, `vk-kanal-rekvizity.md`. About a dozen other candidate "losses" were deliberately left alone — they turned out to be intentional renames/restructuring/wording refinements, not the bug, and the brief said not to invent.

**Selftest:** 1339 ok / 4 pre-existing unrelated failures before → 1366 ok / same 4 failures after. Failure count did not grow.

**Git hygiene note:** a concurrent subagent (job-040) was editing the same working tree; my first commit accidentally swept in their files. Caught it, did a `git reset --soft` (non-destructive, no stash/hard-reset used) and re-split into clean commits — only my files ended up in my two commits.
