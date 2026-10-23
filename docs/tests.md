# Test registry

Built by `python3 tools/test_catalog.py` from the test docstrings; do not edit by hand.

Tests: 163, slow: 0.

## `tests/integration/test_access.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_group_inheritance_and_no_grant` | A member of a child group reads the parent group's namespace, and a person with an expired grant gets 403 |  | — |
| `test_agent_never_exceeds_its_human` | An agent never gets more rights than its person and the token ceiling: finance is closed and it cannot confirm |  | — |
| `test_classification_filter` | A level 2 record is invisible to a person with clearance 1 in search, list and stats |  | — |
| `test_cannot_write_above_clearance` | A record with classification above the author's clearance is refused with 400 |  | — |
| `test_tenant_isolation` | Another organization is closed both ways, and a missing namespace answers with the same 403 as another one's |  | — |
| `test_export_owner_only` | Export from an outside address is available to the namespace owner; the curator gets 403 |  | — |
| `test_recall_layers` | Layered search covers only namespaces where the person has a valid grant |  | — |

## `tests/integration/test_api.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_no_evidence_400` | A record without evidence is refused with bad_request and is not stored |  | — |
| `test_no_kind_and_bad_kind_400` | a record without a kind, with an unknown kind or with a kind of another namespace is refused with 400 | M-L2 | — |
| `test_fields_stored_as_sent` | kind, subject, tags, importance and text are stored exactly as the client sent them | F-1, P1 | — |
| `test_quote_limit` | A quote longer than 200 characters is refused with 400 |  | — |
| `test_auth` | no token or a wrong token gives 401, another namespace 403 | M-A1, M-A2 | — |
| `test_admin_only_local` | The admin token works only from localhost: from an outside address, export gives 403 |  | — |
| `test_initial_strength_owner_bonus` | A record of importance 2 starts at strength 0.55, and at 0.65 from the owner's words |  | — |
| `test_independent_vs_repeat` | three independent pieces of evidence give more strength than one, five repeats less than one independent | M-S2, M-S3 | — |
| `test_decay_with_clock` | In 30 days an episode loses half its strength, while a decision, a pinned record and an open commitment do not decay |  | — |
| `test_used_helpful` | A used mark reinforces strength by 0.15 of the remainder to 1 and is written as an event in the record history |  | — |
| `test_slot_candidate_and_contested` | A new slot value from a third party's words becomes a candidate, not a replacement |  | — |
| `test_same_claim_in_slot_reinforces` | the same person repeated the same claim (the same text in the slot), a confirmation: the evidence is added to the current record, strength grows, no candidate; the same message again changes nothing | C-05 of the assistant spec 12 | — |
| `test_late_old_goes_to_history` | a late record into a slot with an earlier occurred_at goes straight to history; the current one stays | M-C2e | — |
| `test_explicit_supersede_history_revert` | an explicit supersedes replaces the record, history shows both, revert brings the old one back to current. supersedes pointing to a non-current record gives 400 | M-C2d | — |
| `test_slot_unique_constraint` | the database does not allow a second current record in a slot: the unique index fires | D-2 | — |
| `test_external_ref_idempotent` | writing again with the same external_ref does not create a record, it adds evidence to the existing one | A-3 | — |
| `test_archive_unarchive` | an archived record disappears from the list, and after unarchiving is current again with strength 0.30 | M-F3 | — |
| `test_purge_no_traces` | purge deletes the record with its evidence and events | M-F1, D-7, D-8 | — |
| `test_bulk_forget_requires_confirm` | bulk deletion requires a filter and by default only counts the records | M-F4, S-3 | — |
| `test_recall_tags_rare_token_explain_deterministic` | search ranks the record with a rare token first, returns the score breakdown and repeats the order | R-4 | — |
| `test_recall_evidence_and_audience` | search returns the record with its quote and speaker | M-E0, M-A3 | — |
| `test_recalled_top5_once_per_day` | The recalled_top5 event is written at most once a day, even if the record was returned three times |  | — |
| `test_status_and_stats` | Status with a wrong key gives 404; with the right key, database state, embedding dimension and counters |  | — |
| `test_schema_check_rejects_wrong_dim` | the service refuses to start if the embedding dimension in the database does not match the model | D-4 | — |
| `test_mcp_auth` | MCP without a token gives 401, another namespace 403; with its own token the full set of tools is available |  | — |
| `test_mcp_parity_and_tags` | search with tags through MCP and through REST gives the same results | P7, A-1, M-T1 | — |
| `test_import_dry_run_then_apply` | import by default only checks the records and names the faulty ones | 06 | — |

