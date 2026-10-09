"""Legacy knowledge diagnostic projection, never consulted by governed evidence."""
import math
import re
from hashlib import sha256

CONTAINERS=frozenset({'searches','selections','selected','command_authority','action_catalogs','action_resolutions',
    'operational_events','turn_requests','results','requested','accepted','excluded_families','actions',
    'unavailable_sources','skipped_candidates','technical_authority','approved_commands','clarification_continuity',
    'completeness_decision','command_egress','authorizations','rejected_authorizations','candidates'})
COUNTS=frozenset({'search_index','rank','limit','eligible_section_count','candidate_count','sparse_rank','dense_rank',
    'dropped_entries','removed_units','attempt','attempts','relevance_score','sparse_score','dense_similarity','fused_score'})
BOOLEANS=frozenset({'is_derived','explicit_in_current_message','tools_enabled','context_reused','forced_by_server',
    'governed_search_performed','selected_sources','command_presented','consumed_this_turn','pending_result_candidate'})
IDENTITIES=frozenset({'run_id','knowledge_id','version_label','section_id','source_id','procedure_action_id',
    'action_id','fault_id','authorization_run_id','candidate_step_id'})
ENUM_FIELDS=frozenset({'status','decision','stage','document_type','section_type','lifecycle_status',
    'applicability_outcome','selection_state','configured_mode','retrieval_mode','dense_status',
    'outcome','operation_type','command_authority','request_kind','focus','kind','phase','specialist','producer','final_producer'})
VALUES=frozenset({'success','error','accepted','explicit_empty','rejected_unavailable','invalid','authorized','rejected',
    'source','grounding','authorization','SELECTED','AVAILABLE','active','draft','deprecated','retired','applicable',
    'not_applicable','unresolved','sparse','dense','hybrid','disabled','available','unavailable','fallback','completed',
    'failed','timeout','cancelled','resolved','unresolved','not_evaluated','operational','diagnostic','read','write',
    'routing','specialist_invocation','route_enforcement','structured_output','structured_output_retry',
    'structured_output_recovery','plan','execution','approval','preview:source','preview:grounding','preview:authorization',
    'preview_authorized_if_target_confirmed','preview_rejected_if_target_confirmed',
    'team_manager','technical_authority_engineer','incident_manager','problem_manager','automated_operations_engineer'})
from .diagnostic_privacy_registry import VALUES as SERVER_VALUES
VALUES |= SERVER_VALUES | frozenset({'sanitized','passed','removed','kept','blocked','retry','validated',
    'not_started','pending','governed_completion','governed_completion+incident_manager',
    'governed_completion+technical_authority_engineer','governed_completion+team_manager'})
CONTAINERS |= frozenset({'pending_step','blocked_step','pending_before','pending_now','pending_after_binding',
    'binding','resolution','clarification','condition_check','final_acquisition','gaps','discovery',
    'identities','source','governed_conditions','targets','declared_targets','case_target_facts',
    'known_source_applicability','reconstruction','remediation','blocked_applicability',
    'recovery','troubleshooting_thread','structured_output'})
ENUM_FIELDS |= frozenset({'reason','rule','event','operation','resolution','instance_semantics','final_outcome',
    'progression_phase','turn_kind','progression_decision','authority_decision','policy_decision',
    'execution_mode','known_source_applicability','remediation','reconstruction','continuation_rule',
    'allowed','mode','final_kind','validation_error','recovery_status'})
BOOLEANS |= frozenset({'continued','passed','selected','governed_command_presented','known_clarification_open',
    'known_source_available_in_run','open_applicability_clarification','active_fault','route_required',
    'active_operational_investigation','new_objective','result_provided','clarification_answer','generic_continuation',
    'pending_result_candidate','pending_step_changed','source_gate_passed','grounding_passed','authorization_passed'})
IDENTITIES |= frozenset({'known_procedure_action_id','next_step_id','step_id','clarification_id',
    'final_requirement_id','display_id','case_id','active_fault_id','previous_fault_id'})

