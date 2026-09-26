# lexinform-web instructions

These instructions apply only to `web/`. Root `AGENTS.md` and `CONTRIBUTING.md` still apply.
Do not repeat their general repository rules here.

For website work, `docs/website/` contains the implementation specification and task list.
Use `docs/plans/website-implementation-progress.md` for verified completion status.
When section 25 refines or contradicts an earlier part of the plan, section 25 wins.
Read the relevant `WEB-*` item and the section it points to before changing code.

If the implementation shows that an agreed design is wrong or impossible, do not silently replace it
with a different architecture. Explain the concrete conflict and keep the change as narrow as possible.

## Persistence boundaries

Django ORM is a valid persistence API for the web application. A repository is also valid when it
creates a useful boundary. Choose the shape that makes the responsibility clearer for the concrete
case.

- Use selectors for reusable read-side queries and page/use-case query composition.
- Use custom `QuerySet` or manager methods for composable model-local query behaviour.
- Use repositories when they own a meaningful persistence boundary, such as aggregate persistence,
  coordinated writes across models, import-generation state, durable delivery/task state, or an
  existing port that deliberately isolates persistence.
- Services may depend on repositories when that keeps persistence concerns contained or makes a
  business operation easier to reason about and test.
- Keep ORM objects and `QuerySet`s inside the persistence-aware part of the code when the caller is
  intended to be persistence-agnostic.
- Put a transaction boundary in one obvious place. Usually the service owns the business transaction;
  a repository may own it when the repository operation itself is the complete atomic unit.
- Keep network and LLM calls outside database transactions unless a specific protocol makes that
  unavoidable.

Do not create repositories mechanically:

- no repository per model by convention;
- no `BaseRepository[T]` or generic CRUD repository that mirrors Django ORM;
- no pass-through `get/create/update/delete` layer whose only purpose is hiding `.objects`, `save()`
  or `delete()`;
- repository methods should describe persistence operations meaningful to the use case or domain.

## Abstraction discipline

Prefer ordinary Python and Django/Wagtail mechanisms until a real boundary appears.

- Do not create `BaseService`, generic CRUD services, factories, registries, DTO mirrors of every
  model, or interface modules by default.
- Introduce a Protocol/interface when it isolates an external system or persistence boundary, has
  genuinely different implementations, or is useful as a stable seam in meaningful tests.
- Do not introduce an abstraction solely for a hypothetical future provider or framework swap.
- A small amount of straightforward duplication is preferable to a generic abstraction whose stable
  shape is still unknown.
- Do not move domain behaviour into framework magic to reduce visible code. Explicit calls are easier
  to trace and test.

## ORM behaviour

- Avoid hidden database access in model properties, `__str__`, template helpers, and other methods
  that look computationally cheap.
- Treat unexpected query growth as a correctness problem on list, search, card, and admin views.
- Let PostgreSQL enforce invariants that it can express reliably with constraints and indexes.
- Keep multi-model write invariants explicit in the service/repository operation that owns them.

## Implementation choices

When the specification leaves a local implementation detail open:

1. inspect adjacent web code and tests;
2. follow an existing project convention when it fits;
3. choose the simplest reversible design that satisfies the current requirement;
4. avoid expanding the task into unrelated refactoring.

Do not weaken typing, tests, validation, permissions, migration safety, or operational checks to make
an implementation easier. If a framework limitation requires a workaround, keep it narrow and make
the reason visible in the code or test that protects it.