## `tests/integration/test_deep_sleep.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_contradiction_goes_to_owner` | deep sleep hands a contradiction without a slot to the owner: a queue item with two quotes and a slot | M-C6 | — |
| `test_review_requires_review_right_and_winner` | A contradiction decision without a winner gives 400, the queue without the review right 403 |  | — |
| `test_nothing_sensitive_goes_to_llm` | owner_only and level 3 records never go to the LLM, ordinary records of the same subject do | S-6 | — |
| `test_invalid_llm_answer_partial` | non-JSON from the LLM breaks only the contradictions stage: the run is PARTIAL with an alert | M-D3 | — |
| `test_cloud_model_for_sleep_is_refused` | a cloud model in the sleep settings (either of the two): every model stage fails with a clear error and an alert before anything is sent, since mem has no label dictionary and records would go out without masking | L-4 | — |
| `test_deep_sleep_without_model_never_calls_a_provider` | no deep-sleep model configured: the model stages are DISABLED, nothing is sent to any model and no alert is raised, even with an Anthropic key in the settings | 05 | — |
| `test_slot_suggestion_accept` | An accepted slot proposal links the records: the newer stays current, the older goes to history |  | — |
| `test_bad_indices_skip_item_not_stage` | The model returned a group with invalid numbers and the stage used to fail for all subjects |  | — |
| `test_stale_gotcha` | A weakened gotcha goes to the queue as "stale?" |  | — |
| `test_summary_passes_g2` | an episode summary that passes G2 is created as an insight with the evidence of all sources | M-D1 | — |
| `test_summary_distortion_rejected` | a summary with a distorted date is rejected by G2a and goes to the owner as a merge_suggestion | M-D2 | — |
| `test_commitments_never_summarized` | commitment mirrors are never summarized: no write_summary request even goes to the LLM | M-C7, G2d | — |
| `test_no_slots_for_episodes` | Episodes get no slot proposals: no suggest_slots request goes to the LLM and the queue is empty |  | — |
| `test_deep_dry_run_creates_nothing` | A dry run of the deep sleep counts the summary but does not create it |  | — |

## `tests/integration/test_init.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_init_creates_person_namespace_and_working_agent` | init: organization, person, personal namespace and agent; the agent token writes and finds a record; repeating is safe |  | — |
| `test_alert_goes_to_webhook` | with MEM_ALERT_WEBHOOK_URL an alert goes out as a POST with JSON {"text": ...}; a failed send does not break the call | P5 | — |
| `test_migrations_create_no_people_or_namespaces` | migrations create no organizations, principals, namespaces or grants; a new install starts with init | Stage 2 | — |
| `test_service_admin_is_created_once` | the service principal admin for MEM_ADMIN_TOKEN is in organization system; calling again changes nothing | Stage 2 | — |

## `tests/integration/test_poisoning.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_trusted_rule_is_current` | a rule from a trusted source is current and trusted at once and does not go to the queue | 1.6.1 | — |
| `test_web_rule_held_until_owner` | a rule from the web stays an untrusted candidate until the owner decides | 1.6.1 | — |
| `test_web_rule_owner_accepts` | after the owner confirms it, a gotcha from the web becomes current and trusted | 1.6.1 | — |
| `test_untrusted_fact_not_held` | Facts about people from chats stay as they were (current), only marked untrusted |  | — |
| `test_trusted_evidence_lifts_trust` | evidence from the owner's words makes an untrusted fact about a person trusted | 1.6.1 | — |
| `test_hidden_text_removed_and_flagged` | invisible characters are cut out of the text on save, and the record gets the flag removed:invisible | 1.6.2 | — |
| `test_secret_rejected` | a record with a key in the text or a quote is refused and nothing is stored | 1.6.3 | — |
| `test_forward_not_independent` | a forwarded message with the same text does not count as independent evidence | 1.6.4 | — |
| `test_copypaste_not_independent` | a copy-paste of someone else's text is not independent evidence, while another person's own words are | 1.6.4 | — |
| `test_kin_quotes_dont_replace_slot` | two "independent" quotes of one text are one source; no supersession | G1b-independent | — |