CONTAINERS |= frozenset({'blocked_action','model_proposal','remediation_gate','events','alternatives','offered','available','discovery','resumes','initial','final','pending_result','escalation','action','objective_change','retry','state','target_authority','selection_contract','procedure_action_resolution','criteria','requirements','diagnostic_evidence','target_isolated','governed_remediation','prerequisites','open_applicability_clarification','acquisition_gap','candidate_identity','parameters','relationships','duplicate','recheck'})
ENUM_FIELDS |= frozenset({'check','type','state_change_class','result_source','old_status','new_status','state','action','source','binding','discovery','lifecycle_stage','selected_route','final_response_kind','action_type','check_triggered','initial_action_id_status','final_action_id_status','retrieval_status','gate','failure_reason','classification'})
BOOLEANS |= frozenset({'offered','clarification_open','issued','explicit_negative_selection','met','consumed','check_triggered','had_command','accepted','exact_match','forced','invoked','required','changed','skipped','eligible','first_answer_usable','retry_eligible'})
COUNTS |= frozenset({'retry','applicability_dimension_count','dropped','search_count','selection_count','action_count'})
IDENTITIES |= frozenset({'acquisition_candidate_id','initial_action_id','returned_action_id','identity','requirement_id','candidate_id','known_result_step_id','chosen'})

IDENTITY_ARRAYS=frozenset({'selected_sources','previous_selected_sources','consumed_step_ids','fact_ids','offered','source_ids'})
CODE_ARRAYS=frozenset({'missing_dimensions','unresolved_applicability_dimensions','discarded_fields','unmet','reasons','rejected','dropped','citation_resolution'})
VALUES |= frozenset({'vendor','technology','domain','environment','software_version','hardware_model','node_type',
    'pending_step','open_applicability_clarification','evidence_acquisition','action_not_derivable_from_current_source',
    'citation_unresolved_no_selected_evidence','source_lifecycle_not_approved','source_applicability_not_match','source_not_action_authority','answered_partially','google:python_interpreter','command_not_grounded','source_not_current_authority','content_match','reconstructed','reconstructable','reconstructed_from_current_selected_evidence','governed_fail_closed','no_governed_procedure_selected','answer','operator_message','pending_step_superseded','pending_step_cancelled','ANY','AUTO','NONE','settled','alias','canonical','unsupported','citation_matches_selected_evidence','citation_unresolved_but_action_server_resolvable','action_resolution_deferred'})

BOOLEANS |= frozenset({'enforced'})
ENUM_FIELDS |= frozenset({'resulting_applicability'})
IDENTITY_ARRAYS |= frozenset({'released_requirements'})
VALUES |= frozenset({'restart','proceed','governed_post_action_verification','result_stale_after_reopen'})

def project(value,*,depth=0,identities=True):
    if depth>12 or type(value) is not dict: return {}
    safe={}
    for key,item in value.items():
        if key in IDENTITY_ARRAYS and type(item) in (list,tuple) and identities:
            safe[key]=[v if re.fullmatch('sha256:[0-9a-f]{64}',v) else 'sha256:'+sha256(v.encode()).hexdigest() for v in item[:50] if type(v) is str and len(v)<=256]
        elif key in CODE_ARRAYS and type(item) in (list,tuple):
            safe[key]=[v for v in item[:50] if type(v) is str and v in VALUES]
        elif item is None and key in (CONTAINERS | ENUM_FIELDS | BOOLEANS | COUNTS | IDENTITIES):
            safe[key]=None
        elif key in CONTAINERS and type(item) in (dict,list,tuple):
            if type(item) is dict: safe[key]=project(item,depth=depth+1,identities=identities)
            elif type(item) in (list,tuple):
                safe[key]=[project(v,depth=depth+1,identities=identities) for v in item[:50] if type(v) is dict]
        elif key in COUNTS and type(item) in (int,float) and math.isfinite(item) and 0<=item<=2**53-1:
            safe[key]=item
        elif key in BOOLEANS and type(item) is bool: safe[key]=item
        elif key in ENUM_FIELDS and type(item) is str and item in VALUES: safe[key]=item
        elif key in (IDENTITIES | {'source'}) and identities and type(item) is str and len(item)<=128:
            # Pseudonymous correlation; never raw source/user-selected strings.
            safe[key]=item if re.fullmatch('sha256:[0-9a-f]{64}',item) else 'sha256:'+sha256(item.encode()).hexdigest()
    return safe
