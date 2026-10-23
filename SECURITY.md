# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately through
[GitHub security advisories](https://github.com/datatim-hq/onfim/security/advisories/new) for this repository.

Include what you found, how to reproduce it and which version or commit you tested. We aim to acknowledge a report
within 3 working days and to agree on a fix and disclosure date with you.

## Supported versions

Onfim is pre-1.0. Security fixes go into the latest minor release only.

| Version | Supported |
|---|---|
| 0.1.x | yes |

## Scope notes

- A token with the `admin` right works only for direct requests: a request that carries `X-Real-IP` or
  `X-Forwarded-For` is treated as remote and gets no admin access. Keep the service behind a reverse proxy that sets
  one of these headers, and keep port 3120 bound to localhost. `/v1/_stand/*` works only with `MEM_STAND=1`.
- Records from untrusted sources are data, not instructions; a way to make an agent follow instructions stored in
  memory is in scope.
- Leaks across organizations, namespaces or classification levels are in scope.
