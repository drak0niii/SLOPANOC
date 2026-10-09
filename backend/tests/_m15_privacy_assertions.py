"""Independent test comparison for approved pseudonymous diagnostic identities."""
from hashlib import sha256
import re

def correlation(value):
    if value is None:return None
    if type(value) is not str:return value
    return value if re.fullmatch(r'sha256:[0-9a-f]{64}',value) else 'sha256:'+sha256(value.encode()).hexdigest()

def approved(turn,command=None):
    """Current-turn authority records remain the evidence for exact command checks."""
    records=turn.get('records',[])
    if not records and isinstance(turn.get('record'),dict):records=[turn['record']]
    values=[c for r in records for c in r.get('approved_commands_catalog',[])]
    return [c for c in values if command is None or c.get('command')==command]

def correlations(value):
    """Compare the exact structure while normalizing only correlation encoding."""
    if isinstance(value,str):return correlation(str(value))
    if type(value) is dict:return {correlations(k):correlations(v) for k,v in value.items()}
    if type(value) is list:return [correlations(v) for v in value]
    if type(value) is tuple:return tuple(correlations(v) for v in value)
    if type(value) is set:return {correlations(v) for v in value}
    return value
