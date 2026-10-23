-- Stage 1.6: protecting memory from poisoning.
-- trust: the record's trust, computed by the code from evidence (1.6.1); flags: what was cut out/flagged on write (1.6.2).
ALTER TABLE memories ADD COLUMN trust text NOT NULL DEFAULT 'trusted' CHECK (trust IN ('trusted','untrusted'));
ALTER TABLE memories ADD COLUMN flags text[] NOT NULL DEFAULT '{}';

-- existing records: trusted only with evidence from the owner or from the client's own work
UPDATE memories m SET trust = 'untrusted'
 WHERE NOT EXISTS (
   SELECT 1 FROM evidence e WHERE e.memory_id = m.id AND (
     e.speaker = 'owner'
     OR (split_part(e.source_scope, ':', 1) IN ('owner','dev','session','repo','file','import','cowork','code','claude')
         AND lower(e.source_ref) NOT LIKE 'http%')));

ALTER TABLE review_items DROP CONSTRAINT IF EXISTS review_items_type_check;
ALTER TABLE review_items ADD CONSTRAINT review_items_type_check
  CHECK (type IN ('contradiction','stale','slot_suggestion','merge_suggestion','promotion','rule_confirm'));