## `tests/integration/test_principals.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_same_id_in_other_tenant_is_conflict` | a principal with an id that already exists in another organization is a Conflict with both organizations in the text, not a silent clearance update; the existing principal does not change | 08 | — |
| `test_init_in_second_tenant_does_not_reach_first` | init of a second organization with the same person id fails with Conflict and issues no token; the token of the first install still works only in its own namespace | 08 | — |
| `test_other_owner_or_kind_is_conflict` | the same id with another on_behalf_of or another kind is a Conflict; kind and owner do not change | 08 | — |
| `test_repeat_with_same_identity_is_safe` | repeating with the same organization, kind and owner passes and updates clearance and name (re-running an install does not fail) | 08 | — |
| `test_on_behalf_of_must_exist_in_same_tenant` | an agent on behalf of a person from another organization or a missing person is a BadRequest; no principal is created | 08 | — |
| `test_namespace_of_other_tenant_or_owner_is_conflict` | a namespace with an id that already belongs to another organization or owner is a Conflict (otherwise the next grant would open it to someone else); repeating with the same parameters passes | 08 | — |
| `test_cli_error_is_message_and_exit_code` | a failed mem.cli command prints a message to stderr with exit code 1, not a traceback; an install script stops | 08 | — |
| `test_check_client_reports_rights_and_exit_code` | check-client with a token from stdin: rights in the namespace and exit code 0; a token of another organization: "no access" and exit code 1; an unknown token: exit code 1 | 08 | — |
| `test_forget_by_source_ref` | forget everything taken from one source fragment by the evidence `source_ref` (including records where it is only one of several evidence items); other records stay; dry run first, then confirm with the exact count; a list instead of a string is 400 | 05, S-3 | — |

## `tests/integration/test_rls.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_db_level_isolation` | the database itself limits rows to the namespaces in the request scope; without a scope nothing is visible | AC-6 | — |
| `test_api_scope_follows_token` | the assistant token gets no records of namespace work, neither by list nor by search | AC-6 | — |

## `tests/integration/test_sleep.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_g1_same_speaker` | if the speaker changed the time themselves, the light sleep makes the candidate current and the old record history | M-C2a | — |
| `test_g1_third_party_review_then_expire` | a replacement from a third party's words is not applied; after 14 days a contradiction appears | M-C2b | — |
| `test_g1_second_independent_source` | a second independent piece of evidence promotes the candidate with ground G1b-independent | M-C2c | — |
| `test_g1_owner_confirm` | the owner's confirmation promotes a candidate from a third party's words at the next sleep | G1b | — |
| `test_g1c_new_evidence_for_current_blocks` | new evidence for the current record stops the replacement, even from the same speaker | G1c | — |
| `test_slot_free_promotes` | if the slot's current record was archived, the candidate becomes current without confirmation | G1 | — |
| `test_dedup_exact` | an exact duplicate differing in case and spaces merges with the original | M-C1 | — |
| `test_dedup_respects_audience_and_slot` | the nightly merge of duplicates does not widen the audience or mix slots: same_chat records from different chats, same_chat and shareable, records of different slots stay separate; a duplicate from the same chat merges | M-C1 | — |
| `test_three_estimates_not_merged` | three estimates and a drawing for one person stay four current records; the light sleep does not merge them | M-C7 | — |
| `test_forget_episode_about_day_83` | an episode without reinforcement is archived on day 83-85; a decision and a pinned record stay | M-F2 | — |
| `test_used_keeps_alive` | a fact marked used every 20 days keeps strength above 0.5 for the whole model year; without events it drops below 0.2 | M-S4 | — |
| `test_used_recently_blocks_forget` | a weak episode is not archived until 30 days have passed since the last used; after that it is | G3d | — |
| `test_no_equalization_42_canaries` | over 60 nights the strength of 42 canaries does not level out and does not grow without events | M-S1, M-S5 | — |
| `test_retention_rules` | History older than 180 days is archived, the assistant archive older than a year is deleted, work has no auto-deletion |  | — |
| `test_honest_status_and_alerts` | an error in promote gives status FAILED, in dedup_exact PARTIAL, and every failure sends an alert | M-J1 | — |
| `test_idempotent_second_run` | a second light sleep run in a row changes nothing in any stage | M-J2 | — |
| `test_dry_run_changes_nothing` | A dry run of the sleep counts the changes but does not apply them; a normal run then promotes the candidate |  | — |
| `test_disabled_stage` | the forget stage disabled through meta does not run and shows as DISABLED in the report | L-6 | — |
| `test_sleep_endpoints` | A manual sleep run is available only to admin from localhost and is a dry run by default; the namespace client sees the report |  | — |

