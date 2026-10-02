# Assignments

One JSON file per assignment. `scripts/new_assignment.py` commits them here for you,
over the API — you don't normally touch this folder by hand.

```json
{
  "name": "a3",
  "template": "a3-template",
  "soft_deadline": "2026-11-15T23:59:00+00:00",
  "hard_deadline": "2026-11-22T23:59:00+00:00",
  "created": "2026-10-01T12:00:00+00:00"
}
```

Two things read this folder, which is why the files live in the repo rather than on
someone's laptop:

- `register.py` — a group registering now gets one repo per file in here;
- `deadlines.py` — locks repos once `hard_deadline` has passed, and reports lateness
  against both deadlines.

Either deadline may be `null` (that is what `--hard none` stores); an assignment with no
`hard_deadline` is never locked. Editing a date here by hand works and takes effect on the
next hourly run, but `new_assignment.py ORG a3 --hard "..."` is the safer way — it keeps
the rest of the file intact and commits it for you.
