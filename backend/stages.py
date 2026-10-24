"""Which documents exist at which point of the stay (refinement of phases 6 and 9).

A pre-auth is sent at admission, so it can only use what exists then: the admission note, the
investigations and any prescription. The operative note, discharge summary and final bill are written
later; using them at pre-auth would be using information from the future. Both sides read this list,
so neither the hospital nor the simulated insurer expects a later document too early.
"""

from backend.schemas import Document, DocumentType, Stage

ADMISSION_TIME = frozenset({DocumentType.ADMISSION_NOTE, DocumentType.LAB_REPORT, DocumentType.XRAY_REPORT, DocumentType.PRESCRIPTION})
LATER = frozenset(set(DocumentType) - ADMISSION_TIME)


def due_at(stage: Stage) -> frozenset[DocumentType]:
    """Document types that can exist when a message for this stage is prepared."""
    return frozenset(DocumentType) if stage == Stage.DISCHARGE else ADMISSION_TIME


def available(documents: list[Document], stage: Stage) -> list[Document]:
    due = due_at(stage)
    return [doc for doc in documents if doc.type in due]
