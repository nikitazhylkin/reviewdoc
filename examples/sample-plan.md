---
title: Checkout redesign
subtitle: Phase 1 implementation plan
project: Storefront
status: For review
---

A focused plan to cut checkout abandonment. Three sections below, each needs your Approve or Rework before we start.

## Discovery {priority=high status=now}

What we know, and what we are still assuming.

- Cart-to-purchase drops sharpest on the shipping step.
- Guest checkout is requested often, but not built.
- Mobile is most of the traffic and the worse experience.

> [!decision] Ship guest checkout in Phase 1, accounts in Phase 2.

## Build {priority=high}

The work, in order.

### Shipping step

Collapse the three-field address block into one autocompleted input.

### Payment

Add one-tap wallets above the card form.

| Task | Owner | Estimate |
| --- | --- | --- |
| Guest checkout | Backend | 3 days |
| Address autocomplete | Frontend | 2 days |
| Wallet buttons | Frontend | 1 day |

> [!risk] Wallet certification can take a few days on the provider side. Start it first.

## QA and rollout {priority=medium}

How we ship it without breaking the funnel.

- Ship behind a flag at 10% of traffic.
- Watch the shipping-step drop-off for 48 hours.
- Roll to 100% once the funnel holds.

> [!question] Do we gate rollout on a hard conversion number, or on no-regression?
