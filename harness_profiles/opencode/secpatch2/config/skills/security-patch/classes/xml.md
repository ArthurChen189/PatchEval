# XML external entities and entity expansion (CWE-611, 776, 827)

**Tuple.** Attacker-controlled XML (also SVG, XSLT, SOAP, office documents) is parsed by a parser that
resolves DTDs or external entities, or expands entities without limits.

## Complete fix
- Disable DTDs and external entities on **every** parser instance that handles untrusted XML: DOM, SAX,
  StAX, transformers, schema validators, unmarshallers, and XPath evaluators that load documents.
  - Java: set feature `http://apache.org/xml/features/disallow-doctype-decl` to `true`. Otherwise turn off
    external general and parameter entities and `load-external-dtd`, and set `XMLInputFactory.SUPPORT_DTD` to
    false.
  - Python: `defusedxml`; for lxml, `resolve_entities=False, no_network=True`.
  - .NET: `DtdProcessing.Prohibit`.
  - PHP: avoid `LIBXML_NOENT`.
  - Go: `encoding/xml` does not resolve external entities, but limit sizes.
- Make the hardening fail closed. If setting a feature throws, abort instead of continuing with defaults.
- Bound entity expansion and document size.

## Incomplete fixes
- `FEATURE_SECURE_PROCESSING` alone.
- Hardening a single factory while other code paths create their own parser, or accept a parser or reader
  from the caller.
- Catching and logging `setFeature` failures.
