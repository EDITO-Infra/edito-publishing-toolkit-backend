# Publishing Service Integration

This package is EPT's outbound boundary to the external EDITO publishing API.

The default upstream OpenAPI document is available at <https://edito-publisher.vliz.be/openapi.json>. The endpoint can be changed with `PUBLISHING_API_URL`.

## Ownership

- `client.py` owns upstream paths, authentication headers, transport query
  parameters, HTTP execution, response validation, and event pagination.
- `models.py` contains transport DTOs that mirror only the upstream schemas used
  by EPT.
- `dependencies.py` constructs the gateway and submission credentials for
  FastAPI routes.
- `errors.py` defines publishing-specific infrastructure failures and sanitized
  public examples.

Feature/application policy does not belong here. The gateway knows upstream `view`, `limit`, `after_id`, and `next`; the public feature maps EPT's `view`, `limit`, and `cursor` query into those transport parameters.

## Request flow

```text
EPT route
  -> validate the public view/limit/cursor query
  -> feature service requests one summary or event page
  -> PublishingServiceClient maps to upstream view/limit/after_id
  -> external publishing API
  -> publishing transport DTO validation
  -> feature service maps to the stable EPT response
```

`PublishingServiceClient.iter_job_events()` is an infrastructure utility that follows every upstream continuation, rejects repeated or invalid cursors, enforces a maximum page count, and removes duplicate event IDs at page boundaries. The current public endpoint returns one page at a time; clients follow the stable EPT `next` link when exporting all events.

## Model policy

Publishing transport models are infrastructure-only. Routes never return them
directly. Request DTOs reject unknown fields; response DTOs retain additive
fields while validating fields EPT depends on.

The upstream job endpoint has three distinct response DTOs:

- `PublishingServiceJobSummary`
- `PublishingServiceJobDetailResponse`
- `PublishingServiceJobRawResponse`

These are not nested public EPT response models. The feature maps them into one
`GetPublishJobResponse`, where `id` is always present and log-page fields appear
together only for detail/raw mode.

## Contract tests

Gateway behavior tests live in `tests/infrastructure/test_publishing_service.py`. A separately selected integration test reads the configured live `/openapi.json` document and verifies the operation IDs, paths, security, request defaults, response fields, and pagination parameters used by EPT.
