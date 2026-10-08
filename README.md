# hylde
External Hydrus downloader.

## Development

- Install dependencies: `uv sync`
- Install Git hooks: `uv run pre-commit install`
- Run tests: `uv run pytest`
- Run type checks: `uv run ty check`
- Run locally: `uv run python hylde/server.py`

## Multipart archives

Multipart archive handling is controlled by `[multipart] enabled = true`.
When enabled, `7z`/`7zz`/`7za` must be available at startup.
Supported multipart parts currently include `*.zip.<number>` and
`*.part<number>.rar`. Incomplete-but-downloaded parts return HTTP 500 with
`Downloaded archive part; continue with remaining parts.` so Hydrus displays a
clear error message.

## POST pass-through

Hydrus parsers can only issue GET requests. `GET /post` forwards a request as
a POST (e.g. to a GraphQL API) and passes the upstream status, body and
`Content-Type` through unchanged.

| Parameter        | Description                                                        |
| ---------------- | ------------------------------------------------------------------ |
| `url`            | Upstream URL (required, http/https).                               |
| `body`           | Request body, base64url (Hydrus "base64url"; padding optional).    |
| `content_type`   | Request `Content-Type`, default `application/json`.                |
| `headers`        | Extra request headers as a base64url JSON object of strings.       |
| `var_<name>`     | Sets GraphQL variable `<name>` to the string value.                |
| `varjson_<name>` | Sets GraphQL variable `<name>` to the JSON-parsed value (`2`, …).  |

`var_*`/`varjson_*` are for GraphQL: they merge into the top-level `variables`
object of a JSON body, overriding existing keys.

Malformed requests return `400`, upstream connection errors or timeouts
(`maxtimeout`) return `502`. Standard base64 is also accepted.
Like all query parameters, `headers` (e.g. auth tokens) appears in access logs.

Example: a GraphQL query with an auth header and the `id` supplied per request.

```sh
# body: the query, with $id left as a variable
echo -n '{"query":"query($id:ID!){item(id:$id){title tags{name}}}"}' | base64 -w0
# eyJxdWVyeSI6InF1ZXJ5KCRpZDpJRCEpe2l0ZW0oaWQ6JGlkKXt0aXRsZSB0YWdze25hbWV9fX0ifQ==

# headers: JSON object of extra request headers
echo -n '{"Authorization":"Bearer TOKEN"}' | base64 -w0
# eyJBdXRob3JpemF0aW9uIjoiQmVhcmVyIFRPS0VOIn0=
```

```
http://localhost:5000/post?url=https://api.example.com/graphql
  &body=eyJxdWVyeSI6InF1ZXJ5KCRpZDpJRCEpe2l0ZW0oaWQ6JGlkKXt0aXRsZSB0YWdze25hbWV9fX0ifQ==
  &headers=eyJBdXRob3JpemF0aW9uIjoiQmVhcmVyIFRPS0VOIn0=
  &var_id=123
```

(Line breaks for readability only.) hylde POSTs
`{"query":"…","variables":{"id":"123"}}` with `Authorization: Bearer TOKEN`.
In Hydrus, prefer the string converter's "base64url" encoding, which is
URL-safe.

## Rendered pages

Hydrus parsers cannot run JavaScript. `GET /render?url=<page url>` returns a
page's HTML after JavaScript ran, rendered in a browser by a
[trawl](https://github.com/germondai/trawl) instance, which also solves
Cloudflare and similar challenges. The target's status code is passed through;
nothing is cached. Non-HTML responses (e.g. JSON APIs) are returned raw with
their original `Content-Type`.

Relative links (`href`, `src`, `srcset`, …) are made absolute against the
page's final URL, since Hydrus would resolve them against hylde's base url.
Paths inside scripts or JSON are not rewritten.

```toml
[render]
url = "http://trawl:8191"   # trawl instance
timeout = 20                # seconds trawl may spend per page
```

trawl's challenge handling waits at least 30 seconds, so a page with a
challenge or captcha widget can take ~40 seconds regardless of `timeout`; keep
`maxtimeout` above that. Missing or non-http(s) `url` returns `400`, a busy
trawl returns `429`, trawl failures and connection errors return `502`, and a
`maxtimeout` timeout returns `504`.

Hydrus links one parser per URL class, so give each site its own path:
`/render/<site>?url=…` behaves like `/render` and the `<site>` segment only
separates URL classes. In the site's URL class, set the API/redirect URL
converter to percent-encode the URL and prepend
`http://hylde:5000/render/<site>?url=`, then link the site's HTML parser to a
URL class matching that hylde URL.
