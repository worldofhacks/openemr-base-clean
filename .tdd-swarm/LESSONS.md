# TDD-Swarm lessons (epic E6)
- F-D.1 immunization defense is forward-looking: the six-tool set has no Immunization
  resource, so the declined-status trap is latent (ImmunizationClaim blocks at
  citation-resolution). Correct outcome, dead branch — wire + test the trap when an
  Immunization tool is added. Don't claim an active defense that no live path reaches.
- Greenfield modules RED as ImportError at collection is legitimate; verify prior suite
  still passes by --ignore-ing the new files, then confirm assertions bite once modules exist.
- Verified fields must be copied from the CITED RECORD, never the claim — the single most
  important implementation invariant for "the model cannot phrase past verification".
