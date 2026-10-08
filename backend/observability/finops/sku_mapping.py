"""Explicit effective-dated stable identifiers, never description matching."""
from dataclasses import dataclass
from datetime import datetime
from .billing_contracts import CATEGORIES, SourceError, code, instant

@dataclass(frozen=True)
class Mapping:
    service_id: str
    sku_id: str
    category: str
    effective_from: datetime
    effective_to: datetime

    def __post_init__(self):
        code(self.service_id); code(self.sku_id)
        if self.category not in CATEGORIES or instant(self.effective_from) >= instant(self.effective_to):
            raise SourceError('INVALID_MAPPING')

class Registry:
    def __init__(self, mappings=(), version='v1'):
        self.version = code(version, r'[A-Za-z0-9_.-]{1,32}')
        self.mappings = tuple(mappings)
        for i, a in enumerate(self.mappings):
            for b in self.mappings[i+1:]:
                if (a.service_id, a.sku_id) == (b.service_id, b.sku_id) and max(a.effective_from,b.effective_from) < min(a.effective_to,b.effective_to):
                    raise SourceError('AMBIGUOUS_MAPPING')

    def category(self, service, sku, at):
        for m in self.mappings:
            if (service,sku) == (m.service_id,m.sku_id) and m.effective_from <= at < m.effective_to:
                return m.category
        return 'UNMAPPED'