## `tests/integration/test_team.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_delegation_rules` | an agent acts only on behalf of a member or a group of its own organization | TM-1...TM-6 | — |
| `test_layers_isolation_matrix` | a member sees their personal layer and the layers of their groups, but not colleagues' personal ones | TM-4, TM-23, TM-S7 | — |
| `test_speaker_trust_and_independence` | a member's words are trusted, an outside participant's are not | TM-7...TM-11, TM-S6 | — |
| `test_team_rule_needs_curator` | a rule from a member waits for the layer's curator, while a rule from the curator applies at once | TM-11 | — |
| `test_team_rule_with_slot_waits_for_curator_overnight` | a member's rule with a slot does not take effect by itself overnight when the slot is empty; it waits for the curator (the hold used to look only at untrusted rules, and the light sleep promoted it as "the slot is free") | TM-11 | — |
| `test_team_rule_into_occupied_slot_waits_and_replaces_on_accept` | a member's rule into an occupied slot also waits for the curator (a rule_confirm item); gate G1 does not promote it at night; after acceptance it replaces the current one, with one current record in the slot (D-2) | TM-11 | — |
| `test_team_rule_duplicate_of_curator_rule_is_merged_and_item_closed` | a member proposed a rule (waiting for the curator), then the curator said the same; at night the duplicate merges into the curator's current rule, the duplicate's confirmation item closes, one current record in the slot | TM-11 | — |
| `test_team_rule_explicit_supersedes_waits_for_curator` | a member cannot replace the curator's rule with an explicit supersedes: their rule becomes a candidate with a confirmation item and the current one stays; after the curator accepts, the replacement happens (also for a rule without a slot) | TM-11 | — |
| `test_expired_rule_candidate_closes_its_item` | a rule candidate the curator never confirmed is archived after 30 days, and its confirmation item closes the same night (previously only contradiction items were closed) | TM-11 | — |
| `test_promotion_through_curator` | promoting a personal record to the team layer goes through the curator; the source record does not change | TM-13...TM-19 | — |
| `test_promotion_auto_and_rule_by_curator` | in a layer with promotion_auto for topic_fact the promotion applies at once, without the curator | TM-19 | — |
| `test_offboard` | a member who left loses access at once, their personal layer is closed to everyone, their words in the team stay | TM-26...TM-28 | — |
| `test_crm_mirror_aliases_and_forget_subject` | an open CRM deal mirror is trusted and does not decay; after closing it decays | TM-29...TM-33 | — |

## `tests/unit/test_alerts.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_alert_through_proxy` | MEM_TG_PROXY=socks5://tgproxy:1080 — the Bot API request goes through that SOCKS5 proxy | 05 | — |
| `test_no_proxy_direct` | without MEM_TG_PROXY the alert goes to Telegram directly | 05 | — |
| `test_bad_proxy_never_goes_direct` | a typo in MEM_TG_PROXY — the Telegram alert is not sent at all (only logged), never around the proxy | 05 | — |
| `test_webhook_does_not_use_telegram_proxy` | the webhook alert goes directly; the Telegram proxy applies only to Telegram | 05 | — |
| `test_send_failure_does_not_raise` | proxy unreachable — the alert does not break the sleep; the log has the error type, not the URL with the token | 05 | — |
| `test_socks_support_installed` | httpx has SOCKS support (socksio) installed, otherwise an alert through the proxy would fail on send | 05 | — |

