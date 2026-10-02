#!/usr/bin/env python3
"""The course's public data, for its page: what anyone can read in the course
repository, gathered once when the page is published.

    GH_TOKEN=... REPO=org/registration python scripts/site_data.py _site/data.json

A page without a token may ask GitHub's API only 60 times an hour, and that
allowance is per network: a class opening the page from the campus Wi-Fi spends
it in minutes. So the page reads this file, from its own site, and asks GitHub
only when someone signs in. Nothing in it is private - the course settings, the
assignments and how many groups have registered are all public already.
"""
import json
import os
import sys

from ghlib import GitHub, load_assignments, load_course


def main():
    out, repo = sys.argv[1], os.environ["REPO"]
    gh = GitHub(os.environ.get("GH_TOKEN"))
    registered = gh.paginate(f"/repos/{repo}/issues?labels=registered&state=all")
    waiting = [i for i in gh.paginate(f"/repos/{repo}/issues?labels=registration&state=open")
               if not any(l["name"] == "registered" for l in i.get("labels", []))
               and "pull_request" not in i]
    data = {
        "repo": repo,
        "course": load_course(),
        "assignments": load_assignments(gh, repo),
        "registered": len(registered),
        "waiting": len(waiting),
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    print(f"{out}: {len(data['assignments'])} assignment(s), {data['registered']} group(s) "
          f"registered, {data['waiting']} waiting")


if __name__ == "__main__":
    main()
