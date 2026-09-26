from uuid import UUID

from lexinform_web.matters.models import Matter, PublicAlias


def canonical_matter(public_id: UUID) -> Matter:
    visited: set[UUID] = set()
    matter = Matter.objects.get(pk=public_id)
    while matter.canonical_id is not None:
        if matter.public_id in visited:
            raise ValueError("cycle in canonical matters")
        visited.add(matter.public_id)
        matter = Matter.objects.get(pk=matter.canonical_id)
    return matter


def matter_for_path(path: str) -> Matter:
    if path.startswith("/b/"):
        return canonical_matter(UUID(path.removeprefix("/b/").rstrip("/")))
    alias = PublicAlias.objects.get(path=path)
    return canonical_matter(alias.matter_id)
