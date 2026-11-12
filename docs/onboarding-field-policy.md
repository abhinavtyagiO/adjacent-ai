# Onboarding field policy

An incomplete brand profile is a valid draft. Preserve all twelve section objects, using
null for unknown scalars and empty arrays for unknown collections. Enrichment should never
become an onboarding questionnaire or a reason to invent company information.

| Group | Requirement | Missing or unsupported content |
|---|---|---|
| Registered website | Required to register and crawl | Reject an invalid/private URL; fail if the website cannot be read |
| Company name | Required only to confirm | Keep a draft with a null name and let the user supply it |
| Offerings and creative context | Useful for future video generation, optional during onboarding | Keep verified names, descriptions, features, benefits, positioning, value propositions, CTAs, voice and audience insights; omit unsupported values |
| Trust and proof | Optional | Omit unsupported testimonials, customers, metrics, certifications, awards, guarantees and case studies |
| Corporate identity enrichment | Optional | Legal name, founding year, headquarters, mission, vision, regions and values can remain unknown |
| Detailed targeting | Optional | Personas, demographics, psychology, triggers and objections can remain empty; permitted interpretations still need grounded citations and an INFERRED label |
| Pricing and visual identity | Optional | Do not guess prices, currency, billing periods, logos, fonts or colors |
| Marketing guardrails/defaults | Optional at onboarding | Do not invent budgets, channels, preferences or legal requirements; omissions are not permission to make unsupported advertising claims |

Every populated generated leaf must retain valid evidence. Failed evidence checks remove
the unsupported content and add review warnings. This includes missing citations, unknown
URLs, fabricated quotes, disallowed inferences and duplicate field evidence. Unused/unknown
evidence is ignored with a warning, never reassigned to another field.

Collections are handled coherently:

* Drop a testimonial if its quote cannot be verified. Never leave an endorsement with only
  an attribution and no verified quote. Unsupported attribution fields become null.
* Drop an offering if its name cannot be verified. Other offering fields remain optional.
* Clear a pricing object if any populated pricing field fails evidence validation, avoiding
  an amount being combined with the wrong currency or billing period.
* Remove unsupported list entries and empty nested items. Rebuild provenance indexes to
  match the remaining entries exactly, including offerings and testimonials.

Users can review warnings, correct fields and confirm when name and website are present.
Unsupported content never becomes approved content automatically. Crawl failures, invalid
provider JSON/schema, credentials failures, database failures and concurrency conflicts still
produce explicit errors; optional enrichment does not bypass those checks.