## `tests/unit/test_docs.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_every_page_has_a_translation_and_links_resolve` | every user page exists in English and Russian with the same headings and code; all relative links and anchors resolve | #17 | skipif |
| `test_anchor_slugs_follow_github` | heading anchors are computed the way GitHub does, for Latin and Cyrillic headings | #17 | skipif |

## `tests/unit/test_english.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_comments_and_docstrings_are_english` | Comments and docstrings under mem/, tools/, tests/ and migrations/ have no Cyrillic; string data may | #17, code comments and docstrings are in English (CONTRIBUTING) | skipif |

## `tests/unit/test_gates.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_dates_formats` | Dates "30.09", "05.10.2026", "2026-09-15" and Russian "1 October" are extracted as (day, month) pairs |  | — |
| `test_numbers_ignore_dates` | Dates are not counted as numbers, and "2,50" becomes "2.5" |  | — |
| `test_g2_pass` | a summary whose dates, numbers and names all occur in the sources passes without violations | G2 | — |
| `test_g2_date_distortion` | a summary where the source date "30.09" became "by October 1" is rejected by condition a | G2a | — |
| `test_g2_invented_number_and_name` | an invented number in a summary violates condition a, an invented surname condition b | G2 | — |
| `test_g2_length_kind_language` | a summary longer than the sources, a non-episode source and a change of language violate conditions c, d and e | G2 | — |
| `test_proper_names_sentence_start` | A function word at the start of a sentence (a preposition) is not a name, but a surname at the start of a sentence is |  | — |

## `tests/unit/test_jobs_schedule.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_schedule_follows_mem_tz` | Release, operations: the sleep schedule follows the MEM_TZ time zone (UTC by default); deep sleep runs on Sunday |  | — |

## `tests/unit/test_lifecycle.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_initial_strength` | Initial strength depends on importance (0.35 / 0.55 / 0.75); the owner's words add 0.10 |  | — |
| `test_initial_cap` | Initial strength stays under the 0.90 ceiling even with importance 3 from the owner's words |  | — |
| `test_reinforce_saturates_and_never_exceeds_1` | Reinforcements only raise strength towards 1 and never let it exceed 1 |  | — |
| `test_decay_half_life` | One half-life (30 days) halves the strength exactly: 0.8 -> 0.4 |  | — |
| `test_decay_never_increases_property` | decay never raises strength and never takes it below zero | D-6 | — |
| `test_frozen_and_no_half_life` | Kinds without a half-life and frozen records do not decay |  | — |
| `test_forget_horizon_examples` | importance 2 without reinforcement drops below 0.08 after about N days | 05 §1, table of examples | — |
| `test_repeat_is_weaker_than_independent` | five repeats from one source raise strength less than one independent piece of evidence | M-S3 | — |

## `tests/unit/test_llm_routes.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_split_routes` | Sleep route: a name without a prefix is Anthropic only; cloud routes are refused (records would go out without masking); "openai:" only for models on the in-perimeter list; an unknown prefix is an error |  | — |
| `test_check_finds_bad_settings_before_first_call` | A sleep setup error shows before the first call: no Anthropic key, no router address, a cloud model |  | — |
| `test_openai_compat_call_and_errors` | A model inside the perimeter: a request with a tool call, the answer is checked against the schema; the server's response body never gets into the error text; network failures and 503 are retried, then it is an error; an invalid answer is a model error |  | — |
| `test_router_address_cannot_point_to_cloud_and_schema_is_plain` | The "in-perimeter" router address cannot be a cloud provider's address (Yandex, Sber, OpenAI, Anthropic and others), whatever model name is on the list; the tool schema for the router has no type lists |  | — |
| `test_routed_call_goes_to_its_provider` | The call goes to the client of its route with the model name without the prefix |  | — |

## `tests/unit/test_mcp_texts.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_languages_cover_the_same_texts` | English and Russian MCP descriptions have the same set of keys; the English ones have no Cyrillic | Release stage 2 | — |
| `test_tool_descriptions_follow_language` | MEM_MCP_LANG picks the language of tool descriptions and instructions; an unknown language means English | Release stage 2 | — |

