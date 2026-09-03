---
name: ggsel-api
description: Use for any task that designs, implements, reviews, tests, or debugs a GGSEL Seller API V1/V2 integration, including authentication, offers, products, options, variants, stock, chats, orders, reviews, async jobs, and repricing.
---

# GGSEL Seller API skill

## Required knowledge

Before answering or changing code, read the canonical project handbook:

- [`GGSEL_API_REFERENCE.md`](../../GGSEL_API_REFERENCE.md)

It contains the complete verified 17-operation V1 catalog, 24-operation V2 catalog, authentication, schemas, status semantics, implementation recipes, security rules, repository coverage, and unresolved documentation gaps.

Official sources:

- [Seller API V1](https://seller.ggsel.com/docs/en/seller-api-v-1)
- [Seller API V2](https://seller.ggsel.com/docs/en/v2/seller-api-v-2)

## Activation triggers

Use this skill when the request mentions GGSEL/GGSel and any of:

- Seller API, API key, token, signature, permissions, or authentication.
- Offer/product/category/option/variant creation, reading, patching, visibility, stock, or deletion.
- Chats, buyer messages, orders, purchases, sales, reviews, balance, receipts, or unique codes.
- Async jobs, pagination, retries, rate limits, timeouts, or error responses.
- RUB prices, commissions, exchange rates, dry runs, or automated repricing.

## Mandatory workflow

1. Identify the exact GGSEL resource and ID type.
2. Select V1 or V2 deliberately from the capability matrix in the handbook.
3. Confirm the exact method/path in the endpoint catalog.
4. Apply only the authentication scheme for that version.
5. Classify the request shape as official, repository-confirmed, or unresolved.
6. Validate trust-boundary inputs before making an HTTP request.
7. Decide whether retrying is safe; never blindly retry an ambiguous write.
8. For async mutations, persist and poll the job ID to a terminal state.
9. Verify mutations with a subsequent read when possible.
10. Redact credentials, tokens, query strings, buyer PII, unique codes, and inventory content.
11. Add one focused runnable test for non-trivial request construction or response parsing.
12. Update the canonical handbook when authoritative or redacted observed evidence changes an assumption.

## Hard safety rules

- Never invent an unresolved request body or parameter name.
- Never mix V1 token login with V2 API-key-header authentication.
- Never treat offer IDs, stock-product IDs, option IDs, variant IDs, invoice IDs, and chat IDs as interchangeable.
- Never log V1 tokens, the V2 `Authorization` value, API keys, buyer details, or delivered product values.
- Never batch-test inferred writes against live products.
- Require an explicit feature flag, dry run, bounds, approval, and read-back verification for financial or destructive writes.
- Honor `Retry-After`; retry bounded safe reads, not arbitrary POST/PATCH/DELETE operations.
- Treat HTTP success and application success separately, especially V1 `retval` envelopes and V2 async jobs.

## Repository-specific context

- [`ggsel_api.py`](../../ggsel_api.py) is the hardened V1 client plus public V2 offer reads and a disabled-by-default, allowlisted price PATCH with zero retries and GET verification.
- [`config.py`](../../config.py) owns environment validation and the independent V2 price-write server gate; environment live mode must remain false.
- [`repricing.py`](../../repricing.py) contains pure price calculations.
- [`bot_service.py`](../../bot_service.py) runs CBR dry-run or separately confirmed live repricing, persists a pre-write 24-hour checkpoint, and revokes live approval when targets change.
- [`tests/test_ggsel_api.py`](../../tests/test_ggsel_api.py), [`tests/test_repricing.py`](../../tests/test_repricing.py), and [`tests/test_sync_controls.py`](../../tests/test_sync_controls.py) are the focused checks.
- No V1 price-write method exists. Preserve the independent server gate, Telegram confirmation, target allowlist, max-change bound, zero-retry write, and GET verification around automatic V2 repricing.

## Response standard

State which API version, endpoint, authentication method, request-schema evidence, retry policy, verification method, and safety gate are being used. If authoritative details are missing, say exactly what is unresolved and stop before unsafe implementation.
