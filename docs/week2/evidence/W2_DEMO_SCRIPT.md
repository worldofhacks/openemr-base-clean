# Week 2 synthetic-only demo script (4-5 minutes)

Use one exact deployed SHA for web and worker. Keep raw documents, identifiers, tokens,
prompts, transcripts, and provider responses out of the recording.

| Time | Demonstration |
|---:|---|
| 0:00-0:25 | Show `/health` source SHA and `/ready`; identify hard versus soft probes. |
| 0:25-1:05 | Upload the synthetic lab PDF, poll bounded status, open its grounded extraction report, and click a bbox citation into the pinned page preview. |
| 1:05-1:40 | Upload the synthetic intake form twice; show one permanent document identity, eligible exactly-once vitals, and fresh source/artifact readback digests. |
| 1:40-2:10 | Upload a synthetic medication list with “source + grounded artifact only”; show that no MedicationRequest or vital write exists. |
| 2:10-2:40 | Ask the cited question; distinguish patient-record, uploaded-document, and guideline CitationV2 sources, then show critic approval before any answer bytes flush. |
| 2:40-3:05 | Open lab trends, show `6.5` remains distinct from `65`, and click a point to its existing page/bbox preview. |
| 3:05-3:35 | Run/show the real 50-case Tier-1 aggregate and approved Tier-2 aggregate with category arithmetic, cost, retries, and exact SHA. |
| 3:35-4:05 | Show a deliberate malformed-schema or incomplete-citation red gate, then return to the canonical green SHA. |
| 4:05-4:35 | Reconstruct one asynchronous path by correlation ID through queue, OCR/VLM, grounding, retrieval, writes/readback, critic, and terminal summary. |
| 4:35-5:00 | Show the Week 2 dashboard/alerts and finish on healthy readiness. |

If the final UI changes materially, recapture the video. Uploading or approving the final
recording and configuring alert destinations are owner actions.
