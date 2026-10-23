-- 08: organizations, principals, groups, grants, record classification.
-- Organizations, people and namespaces are created by `mem init`; the migration creates no initial rows.

CREATE TABLE tenants (
  id         text PRIMARY KEY,
  name       text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE principals (
  id            text PRIMARY KEY,
  tenant_id     text NOT NULL REFERENCES tenants(id),
  kind          text NOT NULL CHECK (kind IN ('user','agent','service')),
  display_name  text NOT NULL DEFAULT '',
  on_behalf_of  text NULL REFERENCES principals(id),
  clearance     smallint NOT NULL DEFAULT 1 CHECK (clearance BETWEEN 0 AND 3),
  active        bool NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CHECK ((kind = 'agent') = (on_behalf_of IS NOT NULL))
);

CREATE TABLE groups (
  id         text PRIMARY KEY,
  tenant_id  text NOT NULL REFERENCES tenants(id),
  parent_id  text NULL REFERENCES groups(id),
  name       text NOT NULL,
  kind       text NOT NULL DEFAULT 'team' CHECK (kind IN ('org_unit','team','project','role')),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE group_members (
  group_id     text NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  principal_id text NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
  role         text NOT NULL DEFAULT 'member' CHECK (role IN ('member','curator')),
  added_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (group_id, principal_id)
);

ALTER TABLE namespaces
  ADD COLUMN tenant_id text REFERENCES tenants(id),
  ADD COLUMN scope text NOT NULL DEFAULT 'personal' CHECK (scope IN ('personal','group','tenant')),
  ADD COLUMN owner_principal text NULL REFERENCES principals(id),
  ADD COLUMN owner_group text NULL REFERENCES groups(id),
  ADD COLUMN default_classification smallint NOT NULL DEFAULT 1 CHECK (default_classification BETWEEN 0 AND 3);

CREATE TABLE namespace_grants (
  id                 bigserial PRIMARY KEY,
  namespace          text NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,
  subject_type       text NOT NULL CHECK (subject_type IN ('principal','group','tenant')),
  subject_id         text NOT NULL,
  role               text NOT NULL CHECK (role IN ('reader','contributor','curator','owner')),
  max_classification smallint NOT NULL DEFAULT 1 CHECK (max_classification BETWEEN 0 AND 3),
  granted_by         text NOT NULL,
  granted_at         timestamptz NOT NULL DEFAULT now(),
  expires_at         timestamptz NULL,
  UNIQUE (namespace, subject_type, subject_id)
);

-- the namespace profile defines the allowed record kinds (kinds used to be tied to work/assistant)
ALTER TABLE namespaces ADD COLUMN profile text NOT NULL DEFAULT 'work' CHECK (profile IN ('work','assistant'));
ALTER TABLE kinds RENAME COLUMN namespaces TO profiles;

ALTER TABLE namespaces ALTER COLUMN tenant_id SET NOT NULL;

-- records
ALTER TABLE memories
  ADD COLUMN classification smallint NOT NULL DEFAULT 1 CHECK (classification BETWEEN 0 AND 3),
  ADD COLUMN promoted_from uuid NULL REFERENCES memories(id) ON DELETE SET NULL;
CREATE INDEX memories_ns_class ON memories (namespace, classification);

-- tokens belong to a principal; the token's rights/namespaces are a ceiling
ALTER TABLE clients ADD COLUMN principal_id text REFERENCES principals(id);
ALTER TABLE clients ALTER COLUMN principal_id SET NOT NULL;

-- audit
ALTER TABLE audit_log ADD COLUMN principal_id text NULL, ADD COLUMN on_behalf_of text NULL;
ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_check CHECK (action IN (
  'remember','add_evidence','supersede','revert_supersede','archive','unarchive','purge','mark_duplicate',
  'reinforce','job_run','review_decision','pin','unpin','close','promote',
  'grant','revoke','group_add','group_remove','namespace_create','principal_create'));

CREATE TABLE access_log (
  id           bigserial PRIMARY KEY,
  at           timestamptz NOT NULL,
  principal_id text NOT NULL,
  on_behalf_of text NULL,
  namespace    text NOT NULL,
  memory_ids   uuid[] NOT NULL
);
CREATE INDEX access_log_at ON access_log (at);

ALTER TABLE review_items DROP CONSTRAINT review_items_type_check;
ALTER TABLE review_items ADD CONSTRAINT review_items_type_check
  CHECK (type IN ('contradiction','stale','slot_suggestion','merge_suggestion','promotion'));
