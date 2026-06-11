"""Compatibility facade for BinderBridge view helpers.

Feature view functions live in smaller modules. The app facade injects shared
helpers/constants into this module and its child modules at import time, so the
legacy app.py public API remains compatible during the split.
"""

from binderbridge import (
    components,
    collection_queries,
    matchmaking_queries,
    trade_queries,
    views_shell,
    views_admin,
    views_dashboard,
    views_groups,
    views_collection,
    views_wants,
    views_members,
    views_trades,
    want_queries,
)

_VIEW_MODULES = (
    components,
    collection_queries,
    want_queries,
    matchmaking_queries,
    trade_queries,
    views_shell,
    views_admin,
    views_dashboard,
    views_groups,
    views_collection,
    views_wants,
    views_members,
    views_trades,
)
__binderbridge_feature_modules__ = _VIEW_MODULES

for _module in _VIEW_MODULES:
    globals().update(
        {
            name: value
            for name, value in _module.__dict__.items()
            if not name.startswith("_")
        }
    )

__all__ = ['render_subnav', 'render_cards_subnav', 'render_wishlist_subnav', 'group_listing_url', 'render_layout', 'render_login', 'render_two_factor_login', 'render_register', 'render_recovery_code_panel', 'render_two_factor_account_panel', 'render_passkey_account_panel', 'render_account', 'render_admin', 'health_time_label', 'health_status_class', 'render_health_status_counts', 'render_failed_notification_row', 'render_admin_health', 'admin_job_user_label', 'admin_job_time_label', 'admin_job_status_label', 'admin_job_retry_form', 'admin_job_import_target', 'admin_job_import_row', 'admin_job_scryfall_row', 'admin_job_price_row', 'admin_job_notification_row', 'render_admin_jobs', 'admin_audit_log_display_user', 'admin_audit_log_target_label', 'admin_audit_log_time_label', 'render_admin_audit_log_item', 'render_admin_audit_log_table_row', 'trade_dispute_user_label', 'render_trade_dispute_summary_item', 'render_trade_dispute_admin_row', 'render_admin_trade_disputes', 'render_admin_logs', 'render_registration_invite_row', 'render_admin_user_row', 'render_dashboard', 'notification_kind_label', 'render_notifications', 'group_count_label', 'record_is_public', 'visibility_label', 'visibility_badge', 'visibility_checkbox', 'render_group_card', 'normalize_group_view', 'render_groups', 'collection_item_option_tags', 'want_item_option_tags', 'render_group_collection_items', 'render_group_want_items', 'render_group_import_result', 'render_import_warning_block', 'render_import_preview_rows', 'render_import_batch_list', 'render_collection_import_preview', 'render_deck_import_preview', 'render_deck_missing_wishlist_prompt', 'render_deck_import_review', 'render_deck_import_panel', 'render_group_detail', 'public_member_group_rows', 'render_public_group_collection_items', 'render_public_group_want_items', 'render_public_group_detail', 'query_value', 'query_nonnegative_int', 'collection_filters', 'collection_filter_values', 'collection_has_advanced_filters', 'collection_hidden_filter_inputs', 'CARD_SORT_OPTIONS', 'WANT_SORT_OPTIONS', 'GROUP_COLLECTION_SORT_SQL', 'GROUP_WANT_SORT_SQL', 'sort_state', 'sort_order_clause', 'render_sort_controls', 'render_sort_bar', 'collection_where', 'query_int', 'pagination_state', 'page_url', 'current_collection_url', 'render_pagination', 'pagination_hidden_inputs', 'render_cleanup_group_items', 'render_duplicate_cleanup_panel', 'render_cleanup', 'render_audit_issue_badges', 'render_audit_value', 'render_condition_finish_audit_row', 'render_condition_finish_audit', 'render_collection', 'stat_percent_text', 'render_stat_breakdown', 'render_stat_coverage_row', 'render_collection_top_value', 'render_group_count_summary', 'render_collection_statistics', 'browse_filters', 'browse_filter_values', 'browse_has_advanced_filters', 'browse_where', 'browse_filter_users', 'TRADE_PICKER_FILTER_KEYS', 'trade_picker_filter_values', 'trade_picker_has_advanced_filters', 'trade_picker_where', 'trade_picker_pagination_state', 'trade_picker_url', 'trade_picker_preserved_inputs', 'trade_picker_datalists', 'render_trade_picker_pagination', 'render_browse', 'render_browse_row', 'render_collection_row', 'render_price_history_panel', 'render_collection_form', 'render_import', 'default_want_item', 'validate_want_form', 'insert_want_item', 'update_want_item', 'insert_selected_want_items', 'want_trade_matches', 'render_want_card', 'render_wants', 'match_entry_quantity', 'trade_match_entry_from_row', 'add_trade_match_entry', 'sorted_trade_match_entries', 'finalize_trade_match', 'trade_matchmaking_candidate_rows', 'trade_matchmaking_results', 'trade_matchmaking_prefill_url', 'render_trade_match_card_list', 'render_trade_match_card', 'render_trade_matchmaking', 'render_members', 'feedback_rating_label', 'feedback_count_label', 'render_reputation_summary', 'public_profile_stats', 'render_public_trade_card', 'render_public_want_profile_item', 'render_public_group_profile_card', 'render_member_detail', 'render_trades', 'render_trade_table', 'trade_picker_query_inputs', 'trade_quantity_map', 'trade_selected_quantities_from_form', 'trade_price_basis_for', 'price_for_item_basis', 'apply_trade_price_basis', 'trade_selected_items', 'trade_item_price_usd', 'trade_item_price_source', 'trade_entry_value_cents', 'trade_entries_value_cents', 'trade_entries_unpriced_count', 'trade_value_gap_percent', 'trade_balance_details', 'trade_fairness_assessment', 'render_trade_fairness_notice', 'validate_trade_fairness_for_send', 'validate_trade_fairness_for_creation', 'render_trade_value_panel', 'render_trade_selected_hidden_inputs', 'render_trade_selection_list', 'render_trade_live_summary', 'render_counter_context_panel', 'render_trade_review', 'render_trade_picker_section', 'render_new_trade', 'trade_picker_table', 'render_trade_links', 'render_trade_comments', 'render_trade_feedback', 'render_trade_disputes', 'render_trade_detail', 'render_trade_items']
__all__.extend(['render_group_privacy_panel', 'render_shared_group'])

