"""Fixed transparent candidates. Tier3 deliberately unavailable."""
ALGORITHMS={0:'unavailable-v1',1:'eligible-run-rate-v1',2:'weekday-median-v1',3:'NOT_IMPLEMENTED'}

def qualify(quality,issue,policy):
    from .billing_contracts import decimal,instant
    from decimal import Decimal
    if not quality or instant(quality['known_at'])>issue:return False
    return (quality.get('origins',0)>=policy.min_folds and quality.get('months',0)>=policy.min_months
        and quality.get('wape') is not None and quality.get('bias') is not None
        and Decimal(quality['wape'])<=decimal(policy.max_wape) and abs(Decimal(quality['bias']))<=decimal(policy.max_bias)
        and quality.get('relative_improvement') is not None and Decimal(quality['relative_improvement'])>=decimal(policy.seasonal_improvement)
        and quality.get('same_folds') is True)
