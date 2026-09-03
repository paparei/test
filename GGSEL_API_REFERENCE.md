# GGSEL Seller API handbook for humans and AI agents

> Canonical project knowledge base for the GGSEL Seller API. Read this file before designing, implementing, debugging, or reviewing any GGSEL integration.

Retrieved from the official English documentation on 2026-08-30.

## 1. Source authority and evidence labels

Primary sources:

- [Seller API V1](https://seller.ggsel.com/docs/en/seller-api-v-1)
- [Seller API V2](https://seller.ggsel.com/docs/en/v2/seller-api-v-2)

Use these evidence labels throughout this handbook:

- **Official** — directly visible in an official endpoint or schema page.
- **Repository-confirmed** — exercised by this project's client in [`ggsel_api.py`](ggsel_api.py) or its tests.
- **Unresolved** — the official generated page did not expose enough information. Do not infer missing details.

The endpoint request/response widgets were partially broken at retrieval time: the server-rendered pages showed method, path, status codes, and status descriptions, but some parameter/body sections remained loading skeletons because the referenced JavaScript assets returned `NoSuchKey`. Separate schema pages were readable. Consequently:

1. Trust exact method/path pairs and status codes listed here.
2. Trust fields from the linked schema pages.
3. Trust request examples only when marked **Official schema** or **Repository-confirmed**.
4. Never fabricate a write payload from a response object with similar fields.
5. Re-check the official page or ask GGSEL support before implementing any unresolved write request.

## 2. Agent operating rules

When a task involves GGSEL:

1. Identify the resource: account, category, chat, offer, option, variant, inventory product, order, review, or async job.
2. Choose V1 or V2 deliberately; do not mix their authentication schemes or base paths.
3. Start with the least powerful endpoint that satisfies the task. Prefer reads before writes.
4. Validate IDs as positive integers and bound pagination/batch sizes before crossing the HTTP boundary.
5. Treat credentials, V1 tokens, buyer details, product contents, and order data as secrets.
6. Use explicit connect/read timeouts. Retry only operations known to be safe to repeat.
7. Honor `Retry-After` on HTTP `429` and use bounded exponential backoff for temporary failures.
8. For asynchronous mutations, save the returned job ID and poll the documented job-result endpoint.
9. Verify mutations with a subsequent read when a read endpoint exists.
10. Never log API keys, authorization headers, V1 tokens, raw product contents, or unnecessary buyer PII.
11. Never test an inferred payload against all live products. Use a non-production offer or one explicitly approved product.
12. If documentation and observed behavior disagree, preserve the raw redacted response, stop unsafe writes, and update this handbook with evidence.

## 3. API resource model

GGSEL uses related terms that are easy to confuse:

- **Offer** — the seller's marketplace listing. V1 frequently calls this a product/good; V2 consistently uses offer.
- **Option** — a configurable field attached to an offer, such as text, checkbox, or radio choice.
- **Variant** — a selectable value under an option. It can modify the price.
- **Product** — a stock unit/content value delivered to a buyer. V2 product records have `id`, `value`, `status`, and `created_at`.
- **Splitted product** — stock associated with an option variant rather than the base offer.
- **Order/purchase/sale** — a paid or otherwise tracked buyer transaction, generally identified by `invoice_id`.
- **Chat/debate** — the buyer/seller message thread, generally identified by `id_i` in V1 chat operations.
- **Async job** — a background mutation identified by a job ID and polled until completed or failed.

Conceptual hierarchy:

```text
offer
├── base inventory products
└── options
    └── variants
        └── splitted products
```

Do not assume an offer ID, product stock-unit ID, variant ID, invoice ID, and chat ID are interchangeable.

## 4. Version selection

### Use V1 when

- The capability exists only in V1: account balance/receipts, chats/messages, orders, reviews, unique-code checks, or the documented bulk price operation.
- Maintaining or extending this repository's existing client, which currently implements V1.
- Reading the established V1 product response used by the dry-run repricer.

### Use V2 when

- Managing offers, options, variants, or stock through the newer resource-oriented API.
- Category commission fields (`fee` and `payment_fee`) are needed.
- A per-offer patch or explicit batch lifecycle action is appropriate.
- Polling V2 asynchronous jobs.

### Never mix

- V1 token login with V2 endpoints.
- A V2 API-key header with assumptions about V1 response envelopes.
- V1 `product_id` semantics with V2 inventory-product IDs without verifying the resource.

## 5. Hosts, transport, and media types

| Version | Base URL |
|---|---|
| V1 | `https://seller.ggsel.com/api_sellers/api` |
| V2 | `https://seller.ggsel.com/api_sellers/v2` |
| V1 search exception | `https://seller.ggsel.com/api_sellers/xml/shop_search.asp` |

Transport rules:

- HTTPS only.
- Use `Accept: application/json` where JSON is expected.
- Use `Content-Type: application/json` for JSON bodies.
- Some V1 operations accept a `locale` header; this repository uses `ru` or `ru-RU` on selected reads.
- Do not put credentials in a URL path, logs, exception text, or source control.
- The repository defaults to a 5-second connect timeout and 30-second read timeout.

## 6. Authentication

### 6.1 V1 token login

**Official endpoint:** `POST /api_sellers/api/apilogin`

**Repository-confirmed request body:**

```json
{
  "seller_id": 1234567,
  "timestamp": "1788062400",
  "sign": "sha256-hex-digest"
}
```

Signature construction:

```text
timestamp = current Unix time in whole seconds, rendered as a string
sign_input = api_key + timestamp
sign = lowercase_hex(SHA-256(UTF-8(sign_input)))
```

Minimal Python construction:

```python
import hashlib
import time

timestamp = str(int(time.time()))
sign = hashlib.sha256(f"{api_key}{timestamp}".encode()).hexdigest()
```

**Repository-confirmed success shape:** a JSON object containing a non-empty string `token`.

For authenticated V1 operations, this repository passes the token as the `token` query parameter. It never embeds the token directly into a pre-rendered URL. On HTTP `401` or `403`, it clears the token, logs in once, and repeats the request once.

Security rules:

- The API key is used to create the signature but is never sent as `api_key` by this login implementation.
- Generate a new timestamp/signature for each login attempt.
- Never log the signature input, API key, returned token, full query string, or authorization material.
- Keep system time synchronized; timestamp-based authentication may fail when the host clock drifts.

### 6.2 V2 API-key authentication

**Official:** V2 uses an API key issued in the seller admin page.

```http
Authorization: YOUR_API_KEY
```

The security scheme is an `apiKey` in the `Authorization` header. The key must have all permissions or the specific `api_sellers/v2` permissions needed by the operation.

Do not prepend `Bearer` unless current official documentation explicitly changes the scheme; the retrieved documentation only specifies the raw API key in `Authorization`.

## 7. Response and failure conventions

### V1

Many V1 schemas use an application envelope:

```json
{
  "retval": 0,
  "retdesc": "string",
  "errors": ["string"],
  "content": {}
}
```

Not every V1 endpoint uses this exact shape. Examples:

- Messages are documented as a top-level array.
- Chats use `cnt_pages` and `items`.
- Product detail uses a `product` object in the official schema, while observed wrappers may place it under `content`.

A transport-level `2xx` does not always prove application success. Where `retval` exists, verify its success value (this repository expects `0`). Preserve unknown fields for forward compatibility.

### V2

V2 uses HTTP status codes and documented error objects:

```json
{
  "errors": [
    {"code": "NOT_FOUND"}
  ]
}
```

Entity-aware errors can include:

```json
{
  "errors": [
    {
      "code": "NOT_FOUND",
      "entity": {
        "id": 123,
        "resource": "offers",
        "description": "string"
      }
    }
  ]
}
```

Common interpretation:

| Condition | Handling |
|---|---|
| `2xx` | Parse according to the endpoint; `204` intentionally has no response body. |
| `400` | Correct the request; do not blind-retry. |
| `401`/`403` | Authentication/permission failure; refresh V1 token once or fix V2 permissions. |
| `404` | Wrong ID/resource or deleted object; do not blind-retry. |
| `408`, `429`, `500`, `502`, `503`, `504` | Potentially temporary; retry safe reads with bounds/backoff. |
| `422` | Validation, limit, or business-rule failure; inspect `errors` and correct the payload. |

For writes, a read timeout is ambiguous: the server may have accepted the mutation. Do not repeat a non-idempotent write until its result is checked through a read, async-job status, or a client-generated idempotency mechanism documented by GGSEL.

## 8. Complete V1 endpoint catalog (17 operations)

The status descriptions below preserve the official English page wording, including awkward labels.

| Area | Method and path | Documented responses | Purpose/source |
|---|---|---|---|
| Account | `GET /api_sellers/api/sellers/account/balance/info` | `200` return balance info | [Balance](https://seller.ggsel.com/docs/en/return-seller-balance-info) |
| Account | `GET /api_sellers/api/sellers/account/receipts` | `200` return receipts info; `401` Unauthorized | [Receipts](https://seller.ggsel.com/docs/en/return-seller-receipts) |
| Auth | `POST /api_sellers/api/apilogin` | `200` return token info | [Seller token](https://seller.ggsel.com/docs/en/return-seller-token) |
| Categories | `GET /api_sellers/api/categories` | `200` return active category; `401` Unauthorized | [Categories](https://seller.ggsel.com/docs/en/return-all-categories) |
| Chats | `GET /api_sellers/api/debates/v2/chats` | `200` return unread list | [Chats](https://seller.ggsel.com/docs/en/list-of-chats) |
| Chats | `GET /api_sellers/api/debates/v2` | `200` return list; `400` return error | [Messages](https://seller.ggsel.com/docs/en/list-of-messages) |
| Chats | `POST /api_sellers/api/debates/v2` | `200` return status; `400`/`422` invalid parameters | [Create message](https://seller.ggsel.com/docs/en/create-message-without-file) |
| Products | `POST /api_sellers/api/product/edit/prices` | `200` Bulk update; `401` Unauthorized | [Bulk price update](https://seller.ggsel.com/docs/en/updates-prices-of-products-and-variants-in-bulk) |
| Products | `GET /api_sellers/api/product/edit/UpdateProductsTaskStatus` | `200` Bulk update; `401` Unauthorized | [Bulk-update job status](https://seller.ggsel.com/docs/en/get-status-of-async-job) |
| Products | `GET /api_sellers/api/products/list` | `200` shop products | [All products](https://seller.ggsel.com/docs/en/return-all-products) |
| Products | `GET /api_sellers/api/products/:product_id/data` | `200` product info; `401` labeled bad request | [Product info](https://seller.ggsel.com/docs/en/return-product-info) |
| Products | `POST /api_sellers/xml/shop_search.asp` | `200` return search products | [Product search](https://seller.ggsel.com/docs/en/return-all-products-based-on-search-results) |
| Products | `POST /api_sellers/api/seller-goods` | `200` return all products | [Seller products](https://seller.ggsel.com/docs/en/return-all-products-for-seller) |
| Orders | `GET /api_sellers/api/seller-last-sales` | `200` sales ordered by `payment_date desc` | [Last sales](https://seller.ggsel.com/docs/en/return-last-sales) |
| Orders | `GET /api_sellers/api/purchase/info/:invoice_id` | `200` order info; `404` order not found | [Order info](https://seller.ggsel.com/docs/en/get-order-info) |
| Orders | `GET /api_sellers/api/purchases/unique-code/:unique_code` | `200`; official description appears incorrectly copied as “return all products” | [Unique code](https://seller.ggsel.com/docs/en/check-unique-code) |
| Reviews | `GET /api_sellers/api/reviews` | `200` return reviews | [Reviews](https://seller.ggsel.com/docs/en/return-user-reviews) |

### 8.1 Repository-confirmed V1 request parameters

These are proven by [`ggsel_api.py`](ggsel_api.py), not reconstructed from broken widgets:

| Operation | Parameters/body used by this repository |
|---|---|
| List chats | Query: `token`, `pagesize`, `page`; optional `filter_new`, `email`, `id_ds` |
| List messages | Query: `token`, `id_i` (positive chat ID) |
| Create message | Query: `token`, `id_i`; JSON body contains both `message` and `text` with the same cleaned text |
| Last sales | Query: `token`, `top`; headers include `locale: ru` |
| Balance | Query: `token` |
| Order info | Path `invoice_id`; query `token`; header `locale: ru` |
| Reviews | Query: `token`, `type`, `page`, `count`; optional `product_id`; header `locale: ru-RU` |
| Product info | Path `product_id`; query `token` |

Repository validation limits:

- `pagesize`: 1–1000.
- `page`: at least 1.
- `top`: 1–1000.
- Review `count`: 1–100.
- Message text: non-blank after control-character cleanup, truncated to 4000 characters.

### 8.2 Official V1 bulk-price write shape

`POST /product/edit/prices` accepts an `application/json` array:

```json
[
  {
    "product_id": 102615259,
    "price": 166,
    "variants": [
      {
        "variant_id": 123,
        "rate": 5,
        "type": "percentplus"
      }
    ]
  }
]
```

`variants` is optional. Documented variant `type` values are `percentplus`, `percentminus`, `priceminus`, and `priceplus`. A successful submission returns `taskId`; check it through `GET /product/edit/UpdateProductsTaskStatus` before read-back verification. Exact batch limits, rounding increments, and the status endpoint's query parameter names remain unresolved, so this repository does not implement the V1 write.

## 9. Complete V2 endpoint catalog (24 operations)

### 9.1 Async jobs and categories

| Method and path | Documented responses | Purpose/source |
|---|---|---|
| `GET /api_sellers/v2/async_job_results/:job_id` | `200` result; `401` Unauthorized; `404` Not found | [Async job result](https://seller.ggsel.com/docs/en/v2/get-async-job-result) |
| `GET /api_sellers/v2/categories` | `200` categories; `401` Unauthorized | [List categories](https://seller.ggsel.com/docs/en/v2/list-of-categories) |
| `GET /api_sellers/v2/categories/search` | `200` search categories; `401` Unauthorized | [Search categories](https://seller.ggsel.com/docs/en/v2/search-categories) |

### 9.2 Offers

| Method and path | Documented responses | Purpose/source |
|---|---|---|
| `GET /api_sellers/v2/offers` | `200` list; `401` Unauthorized | [List offers](https://seller.ggsel.com/docs/en/v2/list-offers) |
| `POST /api_sellers/v2/offers` | `200` created; `401` Unauthorized; `422` Unprocessable content | [Create offer](https://seller.ggsel.com/docs/en/v2/create-offer) |
| `GET /api_sellers/v2/offers/:id` | `200` offer; `401` Unauthorized; `404` Not found | [Get offer](https://seller.ggsel.com/docs/en/v2/get-offer) |
| `PATCH /api_sellers/v2/offers/:id` | `200` updated; `401` Unauthorized; `404` Not found; `422` Unprocessable content | [Patch offer](https://seller.ggsel.com/docs/en/v2/patch-offer) |
| `POST /api_sellers/v2/offers/batch_activate` | `200` job enqueued; `400` empty `offer_ids`; `401`; `422` too many items | [Batch activate](https://seller.ggsel.com/docs/en/v2/batch-activate-offers) |
| `POST /api_sellers/v2/offers/batch_pause` | `200` job enqueued; `400` empty `offer_ids`; `401`; `422` too many items | [Batch pause](https://seller.ggsel.com/docs/en/v2/batch-pause-offers) |
| `POST /api_sellers/v2/offers/batch_delete` | `200` job enqueued; `400` empty `offer_ids`; `401`; `422` too many items | [Batch delete](https://seller.ggsel.com/docs/en/v2/batch-delete-offers) |

### 9.3 Options and variants

| Method and path | Documented responses | Purpose/source |
|---|---|---|
| `GET /api_sellers/v2/offers/:offer_id/options` | `200` visible options; `401`; `404` | [List options](https://seller.ggsel.com/docs/en/v2/list-offer-options-visible-to-seller) |
| `POST /api_sellers/v2/offers/:offer_id/options` | `200` bulk success; `401`; `404`; `422` | [Create many options](https://seller.ggsel.com/docs/en/v2/create-many) |
| `DELETE /api_sellers/v2/offers/:offer_id/options` | `200` archived; `400`; `401`; `404`; `422` too many items | [Archive options](https://seller.ggsel.com/docs/en/v2/archive-options) |
| `PATCH /api_sellers/v2/offers/:offer_id/options/batch_visibility` | `200` idempotent request; `400`; `401`; `404`; `422` | [Option visibility](https://seller.ggsel.com/docs/en/v2/batch-update-options-visibility) |
| `GET /api_sellers/v2/offers/:offer_id/options/:id` | `200` option; `401`; `404` | [View option](https://seller.ggsel.com/docs/en/v2/view-option) |
| `POST /api_sellers/v2/offers/:offer_id/options/:option_id/variants` | `200` all created/updated; `401`; `404`; `422` | [Create/update variants](https://seller.ggsel.com/docs/en/v2/create-or-update-variants) |
| `DELETE /api_sellers/v2/offers/:offer_id/options/:option_id/variants` | `200` job created; `400`; `401`; `404`; `422` too many items | [Archive variants](https://seller.ggsel.com/docs/en/v2/archive-option-variants-asynchronously) |
| `PATCH /api_sellers/v2/offers/:offer_id/options/:option_id/variants/batch_visibility` | `200` idempotent request; `400`; `401`; `404`; `422` | [Variant visibility](https://seller.ggsel.com/docs/en/v2/batch-update-variants-visibility) |

### 9.4 Products and split products

| Method and path | Documented responses | Purpose/source |
|---|---|---|
| `GET /api_sellers/v2/offers/:offer_id/products` | `200` success; `401` | [List products](https://seller.ggsel.com/docs/en/v2/list-products) |
| `POST /api_sellers/v2/offers/:offer_id/products` | `204` success; `401`; `404`; `422` | [Create products](https://seller.ggsel.com/docs/en/v2/create-products) |
| `DELETE /api_sellers/v2/offers/:offer_id/products` | `200` archive enqueued; `400`; `401`; `404`; `422` products not allowed | [Archive products](https://seller.ggsel.com/docs/en/v2/archive-products) |
| `GET /api_sellers/v2/offers/:offer_id/variants/:variant_id/splitted_products` | `200` success; `401`; `404` | [List split products](https://seller.ggsel.com/docs/en/v2/list-splitted-products) |
| `POST /api_sellers/v2/offers/:offer_id/variants/:variant_id/splitted_products` | `204` success; `401`; `404`; `422` | [Create split products](https://seller.ggsel.com/docs/en/v2/create-splitted-products) |
| `DELETE /api_sellers/v2/offers/:offer_id/variants/:variant_id/splitted_products` | `200` archive enqueued; `400`; `401`; `404`; `422` too many items | [Archive split products](https://seller.ggsel.com/docs/en/v2/archive-splitted-products) |

## 10. V1 schema reference

Several V1 English field descriptions were corrupted in the official generated HTML. The field names, types, nullability, examples, and nesting below were still readable. Unknown semantics are not guessed.

### 10.1 Balance

`balance_object`:

- `retval: integer`
- `retdesc: string`
- `errors: string[] | null`
- `content.amount_t_lock: number | null`
- `content.amount_t_free: number | null`
- `content.amount_t_plus: number | null`

The official text labels these values with legacy `WMT` terminology. Confirm actual currency semantics in the seller account before financial use.

### 10.2 Receipts

`receipts_object`:

- Envelope: `retval`, `retdesc`, nullable `errors`.
- Pagination: `page`, `count`, `has_next_page`, `has_previous_page`, `total_count`, `total_pages`.
- `items[]`:
  - `account_operation_id: integer`
  - `operation`: `id`, `type`, `datetime`, `percent`, `price`, `currency` (documented default `WMT`), `on_account`
  - `owner_id: integer`
  - `product`: `id`, localized `name[]` entries (`locale`, `value`), `deleted`
  - Nullable `code_check_datetime`, `date_free`, `free_description`, `response`

### 10.3 Categories

`categories_object`:

- `retval: integer`
- `retdesc: string`
- `category[]`: `id: integer`, `name: string`, `sub: integer[]`, nullable `cnt: integer`

### 10.4 Product lists

`offer_list_object` includes `retval`, `retdesc`, pagination (`page`, `count`, next/previous flags, totals), and `rows[]`.

Each row can include:

- Identity/content: `id_goods`, `name_goods`, `info_goods`, nullable `add_info`.
- Price: string `price`, `currency`; numeric `price_usd`, `price_eur`, nullable `price_uah`, `price_rur`.
- Counts: `cnt_sell`, `cnt_return`, `cnt_goodresponses`, `cnt_badresponses`, `in_stock`, `num_in_stock`, `num_options`.
- Visibility/commerce: `visible`, nullable `commiss_agent`, nullable `has_discount`.
- `sale_info`: nullable `common_base_price`, `common_price_usd`, `common_price_eur`, `common_price_rur`, `sale_end`, `sale_percent`.

`seller_goods_list_object` uses the same row shape and adds seller/page metadata: `id_seller`, `name_seller`, `cnt_goods`, `pages`, `page`, `order_col`, and `order_dir`.

`offer_search_object` includes:

- `retval`, `retdesc`.
- `pages`: `name`, `rows` (the official `name: integer` appears unusual; verify observed data).
- `products[]`: `id`, `name`, `price`, `snippets.info`, `snippets.name`, sale-price fields, and nullable `sale_percent`.

### 10.5 Product detail

`offer_object` contains `retval`, nullable `retdesc`, and `product`.

Important product fields:

- IDs/navigation: nullable `id`, `id_prev`, `id_next`.
- Display: nullable `name`, `url`, `info`, `add_info`, `release_date`, `collection`, `type`, `text`, `file`, `category_id`.
- Base price: nullable `price`, `currency`.
- Currency prices: `prices.initial` and `prices.default`, each with nullable numeric `RUB`, `USD`, and `EUR`.
- Availability: `is_available`, `show_rest`, `num_in_stock`, `num_in_lock`.
- Agency: `agency_fee`, `agency_sum`, `agency_id`, `gift_commiss`.
- `payment_methods[]`: method `name`; `currencies[]` with `currency`, `code`, `price`, and `limit.min`/`limit.max`.
- `prices_unit`: unit name/amount/currency/count, min/max, descriptions, `unit_fixed`, `unit_only_int`.
- Media: `preview_imgs[]` and nullable `preview_videos[]`, each with URL/width/height.
- `breadcrumbs[]`: category `id`, `name`.
- `options[]`: option identifiers/labels/type/flags and nested variants with value/text/default/price-modifier/stock/visibility fields.
- `statistics`: `sales`, `refunds`, `good_reviews`, `bad_reviews`.
- `seller`: `id`, `name`.
- `sale_info`: common base/currency prices and sale end/percent.

The dry-run parser accepts either direct `currency: RUB` plus `price`, or nested `prices.default.RUB` / `prices.initial.RUB`. It rejects missing, non-positive, non-finite, or non-RUB direct prices.

### 10.6 Chats and messages

`chats_object`:

- `cnt_pages: integer`
- `items[]`:
  - `id_i: integer` — conversation ID
  - `email: string` — buyer email
  - `product: integer` — product ID
  - `last_message: string<date_time>`
  - Nullable `cnt_msg`, `cnt_new`

`messages_object` is an array of:

- `id: integer`
- `message: string`
- `buyer: integer`
- `seller: integer`
- Nullable `deleted: integer`
- `date_written: string<date_time>`
- Nullable `date_seen: string<date_time>`
- Nullable file/media fields: `is_file`, `filename`, `url`, `is_img`, `preview`

Buyer email and message attachments are sensitive. Avoid retaining or logging them unless required.

### 10.7 Last sales

`last_sales_object`:

- `retval`, `retdesc`.
- `sales[]`: `invoice_id`, `date`, and `product`.
- Product: `id`, `name`, `price_rub`, `price_usd`, `price_eur`, nullable `price_uah`.

### 10.8 Reviews

`reviews_object`:

- `retval`, `retdesc`.
- Totals: `totalPages`, `totalItems`, `totalGood`, `totalBad`.
- `reviews[]`: `id`, `info`, `good`, `type`, `date`, numeric `invoice_id`, `name`, nullable `comment`, `owner_id`.

### 10.9 Unique-code result

`unique_code_object` includes:

- Envelope: `retval`, `retdesc`.
- Sale fields: `inv`, `id_goods`, `amount`, `type_curr`, `amount_usd`, `profit`, `date_pay`.
- Buyer/order metadata: nullable `email`, `lang`, `agent_id`, `agent_percent`, `query_string`, `unit_goods`, `cnt_goods`, `promo_code`, `bonus_code`, `cart_uid`; plus `name_invoice`.
- `unique_code_state`: `state`, nullable `date_check`, `date_delivery`, `date_confirmed`, `date_refuted`.
- `options[]`: nullable `id`, `name`, `value`, `variant_id`.

Treat the unique code and buyer fields as secrets.

### 10.10 Order info

`info_order` has `retval`, `retdesc`, and `content`:

- IDs: nullable `item_id`, required-by-schema `content_id`, nullable `cart_uid`, nullable `external_order_id`.
- Product/amount: nullable `name`; `amount: number<float>`; `currency_type` (default `USD`).
- State/time: `invoice_state`; `purchase_date`; nullable `date_pay`.
- Agent/fees: nullable `agent_id`, `agent_percent`; `agent_fee` default `0`; `profit`.
- Input/codes: nullable `query_string`, `unit_goods`, `cnt_goods`, `promo_code`, `bonus_code`.
- Nullable `feedback`: `deleted`, `feedback`, `feedback_type` (`positive` or `negative`), nullable `comment`.
- `unique_code_state`: nullable state and check/delivery/confirmation/refutation timestamps.
- `options[]`: `id`, nullable `name`, `user_data`, nullable `user_data_id`.
- `buyer_info`: `payment_method`, `account`, nullable `email`, `phone`, `skype`, `whatsapp`, `ip_address`, plus `payment_aggregator`.
- `owner`, `day_lock` (default `0`), `lock_state` (default `free`).

The official English description of numeric `invoice_state` values was corrupted. Do not map integer states to business labels without a working official source or observed evidence.

## 11. V2 schema reference

Official schema URL pattern:

```text
https://seller.ggsel.com/docs/en/v2/schemas/<schema-name-with-hyphens>
```

### 11.1 Pagination and async jobs

`pagination_object`:

- `page: integer`
- `limit: integer`
- `has_next_page: boolean`
- `has_previous_page: boolean`
- Nullable deprecated `total_pages` and `total_count`; navigate using the boolean flags.

`async_job_result_object`:

- Required `job_id: string`.
- Required `status`: `pending`, `completed`, or `failed`.
- Required `results: object`; contents depend on operation type.

Polling policy:

1. Save `job_id` durably if loss would make mutation status unknowable.
2. Poll with bounded backoff, not a tight loop.
3. Stop at `completed` or `failed`.
4. Treat unknown future status values as non-success.
5. Inspect operation-specific `results` and verify the mutated resource with a read.

### 11.2 Category

`category_object`:

- `id: integer` — compatible with V1 category IDs.
- `title: string` — localized category name.
- `content_type: string`.
- `fee: number` — category commission percentage as decimal.
- `payment_fee: number` — payment commission percentage as decimal.
- `tree: string` — full category path.
- `has_children: boolean`.

For margin calculations, verify whether “as decimal” means percentage points or fractional rate in actual API values before applying it. Never silently combine it with a hard-coded multiplier.

### 11.3 Create-offer request

`create_offer_request_object` fields:

- Text: `title_ru`, `title_en`, `description_ru`, `description_en`, `instructions_ru`, `instructions_en`.
- Images: `cover_image_ru` Base64; nullable `cover_image_en` Base64.
- `price: number`.
- Nullable `currency`; only `RUB` is allowed.
- Nullable `is_autoselling`, default `false`.
- `category_id: integer`, compatible with V1.
- Nullable `min_quantity` and `max_quantity`, each default `1`.
- Nullable `quantity`.
- Nullable `is_unlimited_quantity`, default `false`.
- Nullable `post_payment_url`, default empty string.
- Nullable `delivery`: `auto` or `manual`, default `auto`.
- Nullable `pre_payment_settings` and `notification_settings` described below.

### 11.3.1 Patch-offer request

The official `update_offer_request_object` is an `application/json` partial-update schema. Its top-level `price` property is a number, so the authoritative minimum price-only request is:

```json
{
  "price": 166
}
```

Use the public positive offer ID in `PATCH /offers/:id` and send the raw V2 API key in `Authorization` without a `Bearer` prefix. HTTP `200` returns the updated offer under `data`; independently verify the price through `GET /offers/:id`.

### 11.4 Offer response/list objects

Core `offer_object` fields:

- `id: integer`.
- `status`: `draft`, `active`, `paused`, `archived`.
- `is_autoselling: boolean`.
- Nullable `delivery`: `auto` or `manual`, default `auto`.
- RU/EN title, description, instructions, and cover-image URLs.
- Nested `category` including `fee` and `payment_fee`.
- `price: number`; `currency` is always `RUB`.
- Quantity: `quantity`, `is_unlimited_quantity`, nullable `min_quantity`, nullable `max_quantity`.
- Sales/stock counts for base and split products.
- `has_splitted_products: boolean`.
- `updated_at`, `created_at` documented in MSK time.
- Nullable `post_payment_url`, pre-payment settings, and notification settings.

`list_offer_object` additionally exposes nullable summary fields and booleans `has_options`, `has_products`, and `has_splitted_products`.

Do not parse MSK timestamps as UTC unless an explicit offset proves it.

### 11.5 Pre-payment and notification settings

`pre_payment_settings_request_object`:

- `is_enabled: boolean`, default `false`.
- Nullable `url: string`.
- `allow_payment: boolean`, default `true`, controlling payment when validation fails.

`offer_notifications_request_object`:

- Nullable `type`: `email` or `url`, default `email`.
- Nullable `url`, nullable `email`.
- Nullable `http_method`: `GET` or `POST`.
- `is_disabled: boolean`, default `false`.
- `is_default: boolean`, default `true`; official description says default mode sends only email.

URLs are SSRF-sensitive configuration. Validate scheme and destination according to deployment policy before accepting untrusted input.

### 11.6 Options

Option types:

- `text`
- `multiline_text`
- `check_box`
- `radio_button`

Full option statuses: `active`, `archived`, `hidden`. List-visible statuses may only show `active` and `hidden`.

`bulk_options_request_object` wraps `options[]`. Each option can contain:

- Optional `id`; absence creates a record.
- `type`, `status`, `has_splitted_products`.
- `title_ru`, `title_en`, `comment_ru`, nullable `comment_en`.
- `is_required`, default `false`.
- `is_price_modifier_hidden`, default `false`.
- `position`, default `0`, ascending.

`single_option_request_body_object` has the same item fields without the `options` wrapper.

`batch_options_visibility_request_object` requires:

```json
{
  "options": [
    {"id": 123, "status": "active"}
  ]
}
```

Each entry requires `id` and `status`; status is `active` or `hidden`.

`option_object` adds `id`, nested `variants[]`, stock/sales counts, and `has_splitted_products`.

### 11.7 Variants

`bulk_variants_request_object` wraps `variants[]`. Each variant can contain:

- Optional `id`; absence creates a record.
- `title_ru`, `title_en`.
- `price: number`.
- `discount_type`: `fixed` or `percent`.
- `impact_type`: `increase` or `decrease`.
- `is_default: boolean`, default `false`.
- `status`: `active`, `archived`, or `hidden`.
- `position: integer`, default `0`, ascending.

`variant_request_object` is the unwrapped item schema.

`batch_variants_visibility_request_object` requires:

```json
{
  "variants": [
    {"id": 456, "status": "hidden"}
  ]
}
```

`option_variant_list_object` uses visible statuses `active`/`hidden`. Full `variant_object` also exposes `in_stock_products_count` and `sold_products_count`.

A variant `price` is a modifier governed by `discount_type` and `impact_type`; do not assume it is the offer's final absolute sale price.

### 11.8 Inventory products

`products_request_object`:

```json
{
  "products": [
    {"value": "product content"}
  ]
}
```

`product_object`:

- `id: integer`
- `value: string` — sensitive delivered content
- `status`: `in_stock`, `sold`, or `archived`
- `created_at: string`

`archive_products_request_object`:

- `product_ids: integer[]`
- `delete_all: string`, documented values `"true"` or `"false"` (not a JSON boolean in the retrieved schema)

Never log `value`. Archiving stock is destructive; require explicit scope and do not default `delete_all` to true.

### 11.9 Offer lifecycle batches

`batch_offer_ids_request_object`:

```json
{
  "offer_ids": [123456, 234567, 345678]
}
```

- `offer_ids` is required.
- Maximum documented batch size: 100.
- Empty arrays produce `400`; too many items produce `422`.
- Activate, pause, and delete enqueue jobs; poll the V2 async-job endpoint.

## 12. Safe implementation recipes

### 12.1 V1 authenticated read

```text
validate input
→ create current timestamp/signature if no valid token
→ POST /apilogin
→ extract non-empty token
→ send GET with token query parameter and timeout
→ on 401/403: clear token, login once, repeat once
→ parse JSON and check retval where present
→ validate expected shape before use
```

### 12.2 V2 read/list

```text
validate IDs and pagination
→ send Authorization header and Accept: application/json
→ parse 2xx response
→ use has_next_page/has_previous_page for pagination
→ handle 401 as key/permission failure
→ handle 404 as missing resource
```

### 12.3 V2 async mutation

```text
validate payload and batch bounds
→ show/log a redacted dry-run plan
→ require explicit approval/feature flag
→ submit once
→ persist returned job ID
→ poll /async_job_results/:job_id with bounded backoff
→ require status=completed
→ verify affected resources through GET
→ alert and stop on failed/unknown status
```

### 12.4 Sending a buyer message

Message POSTs are non-idempotent. A read timeout may happen after acceptance, so blindly retrying can duplicate the message. This repository treats an ambiguous read timeout as delivered. Other transport failures are classified and queued only when duplication risk is controlled.

### 12.5 Repricing

Confirmed API capabilities:

- V1 bulk price write: `POST /api_sellers/api/product/edit/prices`.
- V1 async status: `GET /api_sellers/api/product/edit/UpdateProductsTaskStatus`.
- V2 per-offer write: `PATCH /api_sellers/v2/offers/:id`.
- V2 verification: `GET /api_sellers/v2/offers/:id`.

Safe proposal formula:

```text
proposed_rub = ceil_to_allowed_increment(
    (target_net_usd * usd_rub_rate + fixed_rub_costs)
    / (1 - total_percentage_fees / 100)
)
```

Safety gates:

1. Explicit product/offer allowlist and target USD values.
2. Fresh, timezone-aware exchange-rate timestamp.
3. Plausible rate range and positive finite numbers.
4. Verified category/payment/other fee semantics.
5. Current RUB price read successfully.
6. Maximum percentage-change limit.
7. Absolute minimum price if business policy requires it.
8. Loss-safe rounding to GGSEL's documented increment.
9. Dry-run logs reviewed across multiple rate updates.
10. Known write request schema and one-product canary.
11. Async completion and read-back verification.
12. Kill switch and alerting.

The current project exposes a disabled-by-default V2 price-write method with an offer allowlist, maximum-change guard, zero write retries, and GET read-back verification. The scheduler has a separate guarded live mode: the server write gate and V2 key must be configured, targets must exist, and a user must confirm live mode in Telegram. Target edits revoke approval, while a persistent pre-write UTC checkpoint limits batches to at most once per 24 hours across restarts. No live PATCH or canary was used to develop or test this path.

Observed seller-UI behavior on 2026-08-30 is separate from the public Seller API:

- The website sent `PATCH /api/v1/offers/:id/update_price` with an `offer` object containing quantity fields and `price`.
- This is an internal, seller-session-authenticated route; do not call it with a public V1/V2 API key or treat its body as the public V2 patch schema.
- Its response contained private seller-UI row `id=2615258` and dashboard/catalog `ggsel_id=102615259`; do not interchange them.
- A read-only public test returned `404 NOT_FOUND` for `GET /api_sellers/v2/offers/2615258` and returned the active RUB offer for `GET /api_sellers/v2/offers/102615259`. Therefore, public V2 accepted the dashboard/catalog `ggsel_id` for this verified offer.

## 13. Retry, rate-limit, and concurrency policy

Recommended defaults used by this repository:

- Retry safe methods (`GET`, `HEAD`, `OPTIONS`) for connect failures and HTTP `408`, `429`, `500`, `502`, `503`, `504`.
- Do not automatically retry read timeouts after the full configured timeout; this prevents worker starvation.
- Do not automatically retry POST/PATCH/DELETE unless GGSEL documents idempotency or the result is independently verified.
- Honor numeric or HTTP-date `Retry-After`; this repository clamps cooldown to 1–3600 seconds.
- Keep a bounded connection pool.
- Serialize or coordinate writes that must not race.
- Ensure only one token refresh occurs at a time.
- Pause dependent product reads during a known rate-limit cooldown.

## 14. Security and privacy checklist

- Store API keys/tokens only in environment variables or a secret manager.
- Copy [`.env.example`](.env.example) to `.env`, and keep `.env` uncommitted.
- Redact query strings because V1 tokens are query parameters.
- Do not log V2 `Authorization`.
- Do not log buyer email, account, phone, IP, chats, or attachments unless operationally required.
- Do not log inventory `product.value` or unique codes.
- Validate configured API origins as HTTPS without embedded credentials, query, or fragment.
- Restrict V2 keys to minimum required `api_sellers/v2` permissions.
- Validate notification/pre-payment URLs to prevent unsafe callbacks.
- Require explicit confirmation for archive/delete/batch lifecycle operations.
- Keep audit records for financial changes without storing secrets.

## 15. Repository implementation map

| Capability | Location | State |
|---|---|---|
| V1 client, login, retries, reads, buyer messages | [`ggsel_api.py`](ggsel_api.py) | Implemented |
| Configuration and validation | [`config.py`](config.py) | Implemented |
| Pure RUB calculation/current-price parsing | [`repricing.py`](repricing.py) | Implemented |
| CBR fetch, dry-run, and guarded daily live orchestration | [`bot_service.py`](bot_service.py) | Implemented |
| API hardening tests | [`tests/test_ggsel_api.py`](tests/test_ggsel_api.py) | Implemented |
| Repricing calculation and scheduler safety tests | [`tests/test_repricing.py`](tests/test_repricing.py), [`tests/test_sync_controls.py`](tests/test_sync_controls.py) | Implemented |
| V1 bulk price write | — | Intentionally absent |
| V2 offer list/detail reads and diagnostic | [`ggsel_api.py`](ggsel_api.py) | Implemented, read-only |
| Guarded V2 per-offer price PATCH | [`ggsel_api.py`](ggsel_api.py) | Implemented, disabled by default |

Current client methods:

- V1 login/token refresh.
- V1 list chats and messages.
- V1 send text messages.
- V1 read last sales, balance, order, reviews, and one product.
- V1 resolve/cache product names.
- V2 list and validate offers and read one offer by seller offer ID.
- V2 patch one allowlisted offer price and verify it with a subsequent GET.

Current client does not implement receipts, categories, V1 product listing/search, unique-code checks, V1 price writes/job status, or V2 mutations other than the guarded per-offer price PATCH.

## 16. Model task checklist

Before producing GGSEL code, answer internally:

- Which API version and why?
- What exact resource ID is being used?
- Is the method/path in this handbook?
- Is the authentication scheme correct for that version?
- Is the request shape official, repository-confirmed, or unresolved?
- Is this operation safe to retry?
- Does it return an async job?
- What validates transport success and application success?
- How is the result verified?
- Which secrets/PII must be redacted?
- What feature flag, dry run, max-change gate, or confirmation protects the write?
- What one small runnable test proves request construction and response parsing?

If any write request shape is unresolved, stop and retrieve authoritative evidence rather than inventing code.

## 17. Known documentation limitations and maintenance procedure

Observed limitations on 2026-08-30:

- Some endpoint request/response details render as skeletons.
- Referenced English documentation JavaScript assets returned `NoSuchKey`/404; current Russian assets exposed the V1 bulk-price and V2 patch schemas.
- Several V1 English schema descriptions are mojibake.
- Some official status descriptions appear copied from unrelated endpoints.
- V1 bulk-price batch limits, rounding increments, and job-status query parameters remain unresolved.

When updating this handbook:

1. Use the official English V1/V2 roots above.
2. Record retrieval date.
3. Preserve exact method/path/status data.
4. Link every endpoint/schema source.
5. Label observed behavior separately from official documentation.
6. Add redacted request/response examples only after validation.
7. Never commit credentials, tokens, buyer data, or inventory contents.
8. Run repository tests after changing client assumptions.
