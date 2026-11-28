---json
{
  "id": "bfeb8b8b-4cb5-49c6-bf40-e7f9ba52b45e",
  "revision": 1,
  "published_at": "2026-11-28T13:50:30Z",
  "title": "Sign in workloads without stored secrets",
  "visibility": "highlight",
  "area": "Platform",
  "type": "New",
  "action_required": false,
  "security_review": {
    "status": "not_required"
  },
  "sources": [
    {
      "pr": 1507,
      "role": "implementation"
    }
  ],
  "eligibility": {
    "requires_prs": [
      1507
    ],
    "requires_commits": []
  },
  "assets": [],
  "review": {
    "status": "approved",
    "evidence": [
      "Jack approved this exact entry and setup prompt in the private owner conversation on 2026-11-28: \"This is fine. I still need screenshots before I approve the merge.\" Announcement approval only; merge remains pending.",
      "https://github.com/gobifrost/bifrost/pull/1507"
    ]
  },
  "in_app": true,
  "contributors": [
    {
      "login": "jackmusick",
      "profile_url": "https://github.com/jackmusick",
      "source_pr": 1507
    }
  ],
  "agent_prompt": "You are helping me configure keyless workload sign-in to Bifrost. You can reach my Bifrost instance as an authorized administrator and my GitHub or Azure configuration. Inspect the Identity's roles and organization access, and the provider's issuer, audience, subject, and available claims. Propose an exact trust configuration and verification plan, and wait for my confirmation. After approval, configure the Identity's federated credential and use the API-matched CLI to authenticate. Verify the mapped Identity, an allowed operation, an operation denied by its roles, and the absence of a refresh token. Never print tokens or change unrelated resources."
}
---

Connect GitHub Actions, Azure managed identities, and other trusted workloads to existing Bifrost Identities. Each workload receives a short-lived token limited by that Identity's roles and organization access, with no refresh token.

Idea: [MTG-Thomas](https://github.com/MTG-Thomas) ([#1311](https://github.com/gobifrost/bifrost/issues/1311)); implementation: [Jack Musick](https://github.com/jackmusick).

[Open Identities](/users/identities)
