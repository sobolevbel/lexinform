---
name: "source-command-product-review"
description: "Product-аудит lexinform — ищет продуктовые баги в отображении стадий, сообщениях и отслеживании законопроектов"
---

# source-command-product-review

Use for the migrated `product-review` command. Audit reader-facing behaviour; do not change code.

Read `docs/legislative-process.md`, `docs/roadmap.md`, `AGENTS.md`, and the relevant rendering,
stage, deadline, consultation, and agenda code. Use real examples from all three sources (Sejm,
RCL, wykaz); restore a copy of the `state` dump and use a dry run only when it improves evidence.

Check that every card and update makes the current stage, completed event, next step, and current
reader action clear. Compare API semantics with rendered wording, especially third-reading `End`,
`closureDate`, promulgation versus entry into force, consultation windows, joint/urgent bills,
vetoes, agenda moves, and term rollover. Check successive messages, tags, and operator replies
for contradictions or missed actions.

Report findings by reader impact. Each needs a concrete scenario or message, code location, why it
misleads a reader, and a proposed correction. Finish with a prioritised, estimated fix plan.
