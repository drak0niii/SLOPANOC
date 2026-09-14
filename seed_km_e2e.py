import asyncio

from sqlalchemy.engine import make_url

from backend.config.settings import get_settings
from backend.knowledge.domain.enums import (
    KnowledgeDocumentType,
    LifecycleStatus,
)
from backend.knowledge.domain.models import (
    Applicability,
    KnowledgeMetadata,
    KnowledgeObject,
    KnowledgeSection,
    KnowledgeSource,
    KnowledgeVersion,
)
from backend.knowledge.repository.sqlalchemy import (
    SqlAlchemyKnowledgeRepository,
)


async def main():
    database_url = get_settings().resolve_knowledge_database_url()
    parsed = make_url(database_url)

    if parsed.get_backend_name() != "postgresql":
        raise RuntimeError(
            "REFUSING TO SEED: governed Knowledge is not configured for PostgreSQL/Cloud SQL."
        )

    print("Knowledge backend:", parsed.get_backend_name())
    print("Knowledge driver:", parsed.get_driver_name())
    print("Knowledge database:", parsed.database)
    print("Knowledge host:", parsed.host)
    print("Knowledge port:", parsed.port)

    repo = SqlAlchemyKnowledgeRepository(database_url)

    knowledge_id = "E2E-KM-AURORA-001"
    version_label = "v1"

    try:
        await repo.ensure_schema()

        existing = await repo.get(knowledge_id, version_label)

        if existing is not None:
            print("")
            print("TEST FIXTURE ALREADY EXISTS")
            print("Knowledge ID:", knowledge_id)
            print("Version:", version_label)
            print("No changes made.")
            return

        knowledge = KnowledgeObject(
            knowledge_id=knowledge_id,
            document_type=KnowledgeDocumentType.TECHNICAL_INSTRUCTION,
            title="Aurora Relay Verification Procedure",
            version=KnowledgeVersion(
                label=version_label,
                revision="1",
                effective_from=None,
                effective_to=None,
                supersedes=[],
                superseded_by=[],
            ),
            lifecycle_status=LifecycleStatus.APPROVED,
            source=KnowledgeSource(
                source_system="manual_e2e_fixture",
                source_id="aurora-relay-verification",
                source_uri="https://example.invalid/DO-NOT-EXPOSE-AURORA-E2E",
                display_name="Aurora Relay Governed Test Procedure",
            ),
            metadata=KnowledgeMetadata(
                owner="SLOPANOC E2E Test",
                classification="test",
                tags=[
                    "Aurora",
                    "relay",
                    "verification",
                    "checksum",
                ],
                attributes={
                    "fixture": "phase-5.1j-e2e",
                },
            ),
            applicability=Applicability(
                dimensions={},
                notes="Controlled SLOPANOC end-to-end test fixture.",
            ),
            sections=[
                KnowledgeSection(
                    section_id="17:E2E-KM-AURORA-0012:v112:section-0000",
                    knowledge_id=knowledge_id,
                    heading="Verification",
                    section_type="verification",
                    sequence=0,
                    content=(
                        "For the Aurora Relay verification, confirm that "
                        "the relay checksum is exactly 7319 and the status "
                        "indicator is GREEN. If either value differs, "
                        "verification is not successful."
                    ),
                    source_locator="test-fixture:verification",
                ),
                KnowledgeSection(
                    section_id="17:E2E-KM-AURORA-0012:v112:section-0001",
                    knowledge_id=knowledge_id,
                    heading="Escalation",
                    section_type="escalation",
                    sequence=1,
                    content=(
                        "If the Aurora Relay verification does not meet "
                        "the expected values, collect the observed checksum "
                        "and status and escalate to the platform owner. "
                        "Do not restart or reconfigure the relay as part "
                        "of this verification."
                    ),
                    source_locator="test-fixture:escalation",
                ),
            ],
        )

        await repo.add(knowledge)

        stored = await repo.get(knowledge_id, version_label)

        assert stored is not None
        assert stored == knowledge

        print("")
        print("==============================================")
        print("SLOPANOC CLOUD SQL KM FIXTURE CREATED")
        print("==============================================")
        print("Knowledge ID:", stored.knowledge_id)
        print("Version:", stored.version.label)
        print("Lifecycle:", stored.lifecycle_status.value)
        print("Title:", stored.title)
        print("Sections:", len(stored.sections))
        print("")
        print("Cloud SQL governed-KM seed completed successfully.")

    finally:
        await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
