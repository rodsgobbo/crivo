"""Mapa de cobertura: critério de aceitação -> testes que o verificam.

Este arquivo existe porque a rastreabilidade entre requisito e teste não pode
ser inferida do código. Nomes de teste descrevem comportamento e não carregam
identificadores de requisito, deliberadamente: um nome como
`test_REQ_13_10_clamp` envelhece mal e não diz o que o teste faz.

O mapa é julgamento humano, mas não fica só na afirmação. `test_coverage.py`
verifica mecanicamente que todo critério está mapeado e que todo teste citado
existe de fato — então o mapa não pode apodrecer em silêncio quando um teste é
renomeado ou removido.

Formato: "REQ-N.M": ["arquivo_de_teste::funcao", ...]
"""

COVERAGE: dict[str, list[str]] = {
    # ---------------------------------------------------- REQ-1 identidade
    "1.1": ["test_auth::test_entering_creates_a_session_for_a_new_user"],
    "1.2": [
        "test_auth::test_identity_comes_from_the_subject_not_the_email",
        "test_auth::test_a_reused_email_on_a_new_subject_is_a_different_user",
    ],
    "1.3": [
        "test_auth::test_a_cancelled_authorization_names_the_cause",
        "test_auth::test_a_return_without_a_code_is_refused",
    ],
    "1.4": ["test_auth::test_an_expired_session_stops_resolving"],
    "1.5": ["test_auth::test_an_unknown_or_absent_token_resolves_to_nothing"],
    "1.6": ["test_auth::test_logging_out_removes_only_the_session"],

    # ------------------------------------------------------ REQ-2 linkedin
    "2.1": ["test_linkedin::test_connecting_stores_identity_and_granted_scopes"],
    "2.2": ["test_linkedin::test_credential_material_is_refused_at_the_boundary"],
    "2.3": ["test_linkedin::test_connecting_stores_identity_and_granted_scopes"],
    "2.4": [
        "test_linkedin::test_fields_the_scopes_cannot_reach_are_marked_unavailable",
        "test_linkedin::test_a_narrower_consent_widens_the_unavailable_list",
    ],
    "2.5": ["test_linkedin::test_disconnecting_removes_the_data_and_revokes_the_token"],
    "2.6": ["test_linkedin::test_an_expired_connection_asks_for_reconnection"],

    # ---------------------------------------------------- REQ-3 importacao
    "3.1": ["test_resume_importer::test_a_drive_document_becomes_a_resume"],
    "3.2": ["test_resume_importer::test_a_word_file_becomes_a_resume"],
    "3.3": ["test_resume_importer::test_a_pdf_with_a_text_layer_becomes_a_resume"],
    "3.4": ["test_resume_importer::test_an_unaccepted_format_names_the_accepted_ones"],
    "3.5": ["test_resume_importer::test_a_file_above_the_size_limit_is_refused"],
    "3.6": [
        "test_resume_importer::test_a_document_without_a_text_layer_is_refused",
        "test_resume_importer::test_a_scanned_pdf_is_refused",
    ],
    "3.7": ["test_resume_importer::test_only_the_minimum_drive_scope_is_requested"],
    "3.8": [
        "test_resume_importer::test_the_resume_is_stored_under_the_user_who_imported_it"
    ],
    "3.9": ["test_resume_importer::test_the_daily_import_limit_is_enforced"],

    # ------------------------------------------------------- REQ-4 extracao
    "4.1": ["test_resume_parser::test_extraction_fills_the_closed_field_list"],
    "4.2": ["test_resume_parser::test_fields_outside_the_closed_list_are_discarded"],
    "4.3": ["test_resume_parser::test_absent_fields_become_gaps"],
    "4.4": ["test_resume_parser::test_identical_content_reuses_the_extraction"],
    "4.5": [
        "test_resume_parser::test_identical_content_reuses_the_extraction",
        "test_resume_parser::test_the_reused_extraction_keeps_the_original_provenance",
    ],
    "4.6": ["test_resume_parser::test_an_extraction_is_born_unconfirmed"],
    "4.7": [
        "test_resume_parser::test_a_correction_does_not_overwrite_the_extracted_value"
    ],
    "4.8": ["test_resume_parser::test_an_exhausted_chain_offers_manual_entry"],
    "4.9": ["test_resume_parser::test_extraction_fills_the_closed_field_list"],
    "4.10": ["test_resume_parser::test_the_provenance_is_recorded"],
    "4.11": ["test_resume_parser::test_confirming_opens_the_gate"],
    "4.12": [
        "test_resume_parser::test_without_a_credential_manual_entry_is_offered",
        "test_resume_parser::test_without_a_credential_no_request_is_emitted",
    ],

    # -------------------------------------------------------- REQ-5 perfil
    "5.1": ["test_profile::test_each_consolidation_creates_a_new_immutable_version"],
    "5.2": [
        "test_profile::test_the_resume_beats_the_linkedin_connection",
        "test_profile::test_a_manual_edit_beats_the_resume",
    ],
    "5.3": ["test_profile::test_the_resume_beats_the_linkedin_connection"],
    "5.4": ["test_profile::test_the_level_is_inferred_into_the_version"],
    "5.5": [
        "test_profile::test_a_profile_without_history_is_refused",
        "test_stages::test_a_run_without_a_profile_is_refused_by_name",
    ],
    "5.6": ["test_profile::test_each_consolidation_creates_a_new_immutable_version"],
    "5.7": ["test_profile::test_the_current_version_is_reused_while_no_source_changed"],
    "5.8": ["test_web_app::test_the_office_preference_is_saved_kept_and_clearable"],

    # ------------------------------------------------------- REQ-6 higiene
    "6.1": [
        "test_profile::test_two_current_roles_are_flagged",
        "test_profile::test_a_long_gap_is_flagged",
        "test_profile::test_an_overlap_is_flagged",
    ],
    "6.2": ["test_profile::test_every_problem_carries_field_and_snippet"],
    "6.3": ["test_profile::test_no_problems_yields_an_empty_list_not_a_silence"],

    # ---------------------------------------------------- REQ-7 planejador
    "7.1": ["test_planner::test_every_query_has_at_least_two_terms"],
    "7.2": ["test_planner::test_every_query_has_at_least_two_terms"],
    "7.3": ["test_planner::test_both_languages_are_covered"],
    "7.4": [
        "test_queue::test_finishing_records_the_counters",
        "test_stages::test_searches_come_from_the_profile_and_are_recorded",
    ],
    "7.5": ["test_planner::test_the_same_input_always_produces_the_same_list"],
    "7.6": [
        "test_stages::test_the_collection_survives_a_run_reclaimed_with_an_empty_context"
    ],
    "7.7": [
        "test_stages::test_the_collection_survives_a_run_reclaimed_with_an_empty_context",
        "test_stages::test_the_second_half_survives_the_loss_of_in_memory_context",
    ],

    # ------------------------------------------------------- REQ-8 coleta
    "8.1": ["test_collector::test_a_row_becomes_a_normalized_card"],
    "8.2": ["test_collector::test_a_repeated_identifier_within_the_run_is_discarded"],
    "8.3": ["test_collector::test_card_signals_are_recorded_on_the_card"],
    "8.5": ["test_collector::test_an_empty_result_is_unproductive_not_a_failure"],
    "8.6": [
        "test_collector::test_a_source_error_becomes_a_recorded_failure",
        "test_collector::test_a_failed_search_does_not_stop_the_remaining_ones",
    ],
    "8.7": ["test_collector::test_cards_are_stored_under_the_run_owner"],

    # --------------------------------------------------- REQ-9 pre-filtro
    "9.1": [
        "test_end_to_end::test_the_prefilter_runs_before_any_description_is_fetched",
        "test_stages::test_the_first_half_stops_at_enrichment",
    ],
    "9.2": ["test_prefilter::test_out_of_scope_titles_are_discarded"],
    "9.3": ["test_prefilter::test_the_discard_records_title_company_and_reason"],
    "9.4": [
        "test_prefilter::test_an_onsite_job_outside_the_radius_is_kept_with_a_blocker",
        "test_prefilter::test_the_blocker_names_the_configured_radius",
    ],
    "9.5": ["test_prefilter::test_a_remote_job_is_never_evaluated_geographically"],
    "9.6": [
        "test_prefilter::test_the_counts_before_and_after_are_reported",
        "test_queue::test_finishing_records_the_counters",
        "test_stages::test_only_survivors_are_scored_and_queued_for_description",
    ],

    # ------------------------------------------------ REQ-10 enriquecimento
    "10.1": ["test_enricher::test_a_surviving_job_gets_its_description_stored"],
    "10.2": [
        "test_enricher::test_a_description_that_cannot_be_obtained_marks_the_job",
        "test_enricher::test_the_run_continues_after_a_missing_description",
    ],
    "10.3": ["test_scoring::test_without_a_description_the_score_says_so"],
    "10.4": ["test_enricher::test_the_applicant_count_arrives_with_the_description"],
    "10.5": ["test_enricher::test_contact_emails_from_the_description_are_recorded"],
    "10.6": ["test_enricher::test_the_shared_registry_holds_one_row_per_job"],
    "10.7": ["test_enricher::test_a_stored_description_is_reused_without_a_request"],

    # -------------------------------------------------- REQ-11 contencao
    "11.1": ["test_governor::test_the_first_call_does_not_wait"],
    "11.2": [
        "test_governor::test_later_calls_wait_between_them",
        "test_governor::test_the_wait_comes_from_a_range_not_a_fixed_cadence",
    ],
    "11.3": ["test_governor::test_a_timeout_is_retried_with_a_growing_backoff"],
    "11.4": ["test_governor::test_three_consecutive_failures_record_a_block"],
    "11.5": [
        "test_governor::test_after_a_block_every_new_call_is_refused",
        "test_governor::test_a_refused_call_never_reaches_the_operation",
    ],
    "11.6": ["test_enricher::test_credential_of_a_user_never_reaches_the_collection"],

    # ------------------------------------------------------- REQ-12 cota
    "12.1": ["test_quota::test_the_budget_is_split_among_active_users"],
    "12.2": ["test_quota::test_spending_lowers_what_is_available"],
    "12.3": ["test_enricher::test_a_user_without_quota_is_refused_and_recorded"],
    "12.4": ["test_quota::test_consumption_per_user_is_reported"],
    "12.5": ["test_quota::test_a_new_day_starts_from_zero"],

    # ------------------------------------------------------ REQ-13 score
    "13.1": ["test_scoring::test_the_score_is_an_integer_between_zero_and_one_hundred"],
    "13.2": ["test_scoring::test_every_component_is_reported"],
    "13.3": ["test_scoring::test_synonyms_count_as_matches"],
    "13.4": ["test_scoring::test_a_bonus_signal_raises_the_score"],
    "13.5": ["test_scoring::test_a_penalty_signal_lowers_the_score"],
    "13.6": ["test_scoring::test_without_a_description_the_score_says_so"],
    "13.7": ["test_scoring::test_the_same_input_always_yields_the_same_score"],
    "13.8": ["test_lifecycle_and_scores::test_the_history_is_never_overwritten"],
    "13.9": ["test_scoring::test_an_unmet_eliminatory_requirement_lowers_the_score"],
    "13.10": [
        "test_scoring::test_extreme_bonuses_never_push_past_one_hundred",
        "test_scoring::test_extreme_penalties_never_push_below_zero",
    ],

    # ----------------------------------------------------- REQ-14 lacunas
    "14.1": ["test_scoring::test_gaps_are_what_the_job_asks_and_the_profile_lacks"],
    "14.2": ["test_scoring::test_differentials_are_the_overlap"],
    "14.3": ["test_scoring::test_gaps_and_differentials_never_intersect"],
    "14.4": ["test_scoring::test_frequency_ranks_the_most_asked_first"],
    "14.5": [
        "test_stages::test_a_skill_proven_by_the_history_is_not_a_gap",
        "test_scoring::test_a_skill_proven_by_the_history_is_not_reported_missing",
    ],
    "14.6": [
        "test_report::test_the_ranking_marks_what_the_profile_lacks",
        "test_report::test_without_a_profile_the_ranking_accuses_nothing",
    ],

    "13.11": [
        "test_stages::test_a_hybrid_job_stops_counting_as_remote",
        "test_stages::test_without_a_declared_preference_a_hybrid_job_is_left_alone",
        "test_presenca::test_the_number_of_office_days_is_read",
        "test_presenca::test_hybrid_without_a_number_says_hybrid_and_not_a_number",
    ],

    # ------------------------------------------- REQ-18.14 quem esta contratando
    "18.14": [
        "test_report::test_a_company_with_several_openings_is_listed",
        "test_report::test_an_opening_outside_the_window_is_not_hiring_today",
        "test_report::test_the_report_shows_who_is_hiring",
    ],

    # ------------------------------------------- REQ-18.15 nova ou ja vista
    "18.15": [
        "test_report::test_a_job_that_never_appeared_before_is_marked_new",
        "test_report::test_a_job_scored_in_an_earlier_run_is_marked_already_seen",
        "test_report::test_a_decided_job_shows_the_decision_instead_of_the_novelty",
    ],

    # ----------------------------------------------------- REQ-15 sintese
    "15.1": ["test_synthesis::test_one_logical_request_per_run"],
    "15.2": ["test_synthesis::test_only_the_closed_field_list_goes_up"],
    "15.3": ["test_synthesis::test_an_answer_without_classification_is_refused"],
    "15.4": ["test_synthesis::test_an_invented_url_is_refused"],
    "15.5": [
        "test_synthesis::test_a_missing_provenance_note_is_refused_when_a_job_lacks_description"
    ],
    "15.6": ["test_synthesis::test_an_exhausted_chain_records_a_synthesis_failure"],
    "15.7": ["test_synthesis::test_the_provenance_and_tokens_reach_the_run_record"],
    "15.8": ["test_synthesis::test_only_the_closed_field_list_goes_up"],
    "15.9": ["test_report::test_a_synthesis_failure_is_visible"],
    "15.10": ["test_synthesis::test_the_payload_is_limited_to_the_highest_scores"],
    "15.11": ["test_synthesis::test_the_provenance_and_tokens_reach_the_run_record"],
    "15.12": ["test_synthesis::test_the_provenance_and_tokens_reach_the_run_record"],

    # ----------------------------------------------------- REQ-16 injecao
    "16.1": ["test_model_client::test_external_text_is_wrapped_as_untrusted_data"],
    "16.2": [
        "test_model_client::test_the_system_instruction_comes_from_a_fixed_template",
        "test_model_client::test_the_builder_has_no_free_text_parameter",
    ],
    "16.3": ["test_model_client::test_external_text_is_truncated_at_the_limit"],
    "16.4": ["test_synthesis::test_an_invented_job_identifier_is_refused"],
    "16.5": ["test_resume_parser::test_the_resume_text_is_wrapped_as_untrusted_data"],

    # ------------------------------------------------ REQ-17 deterministico
    "17.1": ["test_end_to_end::test_the_deterministic_run_never_calls_the_model"],
    "17.2": ["test_end_to_end::test_a_deterministic_run_still_produces_a_report"],
    "17.3": ["test_report::test_deterministic_mode_is_announced"],

    # --------------------------------------------------- REQ-18 relatorio
    "18.1": ["test_report::test_each_job_shows_link_place_model_date_and_score"],
    "18.2": ["test_report::test_jobs_are_ordered_by_score_descending"],
    "18.3": ["test_report::test_each_job_shows_link_place_model_date_and_score"],
    "18.4": ["test_report::test_the_full_description_is_shown_when_available"],
    "18.5": ["test_report::test_contacts_blockers_and_gaps_are_shown"],
    "18.6": ["test_report::test_contacts_blockers_and_gaps_are_shown"],
    "18.7": ["test_report::test_discarded_jobs_are_listed_with_their_reason"],
    "18.8": [
        "test_report::test_hostile_text_from_the_source_appears_escaped",
        "test_report::test_the_template_never_disables_autoescaping",
    ],
    "18.9": ["test_report::test_hygiene_problems_are_shown"],
    "18.10": ["test_report::test_the_skill_ranking_is_shown"],
    "18.11": ["test_report::test_a_run_with_no_survivors_says_so_with_the_counts"],
    "18.12": [
        "test_report::test_a_new_job_above_the_threshold_is_highlighted",
        "test_report::test_a_job_an_earlier_run_already_showed_is_not_highlighted",
        "test_report::test_an_already_seen_job_is_not_highlighted",
    ],
    "18.13": ["test_report::test_jobs_left_without_description_by_quota_are_reported"],

    # -------------------------------------------------- REQ-19 isolamento
    "19.1": ["test_repository::test_insert_fills_the_user_identifier_without_being_asked"],
    "19.2": [
        "test_repository::test_a_query_never_returns_another_users_rows",
        "test_repository::test_an_explicit_where_clause_cannot_widen_the_scope",
    ],
    "19.3": ["test_repository::test_a_refused_cross_user_attempt_is_recorded"],
    "19.4": ["test_schema::test_shared_description_table_has_no_user_identifier"],

    # ------------------------------------------------------- REQ-20 LGPD
    "20.1": ["test_scheduler_and_privacy::test_deleting_an_account_removes_every_user_row"],
    "20.2": ["test_scheduler_and_privacy::test_the_export_carries_the_subject_data"],
    "20.3": [
        "test_scheduler_and_privacy::test_the_disclosure_names_what_is_kept_and_for_how_long"
    ],
    "20.4": [
        "test_scheduler_and_privacy::test_an_inactive_user_has_resume_and_extraction_purged"
    ],
    "20.5": [
        "test_scheduler_and_privacy::test_the_proof_of_deletion_survives_the_deletion",
        "test_scheduler_and_privacy::test_the_export_is_recorded",
    ],
    "20.6": [
        "test_scheduler_and_privacy::test_deleting_an_account_preserves_the_shared_description"
    ],
    "20.7": ["test_credential_vault::test_the_disclosure_names_the_destination_and_the_limit"],
    "20.8": [
        "test_lifecycle_and_scores::test_expiring_a_job_forgets_the_recruiter_contacts",
        "test_lifecycle_and_scores::test_contacts_survive_while_another_user_still_has_the_job",
    ],

    # -------------------------------------------------- REQ-21 ciclo de vida
    "21.1": ["test_collector::test_cards_are_stored_under_the_run_owner"],
    "21.2": [
        "test_collector::test_a_job_seen_again_is_not_new_and_keeps_its_first_sighting"
    ],
    "21.3": ["test_lifecycle_and_scores::test_the_user_choice_changes_the_state"],
    "21.4": [
        "test_lifecycle_and_scores::test_three_eligible_absences_expire_the_job",
        "test_lifecycle_and_scores::test_a_job_outside_the_run_window_never_accumulates_absence",
    ],
    "21.5": ["test_queue::test_finishing_records_the_counters"],
    "21.6": ["test_repository::test_a_failed_write_names_the_operation_and_the_cause"],

    # -------------------------------------------------- REQ-22 agendamento
    "22.1": ["test_scheduler_and_privacy::test_the_recurring_window_is_the_incremental_one"],
    "22.2": [
        "test_scheduler_and_privacy::test_a_user_with_an_active_run_is_refused_and_recorded"
    ],
    "22.3": ["test_scheduler_and_privacy::test_a_block_postpones_the_cycle_for_everyone"],
    "22.4": ["test_scheduler_and_privacy::test_a_stalled_run_is_released_by_the_cycle"],
    "22.5": [
        "test_scheduler_and_privacy::test_who_never_ran_comes_first",
        "test_scheduler_and_privacy::test_who_waited_longest_comes_before_who_ran_recently",
    ],
    "22.6": ["test_scheduler_and_privacy::test_an_immediate_run_uses_the_wide_window"],
    "22.7": [
        "test_scheduler_and_privacy::test_the_daily_limit_of_immediate_runs_is_enforced"
    ],
    "22.8": [
        "test_runner::test_a_failing_stage_records_the_reason_in_the_run",
        "test_runner::test_a_broken_stage_ends_the_run_and_not_the_worker",
        "test_stages::test_a_run_without_a_profile_is_refused_by_name",
    ],

    # ------------------------------------------------------ REQ-23 segredos
    "23.1": ["test_secrets_vault::test_reads_a_secret_from_the_environment"],
    "23.2": [
        "test_logging_filters::test_removes_a_value_the_vault_has_delivered",
        "test_logging_filters::test_removes_authorization_and_cookie_headers",
    ],
    "23.3": ["test_report::test_the_report_never_shows_secret_material"],
    "23.4": ["test_credential_vault::test_the_stored_key_is_never_in_clear"],

    # --------------------------------------------------- REQ-24 configuracao
    "24.1": ["test_config::test_reads_every_configured_section_from_the_default_file"],
    "24.2": ["test_config_validation::test_wrong_type_names_the_key_and_expected_type"],
    "24.3": [
        "test_config_validation::test_absent_key_names_the_key_and_expected_type",
        "test_config_validation::test_value_above_range_names_the_bound",
    ],
    "24.4": ["test_queue::test_the_effective_configuration_is_stored_with_the_run"],

    # --------------------------------------------------- REQ-25 provedores
    "25.1": ["test_provider_registry::test_the_shipped_registry_loads"],
    "25.2": ["test_provider_registry::test_a_missing_field_names_it"],
    "25.3": [
        "test_provider_registry::test_a_provider_needing_an_application_is_refused"
    ],
    "25.4": ["test_provider_registry::test_an_unknown_execution_place_is_refused"],
    "25.5": [
        "test_provider_registry::test_a_provider_that_does_not_answer_leaves_the_offer"
    ],
    "25.6": ["test_provider_registry::test_a_local_provider_needs_no_credential"],
    "25.7": [
        "test_provider_registry::test_the_offer_tells_the_user_the_limit_and_the_destination"
    ],

    # -------------------------------------------------- REQ-26 credenciais
    "26.1": ["test_credential_vault::test_a_credential_is_validated_before_being_stored"],
    "26.2": ["test_credential_vault::test_a_provider_error_surfaces_the_cause"],
    "26.3": ["test_credential_vault::test_the_stored_key_is_never_in_clear"],
    "26.4": ["test_credential_vault::test_listing_never_exposes_the_value"],
    "26.5": ["test_credential_vault::test_removing_a_credential_takes_it_out_of_the_chain"],
    "26.6": ["test_credential_vault::test_the_first_store_is_detectable_for_the_prior_notice"],
    "26.7": ["test_model_client::test_a_user_without_credentials_falls_back_to_deterministic"],
    "26.8": ["test_model_client::test_the_chain_follows_the_user_order"],
    "26.9": ["test_model_client::test_an_exhausted_chain_falls_back_to_deterministic"],
    "26.10": [
        "test_model_client::test_one_users_chain_never_contains_anothers_credential",
        "test_model_client::test_using_the_client_for_another_users_work_is_refused",
    ],
    "26.11": ["test_credential_vault::test_the_user_can_reorder_the_chain"],
    "26.12": [
        "test_credential_vault::test_the_first_credential_is_seeded_from_the_operator_default"
    ],

    # --------------------------------------------- REQ-27 insights da extensao
    "27.1": ["test_web_app::test_insights_from_the_browser_are_recorded"],
    "27.2": ["test_web_app::test_an_unknown_signal_is_dropped_instead_of_scored"],
    "27.3": ["test_web_app::test_signals_from_the_browser_join_the_ones_from_collection"],
    "27.4": ["test_web_app::test_an_envelope_with_nothing_recognizable_is_refused"],
    "27.5": [
        "test_web_app::test_a_sweep_collects_the_job_the_crivo_never_found",
        "test_web_app::test_the_applicant_count_of_a_brand_new_job_is_not_lost",
        "test_web_app::test_a_known_job_is_not_recreated_by_a_sweep",
    ],
    "27.6": ["test_web_app::test_a_card_without_title_or_url_is_still_refused"],
    "27.7": [
        "test_web_app::test_a_sweep_records_every_card_at_once",
        "test_web_app::test_an_uncollected_card_is_skipped_instead_of_failing_the_sweep",
    ],
    "27.8": ["test_web_app::test_an_empty_or_oversized_sweep_is_refused"],
    "27.9": [
        "test_web_app::test_insights_for_a_job_of_another_user_are_refused",
        "test_web_app::test_a_sweep_of_another_users_jobs_records_nothing",
    ],
    "27.10": [
        "test_web_app::test_insights_require_a_session",
        "test_web_app::test_a_sweep_requires_a_session",
        "test_web_app::test_a_wrong_token_is_refused",
    ],
    "27.11": [
        "test_web_app::test_a_sweep_authenticates_by_header_without_any_cookie",
        "test_web_app::test_the_preflight_announces_the_token_header",
    ],
    "27.12": ["test_web_app::test_the_extractor_token_opens_nothing_but_the_insight_routes"],
    "27.13": ["test_web_app::test_issuing_a_token_invalidates_the_previous_one"],
    "27.14": [
        "test_web_app::test_the_extension_page_issues_a_token_only_by_post",
        "test_web_app::test_the_extension_page_requires_a_session",
    ],
    "27.15": [
        "test_web_app::test_only_the_extractor_origin_may_reach_the_insights_route",
        "test_web_app::test_the_sweep_route_carries_the_same_origin_allowance",
        "test_web_app::test_the_broad_origin_is_never_allowed",
        "test_web_app::test_other_routes_do_not_carry_the_origin_allowance",
    ],
    "27.16": ["test_stages::test_a_top_applicant_goes_first_in_the_enrichment_queue"],
    "27.17": [
        "test_report::test_being_a_top_applicant_sends_you_straight_to_the_application",
        "test_report::test_a_top_applicant_outranks_a_crowded_queue",
    ],
    "27.18": ["test_report::test_a_top_applicant_does_not_override_a_mandatory_gap"],

    # ----------------------------------------------- REQ-28 releitura do topo
    "28.1": [
        "test_judge::test_only_the_top_reaches_the_model",
        "test_judge::test_the_request_names_the_profile_and_every_job",
        "test_judge::test_the_job_id_carries_no_brackets",
    ],
    "28.2": [
        "test_judge::test_the_request_uses_the_judgement_task_and_not_the_synthesis_one",
        "test_judge::test_the_judgement_task_has_a_system_prompt",
    ],
    "28.3": [
        "test_judge::test_the_whole_request_fits_the_budget",
        "test_judge::test_every_job_gets_the_same_slice_of_description",
    ],
    "28.4": [
        "test_judge::test_a_job_without_a_description_says_so_instead_of_omitting",
        "test_judge::test_a_list_too_long_for_any_description_says_so",
    ],
    "28.5": [
        "test_judge::test_a_well_formed_answer_becomes_scores",
        "test_judge::test_the_shape_a_real_model_actually_returned_is_read",
        "test_judge::test_a_markdown_table_row_is_read",
        "test_judge::test_a_bulleted_line_is_read",
        "test_judge::test_prose_around_the_lines_is_skipped",
    ],
    "28.6": [
        "test_judge::test_an_invented_job_is_ignored",
        "test_judge::test_the_identifier_is_never_loosened",
        "test_judge::test_a_score_outside_the_range_is_refused",
    ],
    "28.7": [
        "test_judge::test_without_a_model_nothing_happens_and_the_run_survives",
        "test_report::test_without_any_reread_the_deterministic_order_is_untouched",
    ],
    "28.8": [
        "test_judge::test_an_exhausted_chain_is_a_recorded_failure_not_a_crash",
        "test_judge::test_a_model_error_is_a_recorded_failure_not_a_crash",
        "test_judge::test_an_unreadable_answer_does_not_invent_an_order",
    ],
    "28.9": ["test_report::test_the_report_shows_where_the_score_came_from"],
    "28.10": [
        "test_report::test_the_model_score_decides_the_order_where_it_exists",
        "test_report::test_a_reread_job_comes_before_one_never_considered",
        "test_report::test_the_description_rule_survives_below_the_reread_tier",
    ],
}
