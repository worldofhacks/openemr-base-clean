-- Conservative W2 extension: add the source+grounded-artifact-only medication list
-- document type.  No OpenEMR clinical-resource table or write leg is introduced.
BEGIN;

ALTER TABLE agent_document_dedup
    DROP CONSTRAINT IF EXISTS agent_document_dedup_doc_type_check;

ALTER TABLE agent_document_dedup
    ADD CONSTRAINT agent_document_dedup_doc_type_check
    CHECK (doc_type IN ('lab_pdf','intake_form','medication_list'));

COMMIT;