## `tests/unit/test_pins.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_images_by_digest` | FROM in the Dockerfile and the pgvector image in Compose files and CI carry a digest, so a build tomorrow gets the same Python and the same Postgres | 05 | skipif |
| `test_pip_installs_only_hashed_locks` | the image and CI install libraries only from lock files and only with hash checking | 05 | skipif |
| `test_lock_matches_requirements` | every library from requirements*.txt is in the lock file at a version its specifier allows (a bump in requirements without `tools/lock.sh` fails here), and every pinned line has hashes | 05 | skipif |

## `tests/unit/test_policy.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_direct_grant_and_clearance_cap` | A personal contributor grant gives read and write, and the classification level is cut by the person's clearance |  | — |
| `test_group_grant_and_best_role` | Group and organization grants add up: the best role and the highest classification level win |  | — |
| `test_ceiling_limits_rights_and_namespaces` | The token ceiling cuts rights even for the owner, and a namespace outside the ceiling is closed entirely |  | — |
| `test_tenant_mismatch_expired_inactive_missing` | No access if the namespace belongs to another organization, the grant expired, the person is inactive or the namespace is missing |  | — |
| `test_local_admin_bypass` | The local administrator gets the admin right and level 3 without grants and in any organization |  | — |

## `tests/unit/test_safety.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_evidence_trusted` | the owner's words and the work of own clients (dev, session) are trusted, other chats and the web are not | 1.6.1 | — |
| `test_sanitize_invisible_bidi_tags` | invisible characters, bidi overrides and Unicode tags are cut out with the finding removed:invisible | 1.6.2 | — |
| `test_sanitize_ansi_and_control` | ANSI sequences and control characters are cut out with the findings removed:ansi and removed:control | 1.6.2 | — |
| `test_sanitize_mixed_script_flag_keeps_text` | a word mixing Cyrillic and Latin letters gets the mixed-script flag, and the text is not changed | 1.6.2 | — |
| `test_sanitize_encoded_readable` | a readable command hidden in base64 or hex gets the encoded flag | 1.6.2 | — |
| `test_sanitize_loyal_corpus` | honest texts (hashes, data URIs, code, mixed names, versions) pass without findings or changes | 1.6.2 | — |
| `test_find_secret` | Anthropic, AWS, GitHub, Slack, Telegram and JWT keys and private keys are recognized by their shape | 1.6.3 | — |
| `test_no_false_secret` | mentions of key prefixes, a path to keys and long numbers are not taken for a secret | 1.6.3 | — |
| `test_kin_forward_and_copypaste` | a forward, a copy-paste in another case and the same text with extra spaces count as related | 1.6.4 | — |
| `test_kin_distinct_texts` | a retelling in other words, short matches and an empty quote do not count as related | 1.6.4 | — |
| `test_independent_groups` | evidence with one key or a related text merges into one independent group | 1.6.4 | — |

## `tests/unit/test_search.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_weights_sum_to_one` | The weights of the ranking formula (meaning, words, strength, prior) sum to 1 |  | — |
| `test_deterministic_and_tiebreak` | result order does not depend on candidate order; with an equal score the newer record wins, then the smaller id | R-1 | — |
| `test_strength_breaks_equal_relevance` | with equal relevance the record with more strength, that is more confirmations, ranks higher | 04 | — |
| `test_lex_zero_when_no_match` | Without a word match lex is 0 even with high semantic similarity; the only match gives 1 |  | — |
| `test_explain_fields` | the score breakdown has id, total and the components sem, lex, strength, prior, and nothing else | R-2 | — |

## `tests/unit/test_test_catalog.py`

| Test | What it checks | Covers | Marks |
|---|---|---|---|
| `test_every_test_has_description` | Every test has a docstring saying what it checks | the test registry grows together with the code (CONTRIBUTING, the rule about tests) | — |
| `test_registry_is_up_to_date` | docs/tests.json and docs/tests.md match the test code: after a new test, run python3 tools/test_catalog.py | the test registry grows together with the code (CONTRIBUTING, the rule about tests) | — |

