-- Spec 09: team memory. All new fields are optional, so existing clients behave as before.

-- TM-1: delegated client: a team agent service acts on behalf of a group member or the group itself
ALTER TABLE clients ADD COLUMN delegate_group text NULL REFERENCES groups(id);

-- TM-7: the speaker is a member of the organization (for trust and independence of confirmations)
ALTER TABLE evidence ADD COLUMN speaker_principal text NULL REFERENCES principals(id);

-- TM-19, TM-27, TM-35: namespace settings (promotion_auto, deep_sleep, purge_after)
ALTER TABLE namespaces ADD COLUMN settings jsonb NOT NULL DEFAULT '{}';

-- TM-12: profile team, a team namespace
ALTER TABLE namespaces DROP CONSTRAINT IF EXISTS namespaces_profile_check;
ALTER TABLE namespaces ADD CONSTRAINT namespaces_profile_check CHECK (profile IN ('work','assistant','team'));
UPDATE kinds SET profiles = array_append(profiles, 'team')
 WHERE id IN ('topic_fact','person_fact','insight','episode','commitment_mirror','decision','convention')
   AND NOT ('team' = ANY (profiles));

-- TM-32: CRM entity mirror: while the entity is open, strength does not decay (like commitment_mirror)
INSERT INTO kinds (id, description, profiles, rank_weight, half_life_days, forgettable, review_below)
VALUES ('crm_mirror', 'Зеркало сущности CRM (контакт, сделка, объект)', '{assistant,team}', 1.0, 14, true, NULL)
ON CONFLICT (id) DO NOTHING;

-- TM-22: who proposed a queue item (promotion between layers, a team rule)
ALTER TABLE review_items ADD COLUMN proposed_by text NULL;

-- TM-31: subject aliases per organization: person:tg123 -> crm:contact:77
CREATE TABLE subject_aliases (
  tenant_id  text NOT NULL REFERENCES tenants(id),
  alias      text NOT NULL,
  canonical  text NOT NULL,
  created_by text NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, alias)
);
CREATE INDEX subject_aliases_canonical ON subject_aliases (tenant_id, canonical);

-- audit log: new actions of spec 09
ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_check CHECK (action IN (
  'remember','add_evidence','supersede','revert_supersede','archive','unarchive','purge','mark_duplicate',
  'reinforce','job_run','review_decision','pin','unpin','close','promote',
  'grant','revoke','group_add','group_remove','namespace_create','principal_create',
  'promote_request','alias_set','offboard','forget_subject'));
