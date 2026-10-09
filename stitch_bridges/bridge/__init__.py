"""Bridge core: shared guts for reverse-engineered web-AI providers.

A *bridge* turns a consumer web service (Notion agent, …) into an
OpenAI-compatible endpoint served on loopback by the owning service plugin.
The reusable parts live here: sticky browser-fingerprint sessions (curl_cffi),
an account pool with cooldown/weight/concurrency, and an SSE passthrough.
"""
