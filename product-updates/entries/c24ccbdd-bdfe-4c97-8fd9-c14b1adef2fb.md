---json
{
  "id": "c24ccbdd-bdfe-4c97-8fd9-c14b1adef2fb",
  "revision": 1,
  "published_at": "2026-11-07T02:08:07Z",
  "title": "Protect Workspace and Build Boundaries",
  "visibility": "highlight",
  "area": "Platform",
  "type": "Security",
  "action_required": false,
  "in_app": false,
  "security_review": {
    "status": "required",
    "review_ref": "https://github.com/gobifrost/bifrost/pull/929"
  },
  "sources": [
    {
      "pr": 929,
      "role": "implementation"
    },
    {
      "pr": 1212,
      "role": "implementation"
    },
    {
      "pr": 1213,
      "role": "implementation"
    },
    {
      "pr": 1235,
      "role": "implementation"
    },
    {
      "pr": 1258,
      "role": "implementation"
    }
  ],
  "contributors": [
    {
      "login": "jackmusick",
      "profile_url": "https://github.com/jackmusick",
      "source_pr": 929
    },
    {
      "login": "jackmusick",
      "profile_url": "https://github.com/jackmusick",
      "source_pr": 1212
    },
    {
      "login": "MTG-Thomas",
      "profile_url": "https://github.com/MTG-Thomas",
      "source_pr": 1212,
      "role": "implementation"
    },
    {
      "login": "jackmusick",
      "profile_url": "https://github.com/jackmusick",
      "source_pr": 1213
    },
    {
      "login": "MTG-Thomas",
      "profile_url": "https://github.com/MTG-Thomas",
      "source_pr": 1213,
      "role": "implementation"
    },
    {
      "login": "MTG-Thomas",
      "profile_url": "https://github.com/MTG-Thomas",
      "source_pr": 1235
    },
    {
      "login": "jackmusick",
      "profile_url": "https://github.com/jackmusick",
      "source_pr": 1258
    }
  ],
  "eligibility": {
    "requires_prs": [
      929,
      1212,
      1213,
      1235,
      1258
    ],
    "requires_commits": []
  },
  "assets": [],
  "review": {
    "status": "approved",
    "evidence": [
      "https://github.com/gobifrost/bifrost/pull/929",
      "https://github.com/gobifrost/bifrost/pull/1212",
      "https://github.com/gobifrost/bifrost/pull/1213",
      "https://github.com/gobifrost/bifrost/pull/1235",
      "https://github.com/gobifrost/bifrost/pull/1258",
      "Release-only canonical notice prepared from the approved per-source security reviews; source claims, attribution and copy await final range review. Existing in-app omissions are preserved by in_app:false.",
      "Release coordinator completed final copy/source review under delegated preparation authority: concrete customer outcome, scoped required actions, verified routes, retained UUID/credits, security/source evidence and agent prompts. This approves the prepared prose, not stable publication; the final source freeze and nightly remain release gates."
    ]
  }
}
---
Editor deletion rejects empty and ambiguous paths before storage access. Solution policy caches keep installation scopes separate, and deploy archives reject symlinks. Development containers start as the unprivileged user. Build tooling verifies package integrity; Python and certificate maintenance reject compromised trust paths while preserving configured private certificate authorities.
