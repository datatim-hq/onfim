-- Protection item 6 (AC-6): the database itself isolates memory namespaces. The service runs as role mem_app
-- (not the owner, not a superuser); the connection scope is the setting mem.ns ('*' or a comma-separated list).
-- An empty scope -> no rows (deny by default). A bug in the code cannot open another namespace.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mem_app') THEN
    CREATE ROLE mem_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

CREATE OR REPLACE FUNCTION mem_ns_ok(ns text) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('mem.ns', true), '') = '*'
      OR ns = ANY (string_to_array(nullif(current_setting('mem.ns', true), ''), ','))
$$;

ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ns_memories ON memories;
CREATE POLICY ns_memories ON memories USING (mem_ns_ok(namespace)) WITH CHECK (mem_ns_ok(namespace));
DROP POLICY IF EXISTS ns_evidence ON evidence;
CREATE POLICY ns_evidence ON evidence USING (EXISTS (SELECT 1 FROM memories m WHERE m.id = evidence.memory_id))
  WITH CHECK (EXISTS (SELECT 1 FROM memories m WHERE m.id = evidence.memory_id));
DROP POLICY IF EXISTS ns_memory_events ON memory_events;
CREATE POLICY ns_memory_events ON memory_events USING (EXISTS (SELECT 1 FROM memories m WHERE m.id = memory_events.memory_id))
  WITH CHECK (EXISTS (SELECT 1 FROM memories m WHERE m.id = memory_events.memory_id));
DROP POLICY IF EXISTS ns_review_items ON review_items;
CREATE POLICY ns_review_items ON review_items USING (mem_ns_ok(namespace)) WITH CHECK (mem_ns_ok(namespace));
